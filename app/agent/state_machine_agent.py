"""状态机提取（纯规则 + 源码证据）。

流程（TASK_BOOK 6.1.2）：
1. 找状态枚举 → states
2. 找 setStatus(X) / setXxxStatus(X) 写入点
3. 向上找方法边界，由方法名推断 trigger
4. 方法内向上找状态守卫（含状态字面量的 if），推导 from：
   - 否定式 if(!X) throw → 来源态 = {X}
   - 肯定式 if(X) throw → 来源态 = 全集 − X
   - 无守卫 → 来源态 = 全集 − 目标态
5. verified=true 的唯一判据：找到写状态的那行代码（setStatus）
6. BFS 计算最短推进路径（供生成流转用例的前置步骤）
"""
from __future__ import annotations

import os
import re
from collections import deque
from dataclasses import dataclass, field

from app.analyzer.models import BackendResult

# setStatus(X) / setXxxStatus(X)，目标态取 X（可为 OrderStatus.PAID）
SET_STATUS_RE = re.compile(r"\.?\s*set\w*Status\s*\(\s*([^)]*?)\s*\)")
METHOD_SIG_RE = re.compile(r"\b(public|private|protected)\s+[A-Za-z_<>,\.\[\]\s]+\s+(\w+)\s*\(")
IF_RE = re.compile(r"\bif\s*\(([^{}]*)\)")
THROW_RE = re.compile(r"\bthrow\b")
GUARD_EXIT_RE = re.compile(r"\b(throw|continue|return|break)\b")
# 状态字面量："CREATED" 或 OrderStatus.CREATED
STATUS_LITERAL_RE = re.compile(r'"([A-Z][A-Z0-9_]*)"|\b(?:[A-Z]\w+)?(?:Status|State)\s*\.\s*([A-Z][A-Z0-9_]*)')

TRIGGER_MAP = {
    "pay": "支付", "ship": "发货", "deliver": "发货", "complete": "完成",
    "cancel": "取消", "create": "创建", "submit": "提交", "update": "更新",
    "delete": "删除", "remove": "删除", "approve": "审批通过", "reject": "驳回",
    "refund": "退款", "receive": "确认收货", "confirm": "确认", "start": "开始",
    "close": "关闭", "finish": "完成", "send": "发送",
}


@dataclass
class Transition:
    id: str
    from_: str
    to: str
    trigger: str
    verified: bool
    evidence: list[str] = field(default_factory=list)
    guard: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "from": self.from_,
            "to": self.to,
            "trigger": self.trigger,
            "verified": self.verified,
            "evidence": self.evidence,
            "guard": self.guard,
        }


@dataclass
class StateMachineResult:
    name: str
    states: list[dict] = field(default_factory=list)
    transitions: list[Transition] = field(default_factory=list)
    confidence: float = 1.0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "states": self.states,
            "transitions": [t.to_dict() for t in self.transitions],
            "confidence": self.confidence,
        }


def _find_status_enum(backend: BackendResult) -> tuple[str, list[dict]] | None:
    """返回 (枚举名, states)。识别名字含 status/state 或取值多为大写状态词的枚举。"""
    for en in backend.enums:
        name = en.name
        values = [v.name for v in en.values]
        is_status = ("status" in name.lower()) or ("state" in name.lower())
        if is_status and values:
            return name, [{"name": v.name, "label": v.label} for v in en.values]
    # 兜底：第一个有值的枚举
    for en in backend.enums:
        if en.values:
            return en.name, [{"name": v.name, "label": v.label} for v in en.values]
    return None


def _extract_status_literals(guard: str) -> set[str]:
    return {
        m.group(1) or m.group(2)
        for m in STATUS_LITERAL_RE.finditer(guard)
        if m.group(1) or m.group(2)
    }


def _is_negated(guard: str) -> bool:
    """否定式：!X.equals(...)、!"X".equals(...) 或 status != OrderStatus.X。"""
    if "!=" in guard and _extract_status_literals(guard):
        return True
    return bool(re.search(r'!\s*(?:[\w.\"\'()]+\.equals|"[A-Z_]+")', guard))


