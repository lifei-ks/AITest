"""把「固定总数」断言改写为运行时基线断言。

问题：测试数据持久化后，列表总数不是固定的 4/5。直接断言「共 5 条」会在脏数据
环境必然失败。正确做法是执行前读取基线，再断言「基线 + 1」。

本模块只处理结构明确、可安全推导的场景：
- 用例中存在创建/新增动作
- 能在期望或页面事实中找到列表路由
- 只有一个“共/总数/数量 N 条”断言
其余情况不改写，交给审核/人工处理，避免生成看似动态但不可执行的步骤。
"""
from __future__ import annotations

import re

from app.agent.quality_gate import quoted_anchors

COUNT_CLAUSE_RE = re.compile(r"(?:共|总数(?:变为|为)?|数量(?:变为|为)?)\s*(\d+)\s*(条|个|项|行)")
NUMBER_RE = re.compile(r"\d+")
CREATE_WORDS = ("提交", "创建", "新增", "保存")
ROUTE_RE = re.compile(r"/[A-Za-z0-9_\-/]+")


def _flexible(text: str) -> str:
    """把中文短语变成允许空白的正则片段。"""
    parts = [re.escape(part) for part in (text or "").split() if part]
    return r"\s*".join(parts)


def _count_pattern(anchor: str) -> str:
    match = NUMBER_RE.search(anchor or "")
    if not match:
        return r"(\d+)"
    prefix = (anchor[: match.start()] or "").strip()
    suffix = (anchor[match.end():] or "").strip()
    pattern = f"{_flexible(prefix)}\\s*(\\d+)\\s*{_flexible(suffix)}".strip()
    return pattern or r"(\d+)"


def _list_route(case: dict, list_routes: list[str]) -> str:
    text = " ".join(
        [*(case.get("expectedResults") or []), *(case.get("steps") or [])]
    )
    for match in ROUTE_RE.finditer(text):
        route = match.group(0)
        if "list" in route.lower():
            return route
    for route in list_routes:
        if "list" in (route or "").lower():
            return route
    return ""


def _has_create_action(steps: list[dict], before_index: int) -> bool:
    for step in steps[:before_index]:
        if step.get("type") != "ui_action":
            continue
        text = f"{step.get('action') or ''} {step.get('target') or ''}"
        if any(word in text for word in CREATE_WORDS):
            return True
    return False


def _rewrite_expected_text(text: str) -> str:
    return COUNT_CLAUSE_RE.sub("总数比操作前增加 1 条", text or "", count=1)


def normalize_case_counts(case: dict, list_routes: list[str] | None = None) -> dict:
    """原地改写 case（camelCase dict）；返回 changed/notes，不负责持久化。"""
    steps = case.get("structuredSteps") or []
    count_steps: list[tuple[int, str, str]] = []
    for index, step in enumerate(steps):
        if step.get("type") != "state_assert":
            continue
        raw_expected = str(step.get("expected") or "")
        if "${" in raw_expected:
            continue
        anchors = quoted_anchors(raw_expected)
        for anchor in anchors:
            if COUNT_CLAUSE_RE.search(anchor):
                count_steps.append((index, raw_expected, anchor))
                break
    if len(count_steps) != 1:
        return {
            "changed": False,
            "drop": bool(count_steps),
            "notes": ["存在多个固定总数断言，无法安全转换为运行时基线"] if count_steps else [],
        }
    index, raw_expected, anchor = count_steps[0]
    if not _has_create_action(steps, index):
        return {
            "changed": False,
            "drop": True,
            "notes": [
                "固定总数断言没有创建/新增动作可建立基线，无法在共享数据环境稳定执行；请改用唯一行标识或数据隔离测试"
            ],
        }
    route = _list_route(case, list_routes or [])
    if not route:
        return {
            "changed": False,
            "drop": True,
            "notes": ["检测到固定总数断言，但未能确定列表路由，无法建立运行时基线"],
        }

    pattern = _count_pattern(anchor)
    variable = "base_count"
    capture = {
        "order": 0,
        "type": "capture",
        "action": "读取当前列表总数作为基线",
        "target": "列表总数",
        "uiSelector": {"type": "text", "value": ""},
        "expected": "",
        "inputValue": "",
        "variable": variable,
        "pattern": pattern,
    }
    route_step = {
        "order": 0,
        "type": "ui_action",
        "action": "打开列表读取执行前总数",
        "target": "列表",
        "uiSelector": {"type": "route", "value": route},
        "expected": "",
        "inputValue": "",
        "variable": "",
        "pattern": "",
    }

    first_selector = (steps[0].get("uiSelector") or {}) if steps else {}
    if steps and first_selector.get("type") == "route" and first_selector.get("value") == route:
        insert_at = 1
    else:
        steps.insert(0, route_step)
        insert_at = 1
    steps.insert(insert_at, capture)
    for order, step in enumerate(steps, start=1):
        step["order"] = order

    def _dynamic(text: str) -> str:
        return COUNT_CLAUSE_RE.sub(
            lambda match: match.group(0).replace(match.group(1), "${base_count+1}"),
            text,
            count=1,
        )

    # index 是改写前的位置；插入步骤后按内容重新定位原本的 count 断言
    target_step = next(
        (
            step
            for step in steps
            if step.get("type") == "state_assert"
            and COUNT_CLAUSE_RE.search(str(step.get("expected") or ""))
            and "${" not in str(step.get("expected") or "")
        ),
        None,
    )
    if target_step is None:
        return {"changed": False, "notes": ["定位固定数量断言失败，未自动改写"]}
    target_step["expected"] = _dynamic(str(target_step.get("expected") or ""))
    selector = target_step.get("uiSelector")
    if isinstance(selector, dict) and selector.get("value"):
        selector["value"] = _dynamic(str(selector["value"]))

    new_expected_results = [
        _rewrite_expected_text(str(item)) for item in (case.get("expectedResults") or [])
    ]
    if new_expected_results:
        case["expectedResults"] = new_expected_results
    preconditions = list(case.get("preconditions") or [])
    note = "执行前读取列表总数作为运行时基线，总数按「基线+1」断言，不使用固定种子数量"
    if note not in preconditions:
        preconditions.append(note)
    case["preconditions"] = preconditions
    case["steps"] = [str(step.get("action") or "") for step in steps if step.get("action")]
    return {
        "changed": True,
        "notes": [
            "已把固定总数断言改为运行时基线：先 capture 当前总数，再断言「基线+1」"
        ],
    }