def _target_state(args: str) -> str | None:
    """setStatus(OrderStatus.PAID) / setStatus(PAID) → PAID。"""
    m = re.search(r"(?:Status|State)\s*\.\s*([A-Z][A-Z0-9_]*)", args)
    if m:
        return m.group(1)
    m = re.search(r"\b([A-Z][A-Z0-9_]*)\b", args)
    return m.group(1) if m else None


def _trigger_for(method_name: str) -> str:
    return TRIGGER_MAP.get(method_name.lower(), method_name)


def extract_state_machines(backend: BackendResult, java_files: list[str], root: str) -> list[StateMachineResult]:
    found = _find_status_enum(backend)
    if not found:
        return []
    enum_name, states = found
    all_states = [s["name"] for s in states]
    result = StateMachineResult(name=enum_name, states=states)

    tr_counter = 0
    for f in java_files:
        rel = os.path.relpath(f, root)
        with open(f, "r", encoding="utf-8", errors="replace") as fh:
            lines = [ln.rstrip("\n") for ln in fh.readlines()]

        for i, ln in enumerate(lines):
            sm = SET_STATUS_RE.search(ln)
            if not sm:
                continue
            target = _target_state(sm.group(1))
            if not target or target not in all_states:
                continue
            # 写初始态是"对象初始化"（如 create 里 setStatus(CREATED)），不是状态流转，跳过
            if all_states and target == all_states[0]:
                continue

            # 向上找方法签名（方法边界）
            method_name = None
            method_start = None
            for j in range(i - 1, -1, -1):
                mm = METHOD_SIG_RE.search(lines[j])
                if mm:
                    method_name = mm.group(2)
                    method_start = j
                    break

            # 方法内向上找最近的含状态字面量的 if 守卫（其后必须显式离开当前流程）
            guard_text = None
            if method_name:
                for j in range(i - 1, (method_start or 0) - 1, -1):
                    im = IF_RE.search(lines[j])
                    if im:
                        cond = im.group(1)
                        # 只认含状态字面量且同一方法内存在 throw/continue/return/break 的守卫
                        if _extract_status_literals(cond):
                            seg = "\n".join(lines[j:i])
                            if GUARD_EXIT_RE.search(seg):
                                guard_text = cond.strip()
                                break

            # 推导 from
            if guard_text:
                g = _extract_status_literals(guard_text)
                if _is_negated(guard_text):
                    from_states = sorted(g)
                else:
                    from_states = sorted(set(all_states) - g)
            else:
                from_states = sorted(set(all_states) - {target})

            from_states = [s for s in from_states if s != target]

            tr_counter += 1
            evidence = [f"{rel}#{i + 1}"]
            # 端点证据：controller 中方法名相同
            for ep in backend.endpoints:
                if ep.methodName == method_name:
                    evidence.append(f"{ep.file}#{ep.line}")
                    break

            for fs in from_states:
                tr_counter += 1
                result.transitions.append(
                    Transition(
                        id=f"tr-{tr_counter}",
                        from_=fs,
                        to=target,
                        trigger=_trigger_for(method_name or ""),
                        verified=True,  # 找到 setStatus 写入点
                        evidence=evidence,
                        guard=guard_text or "",
                    )
                )

    if not result.transitions:
        return []
    return [result]


def bfs_paths(states: list[dict], transitions: list, initial_state: str) -> dict[str, list[str]]:
    """BFS 计算从初始态到每个态的最短推进路径（含目标态）。返回 {目标态: [初始态, ..., 目标态]}。

    兼容 Transition 对象与落库后的 dict（t.from_/t.to 或 t["from"]/t["to"]）。
    """
    names = [s["name"] if isinstance(s, dict) else s.name for s in states]
    adj: dict[str, set[str]] = {n: set() for n in names}
    for t in transitions:
        frm = t["from"] if isinstance(t, dict) else t.from_
        to = t["to"] if isinstance(t, dict) else t.to
        adj.setdefault(frm, set()).add(to)

    paths: dict[str, list[str]] = {initial_state: [initial_state]}
    queue = deque([initial_state])
    while queue:
        cur = queue.popleft()
        for nxt in adj.get(cur, set()):
            if nxt not in paths:
                paths[nxt] = paths[cur] + [nxt]
                queue.append(nxt)
    return paths
