"""规则模式测试用例生成器（纯规则，零 LLM）。

四类用例：
- positive：表单必填字段全填合法值 → 提交 → 预期成功文案
- negative：逐个必填字段留空 → 提交 → 预期该字段必填提示
- boundary：有 maxLength/pattern/min 约束的字段 → 构造越界值 → 预期校验提示
- state：每个 verified transition 一条 → 按 BFS 路径先推进到 from 态再执行 trigger

structured_steps 契约（TASK_BOOK 6.3.2）：
  {"order":n, "type":"ui_action|input|state_assert", "action":"...", "target":"...",
   "uiSelector":{"type":"...","value":"..."}, "expected":"", "inputValue":""}
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any

from app.analyzer.models import BackendResult, FrontendResult
from app.agent.state_machine_agent import bfs_paths
from app.agent.quality_gate import is_compound_expected, quoted_anchors

# 字段名 → 合法测试值（启发式，兜底用通用值）
FIELD_SAMPLE: dict[str, str] = {
    "receivername": "张三",
    "name": "张三",
    "username": "张三",
    "phone": "13800138000",
    "mobile": "13800138000",
    "address": "北京市朝阳区测试路1号",
    "amount": "100.00",
    "price": "100.00",
    "money": "100.00",
    "remark": "自动化测试备注",
    "orderno": "ORD123456",
    "order_no": "ORD123456",
    "email": "test@example.com",
}

# 实体/模块名中文映射（启发式）
MODULE_CN = {
    "Order": "订单",
    "OrderStatus": "订单",
    "User": "用户",
    "Product": "商品",
    "Pay": "支付",
    "Goods": "商品",
}

# 状态 action → 中文触发词
TRIGGER_CN = {
    "pay": "支付", "ship": "发货", "complete": "完成", "cancel": "取消",
    "refund": "退款", "receive": "确认收货", "create": "创建",
}

# 触发动作 → 操作成功文案（断言锚点，需与靶站真实文案一致）
TRIGGER_SUCCESS = {
    "支付": "支付成功", "发货": "发货成功", "完成": "订单已完成",
    "取消": "订单已取消", "退款": "退款成功", "确认收货": "确认收货成功",
}


@dataclass
class GeneratedCase:
    title: str
    module: str
    type: str
    priority: str
    preconditions: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    expected_results: list[str] = field(default_factory=list)
    structured_steps: list[dict] = field(default_factory=list)
    test_data: dict = field(default_factory=dict)
    coverage_refs: dict = field(default_factory=dict)
    source: str = "rule"
    confidence: float = 1.0
    # 需求测试点来源（需求版本生成时写入）
    test_point_id: str | None = None


def _module_name(backend: BackendResult) -> str:
    # 优先取有中文映射的实体（领域实体），否则第一个实体
    for e in backend.entities:
        if e.name in MODULE_CN:
            return MODULE_CN[e.name]
    for e in backend.entities:
        return e.name
    return "业务模块"


def _state_module(sm_name: str) -> str:
    """状态机名 → 模块中文名（OrderStatus → 订单）。"""
    base = re.sub(r"(Status|State)$", "", sm_name)
    return MODULE_CN.get(base, base)


def _required_field_names(backend: BackendResult) -> set[str]:
    """从后端实体的 @NotBlank/@NotNull/@NotEmpty 约束推断必填字段名。

    决策：Vue 的必填常在 <script> rules 里（不在 <el-form-item> 标签），前端解析器拿不到，
    故用后端校验注解兜底判定 required。
    """
    names: set[str] = set()
    for e in backend.entities:
        for c in e.fieldConstraints:
            if c.rule in ("notblank", "notnull", "notempty"):
                names.add(c.field)
    return names


def _is_required(field, required_names: set[str]) -> bool:
    return bool(field.required) or (field.prop in required_names) or (field.label in required_names)


def _step(order: int, type_: str, action: str, target: str, selector_type: str, selector_value: str, expected: str = "", input_value: str = "") -> dict:
    return {
        "order": order,
        "type": type_,
        "action": action,
        "target": target,
        "uiSelector": {"type": selector_type, "value": selector_value},
        "expected": expected,
        "inputValue": input_value,
    }


def _sample_value(field_name: str, field_type: str = "input") -> str:
    key = (field_name or "").lower().replace("-", "_")
    if key in FIELD_SAMPLE:
        return FIELD_SAMPLE[key]
    return "测试数据"


def _success_text(frontend: FrontendResult) -> str:
    for t in frontend.uiTexts:
        if "成功" in t:
            return t
    return "操作成功"


def _find_form_pages(frontend: FrontendResult) -> list:
    """返回带表单字段的页面列表。"""
    out = []
    for p in frontend.pages:
        fields = [f for frm in p.forms for f in frm.fields]
        if fields:
            out.append((p, fields))
    return out


def _open_step(route: str, page_name: str, order: int) -> dict:
    return _step(order, "ui_action", f"打开{page_name}页面", page_name, "route", route or "/")


def _fill_step(field, order: int, value: str) -> dict:
    sel_type = "placeholder" if field.placeholder else "label"
    sel_value = field.placeholder or field.label
    return _step(order, "input", f"输入{field.label or field.prop}", field.label or field.prop, sel_type, sel_value, input_value=value)


def _submit_step(order: int, submit_text: str = "提交") -> dict:
    return _step(order, "ui_action", f"点击{submit_text}", submit_text, "text", submit_text)


def _assert_step(order: int, expected: str) -> dict:
    # 引号锚点：三层断言第 2 层要求 expected 有「」锚定的可见文案
    anchor = f"「{expected}」"
    return _step(order, "state_assert", f"验证{expected}", "", "text", anchor, expected=anchor)


def _gen_positive(backend: BackendResult, frontend: FrontendResult) -> list[GeneratedCase]:
    cases = []
    success = _success_text(frontend)
    for page, fields in _find_form_pages(frontend):
        required_names = _required_field_names(backend)
        required = [f for f in fields if _is_required(f, required_names)]
        # 决策：无必填字段的表单（如搜索框）不生成正例。理由：非录入表单，"提交"语义不成立
        if not required:
            continue
        order = 1
        steps = [_open_step(page.route, page.name, order)]
        order += 1
        test_data = {}
        for f in required:
            val = _sample_value(f.prop or f.label, f.type)
            steps.append(_fill_step(f, order, val))
            test_data[f.prop or f.label] = val
            order += 1
        steps.append(_submit_step(order))
        order += 1
        steps.append(_assert_step(order, success))

        cases.append(
            GeneratedCase(
                title=f"{_module_name(backend)}：填写合法信息提交成功",
                module=_module_name(backend),
                type="positive",
                priority="P0",
                preconditions=[f"已进入{page.name}页面"],
                steps=[f"打开{page.name}页面", "填写所有必填字段", "点击提交"],
                expected_results=[success],
                structured_steps=steps,
                test_data=test_data,
                coverage_refs={"endpointIds": [], "transitionIds": [], "ruleIds": []},
            )
        )
    return cases


def _gen_negative(backend: BackendResult, frontend: FrontendResult) -> list[GeneratedCase]:
    cases = []
    for page, fields in _find_form_pages(frontend):
        required_names = _required_field_names(backend)
        required = [f for f in fields if _is_required(f, required_names)]
        for f in required:
            label = f.label or f.prop
            order = 1
            steps = [_open_step(page.route, page.name, order)]
            order += 1
            # 填其他必填字段，唯独当前字段留空
            test_data = {}
            for other in required:
                if other is f:
                    continue
                val = _sample_value(other.prop or other.label)
                steps.append(_fill_step(other, order, val))
                test_data[other.prop or other.label] = val
                order += 1
            steps.append(_submit_step(order))
            order += 1
            expect = f"{label}不能为空"
            steps.append(_assert_step(order, expect))

            cases.append(
                GeneratedCase(
                    title=f"{_module_name(backend)}：{label}({f.prop or label})为空校验失败",
                    module=_module_name(backend),
                    type="negative",
                    priority="P1",
                    preconditions=[f"已进入{page.name}页面"],
                    steps=[f"打开{page.name}页面", f"{label}留空，其余必填项填写", "点击提交"],
                    expected_results=[expect],
                    structured_steps=steps,
                    test_data=test_data,
                    coverage_refs={"endpointIds": [], "transitionIds": [], "ruleIds": []},
                )
            )
    return cases


def _gen_boundary(backend: BackendResult, frontend: FrontendResult) -> list[GeneratedCase]:
    cases = []
    for page, fields in _find_form_pages(frontend):
        # 字段约束来自页面 fields 的 rules（简化），或用后端实体约束
        for f in fields:
            label = f.label or f.prop
            # 有 maxLength / pattern / min 的字段
            rules = f.rules or []
            bound_val = None
            expect = None
            if f.placeholder and "max" in "".join(rules).lower():
                pass  # 简化：rules 多为 ["required"]，长度约束在别处
            # 这里用后端实体约束匹配字段，见调用处注入 constraints
        # 边界用例由调用方注入 entityConstraints 后再生成（见 generate_rule_cases）
    return cases


def _gen_boundary_with_constraints(backend: BackendResult, frontend: FrontendResult) -> list[GeneratedCase]:
    """基于后端实体约束生成边界用例。"""
    cases = []
    # 建立 prop -> constraints 映射（后端实体字段名 = 前端 prop 名，近似）
    # 同字段同 rule 去重：DTO 与实体常复用相同约束，避免重复生成
    constraints: dict[str, list[dict]] = {}
    for e in backend.entities:
        for c in e.fieldConstraints:
            existing = constraints.setdefault(c.field, [])
            if not any(x["rule"] == c.rule for x in existing):
                existing.append({"rule": c.rule, "value": c.value})

    required_names = _required_field_names(backend)
    for page, fields in _find_form_pages(frontend):
        for f in fields:
            label = f.label or f.prop
            cs = constraints.get(f.prop, []) or constraints.get(f.label, [])
            for c in cs:
                rule = c["rule"]
                value = c["value"]
                if rule == "size" and isinstance(value, int):
                    bound_val = "超" * (value + 1)  # 超过 maxLength
                    expect = f"{label}不能超过{value}个字符"
                elif rule == "pattern" and value:
                    bound_val = "123"  # 不符合手机号格式
                    expect = f"{label}格式不正确"
                elif rule in {"max", "min"} and value is not None:
                    bound_val = str(int(value) + 1) if rule == "max" else str(int(value) - 1)
                    expect = f"{label}超出范围"
                else:
                    continue

                order = 1
                steps = [_open_step(page.route, page.name, order)]
                order += 1
                for other in fields:
                    if other is f:
                        continue
                    if _is_required(other, required_names):
                        val = _sample_value(other.prop or other.label)
                        steps.append(_fill_step(other, order, val))
                        order += 1
                steps.append(_fill_step(f, order, bound_val))
                order += 1
                steps.append(_submit_step(order))
                order += 1
                steps.append(_assert_step(order, expect))

                cases.append(
                    GeneratedCase(
                        title=f"{_module_name(backend)}：{label}({f.prop or label})边界校验失败",
                        module=_module_name(backend),
                        type="boundary",
                        priority="P1",
                        preconditions=[f"已进入{page.name}页面"],
                        steps=[f"打开{page.name}页面", f"{label}输入越界值", "点击提交"],
                        expected_results=[expect],
                        structured_steps=steps,
                        test_data={f.prop or label: bound_val},
                        coverage_refs={"endpointIds": [], "transitionIds": [], "ruleIds": []},
                    )
                )
    return cases


def _gen_state(state_machines: list[dict], bfs_paths: dict, frontend: FrontendResult) -> list[GeneratedCase]:
    cases = []
    list_route = ""
    for p in frontend.pages:
        if "list" in (p.route or "").lower():
            list_route = p.route
            break

    for sm in state_machines:
        module = _state_module(sm.get("name", ""))
        states = sm.get("states", [])
        transitions = sm.get("transitions", [])
        initial = states[0]["name"] if states else ""
        label_map = {s["name"]: s.get("label", s["name"]) for s in states}
        # from→to → trigger 反查表（前置推进用真实按钮文字，如"支付"/"发货"）
        trigger_map = {(t["from"], t["to"]): t.get("trigger", "") for t in transitions}
        for t in transitions:
            if not t.get("verified"):
                continue
            from_ = t["from"]
            to = t["to"]
            trigger = t.get("trigger", "")
            # BFS 前置推进路径（到 from 态）
            path = bfs_paths.get(from_, [from_]) if from_ != initial else [from_]
            pre_actions = []
            for i in range(len(path) - 1):
                act = trigger_map.get((path[i], path[i + 1]), "")
                if act:
                    pre_actions.append(act)

            order = 1
            steps = []
            if list_route:
                steps.append(_step(order, "ui_action", "打开订单列表", "订单列表", "route", list_route))
                order += 1
            for act in pre_actions:
                steps.append(_step(order, "ui_action", act, act, "text", act))
                order += 1
            steps.append(_step(order, "ui_action", f"执行{trigger}", trigger, "text", trigger))
            order += 1
            # 断言用操作成功文案（初始数据里没有，只有操作成功才显示 → 不假绿）
            success = TRIGGER_SUCCESS.get(trigger, f"{trigger}成功")
            steps.append(_assert_step(order, success))

            cases.append(
                GeneratedCase(
                    title=f"{module}：{trigger}（{label_map.get(from_, from_)}→{label_map.get(to, to)}）",
                    module=module,
                    type="state",
                    priority="P0",
                    preconditions=[f"存在一条状态为{label_map.get(from_, from_)}的订单"],
                    steps=[f"找到{label_map.get(from_, from_)}的订单", f"执行{trigger}", f"验证状态变为{label_map.get(to, to)}"],
                    expected_results=[success],
                    structured_steps=steps,
                    test_data={},
                    coverage_refs={"endpointIds": [], "transitionIds": [t.get("id", "")], "ruleIds": []},
                )
            )
    return cases


def generate_rule_cases(backend: BackendResult, frontend: FrontendResult, state_machines: list[dict]) -> list[GeneratedCase]:
    cases: list[GeneratedCase] = []
    cases.extend(_gen_positive(backend, frontend))
    cases.extend(_gen_negative(backend, frontend))
    cases.extend(_gen_boundary_with_constraints(backend, frontend))
    # 每个状态机独立算 BFS 最短推进路径，再生成流转用例
    for sm in state_machines:
        states = sm.get("states", [])
        transitions = sm.get("transitions", [])
        if not states or not transitions:
            continue
        initial = states[0]["name"]
        paths = bfs_paths(states, transitions, initial)
        cases.extend(_gen_state([sm], paths, frontend))
    return cases


# ---------- 需求测试点 → 可执行用例（规则兜底，参数取自代码事实） ----------

_POINT_TYPE = {
    "functional": "positive",
    "negative": "negative",
    "boundary": "boundary",
    "state": "state",
    "api": "positive",
    "permission": "negative",
}


_SUCCESS_HINTS = ("成功", "可创建", "可提交", "可保存", "允许", "通过")
_ERROR_HINTS = ("不能", "失败", "错误", "超出", "超过", "不正确", "非法", "为空", "必填", "无效", "未通过", "不通过")
# 同一子句里出现这些词时，「成功/通过」是反讽或前置条件，不能当作正例预期
_NEGATED_SUCCESS_WORDS = ("未通过", "不通过", "无法", "禁止", "不能", "失败", "错误")


def _expects_success(text: str) -> bool:
    """按子句判断是否描述了成功结果，避免被「未通过」「不能成功」误导。"""
    for clause in re.split(r"[；;。]", text or ""):
        if not any(w in clause for w in _SUCCESS_HINTS):
            continue
        if any(w in clause for w in _NEGATED_SUCCESS_WORDS):
            continue
        return True
    return False


def _atomic_expected(text: str) -> str:
    """把单个预期收敛为一个可断言文案；复合预期（多场景/多结果）返回空串。

    - 复合预期不能压成单条断言，交由上层拆成多条用例
    - 只带一个「」锚点时取锚点内文案，去掉「提交后提示」这类前缀
    """
    raw = (text or "").strip()
    if not raw or is_compound_expected(raw):
        return ""
    anchors = quoted_anchors(raw)
    if len(anchors) == 1:
        return anchors[0]
    return raw.strip("「」").strip()


def _fallback_anchor(text: str) -> str:
    """极端兜底：复合预期只能保留一个锚点时，取最后一个（通常是错误提示）。"""
    anchors = quoted_anchors(text)
    if anchors:
        return anchors[-1]
    return re.split(r"[；;]", (text or "").strip())[0].strip().strip("「」")


def _assert_expected(order: int, text: str) -> dict:
    """预期文案断言：一个断言只留一个「」锚点，避免嵌套引号让执行器切错文案。"""
    anchor_text = _atomic_expected(text) or _fallback_anchor(text)
    anchor = f"「{anchor_text}」" if anchor_text else ""
    return _step(order, "state_assert", f"验证{anchor}", "", "text", anchor, expected=anchor)


def _page_for_point(point: dict, frontend: FrontendResult):
    if not frontend:
        return None
    route = (point.get("pageRoute") or "").strip()
    if route:
        for p in frontend.pages:
            if p.route == route:
                return p
    text = f"{point.get('title', '')} {point.get('description', '')}"
    for p in frontend.pages:
        if p.name and p.name in text:
            return p
        key = (p.route or "").strip("/")
        if key and key in text:
            return p
    form_pages = [p for p in frontend.pages if any(fm.fields for fm in p.forms)]
    return form_pages[0] if form_pages else (frontend.pages[0] if frontend.pages else None)


def _page_fields(page) -> list:
    if page is None:
        return []
    return [f for form in page.forms for f in form.fields]


def _pick_fields(point: dict, page) -> list:
    fields = _page_fields(page)
    wanted = [str(x) for x in (point.get("fields") or [])]
    if not wanted:
        return fields
    picked = [f for f in fields if (f.prop in wanted) or (f.label in wanted)]
    if picked:
        # 保留页面顺序，避免步骤与表单视觉顺序不一致
        return [f for f in fields if f in picked]
    return fields


def _point_module(point: dict, page, backend: BackendResult) -> str:
    if page is not None and page.name:
        return page.name
    return _module_name(backend)


def _point_expected(point: dict, fallback: str) -> str:
    """测试点的单条可断言预期；复合预期不使用，改用场景兜底文案。"""
    return _atomic_expected(str(point.get("expected") or "")) or fallback


def _boundary_expect(point: dict, *, valid: bool, success: str, fallback: str) -> str:
    """从测试点预期中挑出与当前边界场景匹配的单条可见文案。"""
    raw = str(point.get("expected") or "").strip()
    atomic = _atomic_expected(raw)
    if atomic:
        # 单预期只在语义与当前场景一致时采用；成功/失败混写时改用场景兜底
        has_error = any(w in raw for w in _ERROR_HINTS)
        if (valid and _expects_success(raw) and not has_error) or (
            not valid and has_error and not _expects_success(raw)
        ):
            return atomic
        return success if valid else fallback
    hints = _SUCCESS_HINTS if valid else _ERROR_HINTS
    for clause in re.split(r"[；;。]", raw):
        if not clause.strip():
            continue
        if valid and not _expects_success(clause):
            continue
        # 越界场景不能被成功子句误导（如「…可创建并提示「订单创建成功」」）
        if not valid and (_expects_success(clause) or not any(w in clause for w in hints)):
            continue
        anchors = quoted_anchors(clause)
        if anchors:
            return anchors[0]
    return success if valid else fallback


def _finish_point_case(case: GeneratedCase, point: dict, page, backend: BackendResult) -> GeneratedCase:
    """把模板用例绑定到测试点：标题/优先级/来源/断言以测试点为准。"""
    base_title = point.get("title") or case.title
    # 一个测试点生成多条用例时保留（字段）后缀，避免标题重复被去重
    suffix = re.search(r"（[^（）]{1,20}）$", case.title)
    if suffix and suffix.group(0) not in base_title:
        base_title = f"{base_title}{suffix.group(0)}"
    case.title = base_title[:255]
    case.priority = point.get("priority") or case.priority
    case.module = _point_module(point, page, backend)
    case.test_point_id = point.get("id")
    refs = dict(case.coverage_refs or {})
    refs["testPointIds"] = [point.get("id")] if point.get("id") else []
    case.coverage_refs = refs
    if point.get("expected"):
        expected = _atomic_expected(str(point["expected"]))
        # 复合预期不能覆盖模板已拆好的单场景断言（如 20字符成功 / 21字符报错）
        if expected:
            case.expected_results = [expected]
            last = case.structured_steps[-1] if case.structured_steps else None
            if last and last.get("type") == "state_assert":
                case.structured_steps[-1] = _assert_expected(last.get("order", len(case.structured_steps)), expected)
    case.steps = [s.get("action", "") for s in case.structured_steps if s.get("action")][:8]
    return case


def _generic_point_case(point: dict, backend: BackendResult, frontend: FrontendResult, reason: str) -> GeneratedCase:
    """无法匹配页面时的兜底用例：至少保证测试点在用例列表可追踪、可手工完善。"""
    page = _page_for_point(point, frontend)
    route = (point.get("pageRoute") or (page.route if page else "") or "/").strip() or "/"
    expected = _point_expected(point, point.get("title") or "操作成功")
    steps = [
        _step(1, "ui_action", f"打开页面{route}", route, "route", route),
        _assert_expected(2, expected),
    ]
    return GeneratedCase(
        title=(point.get("title") or "测试点")[:255],
        module=_point_module(point, page, backend),
        type=_POINT_TYPE.get(point.get("category", "functional"), "positive"),
        priority=point.get("priority") or "P1",
        preconditions=[f"需求版本测试点：{reason}"],
        steps=[s["action"] for s in steps],
        expected_results=[expected],
        structured_steps=steps,
        test_data={},
        coverage_refs={"endpointIds": [], "transitionIds": [], "ruleIds": [], "testPointIds": [point.get("id")]},
        source="rule",
        confidence=0.5,
        test_point_id=point.get("id"),
    )


def _state_cases_for_points(machines: list[dict], frontend: FrontendResult) -> list[tuple[dict, GeneratedCase]]:
    out: list[tuple[dict, GeneratedCase]] = []
    for sm in machines:
        states = sm.get("states") or []
        transitions = sm.get("transitions") or []
        if not states or not transitions:
            continue
        paths = bfs_paths(states, transitions, states[0]["name"])
        for case in _gen_state([sm], paths, frontend):
            out.append((sm, case))
    return out


def generate_rule_cases_for_points(
    points: list[dict],
    backend: BackendResult,
    frontend: FrontendResult,
    machines: list[dict],
) -> list[GeneratedCase]:
    """按测试点生成用例：functional/negative/boundary/state，参数结合代码事实。"""
    if not points:
        return []
    required_names = _required_field_names(backend)
    success = _success_text(frontend)
    state_cases = _state_cases_for_points(machines, frontend)
    cases: list[GeneratedCase] = []

    for point in points:
        category = point.get("category") or "functional"
        page = _page_for_point(point, frontend)
        fields = _pick_fields(point, page)
        page_fields = _page_fields(page)
        text = f"{point.get('title', '')} {point.get('description', '')}"
        # 测试点名里明确提到的字段优先（如「手机号格式不正确」→ 只针对 phone）
        mentioned = [f for f in fields if (f.label and f.label in text) or (f.prop and f.prop in text)]

        # ---- 状态流转：优先复用状态机 BFS 用例，断言换成测试点预期 ----
        if category == "state" and state_cases:
            matched = None
            for _sm, sc in state_cases:
                for t in _sm.get("transitions", []):
                    trigger = t.get("trigger") or ""
                    if trigger and trigger in text and trigger in sc.title:
                        matched = sc
                        break
                if matched:
                    break
            if matched is not None:
                cases.append(_finish_point_case(copy.deepcopy(matched), point, page, backend))
                continue

        # ---- 反例：测试点指定字段留空 ----
        if category == "negative" and page is not None:
            base = mentioned or fields
            targets = [f for f in base if _is_required(f, required_names)] or mentioned or fields[:1]
            made = False
            # 格式/非法值类反例：填一个非法值，而不是留空
            invalid_value = "123" if any(w in text for w in ("格式", "不正确", "非法", "不符合")) else ""
            for f in targets[:2]:
                label = f.label or f.prop
                order = 1
                steps = [_open_step(page.route, page.name, order)]
                order += 1
                test_data = {}
                # 填齐页面上的其他必填项，保证只有当前字段触发校验错误
                for other in page_fields:
                    if other is f:
                        continue
                    if _is_required(other, required_names) or len(page_fields) <= 3:
                        val = _sample_value(other.prop or other.label)
                        steps.append(_fill_step(other, order, val))
                        test_data[other.prop or other.label] = val
                        order += 1
                if invalid_value:
                    steps.append(_fill_step(f, order, invalid_value))
                    test_data[f.prop or label] = invalid_value
                    order += 1
                steps.append(_submit_step(order))
                order += 1
                expect = _point_expected(point, f"{label}格式不正确" if invalid_value else f"{label}不能为空")
                steps.append(_assert_expected(order, expect))
                case = GeneratedCase(
                    title=(point.get("title") or f"{label}为空校验")[:255],
                    module=_point_module(point, page, backend),
                    type="negative",
                    priority=point.get("priority") or "P1",
                    preconditions=[f"已进入{page.name}页面"],
                    steps=[s["action"] for s in steps],
                    expected_results=[expect],
                    structured_steps=steps,
                    test_data=test_data,
                    coverage_refs={"endpointIds": [], "transitionIds": [], "ruleIds": []},
                    test_point_id=point.get("id"),
                )
                if len(targets) > 1:
                    case.title = f"{case.title}（{label}）"[:255]
                cases.append(_finish_point_case(case, point, page, backend))
                made = True
            if made:
                continue

        # ---- 边界：测试点字段的约束；合法边界与越界值拆成两条用例 ----
        if category == "boundary" and page is not None and fields:
            constraint_map: dict[str, list] = {}
            for e in backend.entities:
                for c in e.fieldConstraints:
                    if c.rule in ("size", "pattern", "max", "min") and c.value is not None:
                        constraint_map.setdefault(c.field, []).append(c)
            preferred = [f for f in fields if (f.prop in constraint_map) or (f.label in constraint_map)]
            preferred = [f for f in preferred if f in mentioned] or preferred
            target_fields = preferred
            raw_expect = str(point.get("expected") or "")
            # 复合边界点（合法+越界）拆成两条；只有越界或只有合法时只出一条
            wants_valid = _expects_success(raw_expect)
            wants_invalid = any(w in raw_expect for w in _ERROR_HINTS) or not wants_valid
            made = False
            for f in target_fields[:2]:
                cons = constraint_map.get(f.prop) or constraint_map.get(f.label) or []
                if not cons:
                    continue
                c = cons[0]
                label = f.label or f.prop
                multi_target = len(target_fields) > 1

                def suffix(text: str, _label: str = label, _multi: bool = multi_target) -> str:
                    return f"（{_label}{text}）" if _multi else f"（{text}）"

                if c.rule == "size":
                    limit = int(c.value)
                    default_error = f"{label}不能超过{c.value}个字符"
                    scenarios = [
                        ("valid", "合" * limit, suffix(f"{limit}字符合法")),
                        ("invalid", "超" * (limit + 1), suffix(f"{limit + 1}字符超长")),
                    ]
                elif c.rule == "pattern":
                    default_error = f"{label}格式不正确"
                    scenarios = [("invalid", "123", suffix("格式错误"))]
                    # 只有已知样本能保证满足正则时才生成合法值用例，避免造出必失败的正例
                    sample_key = (f.prop or label or "").lower().replace("-", "_")
                    if sample_key in FIELD_SAMPLE:
                        scenarios.insert(0, ("valid", FIELD_SAMPLE[sample_key], suffix("格式合法")))
                elif c.rule == "max":
                    limit = int(c.value)
                    default_error = f"{label}超出范围"
                    scenarios = [
                        ("valid", str(limit), suffix(f"取上限{limit}")),
                        ("invalid", str(limit + 1), suffix("超过上限")),
                    ]
                else:  # min
                    limit = int(c.value)
                    default_error = f"{label}超出范围"
                    scenarios = [
                        ("valid", str(limit), suffix(f"取下限{limit}")),
                        ("invalid", str(limit - 1), suffix("低于下限")),
                    ]

                for kind, value, scenario_suffix in scenarios:
                    if kind == "valid" and not wants_valid:
                        continue
                    if kind == "invalid" and not wants_invalid:
                        continue
                    expect = _boundary_expect(
                        point,
                        valid=(kind == "valid"),
                        success=success,
                        fallback=default_error,
                    )
                    order = 1
                    steps = [_open_step(page.route, page.name, order)]
                    order += 1
                    test_data = {}
                    # 填齐其他必填项：合法边界要能真正提交成功，越界也只让目标字段报错
                    for other in page_fields:
                        if other is f:
                            continue
                        if _is_required(other, required_names):
                            val = _sample_value(other.prop or other.label)
                            steps.append(_fill_step(other, order, val))
                            test_data[other.prop or other.label] = val
                            order += 1
                    steps.append(_fill_step(f, order, value))
                    test_data[f.prop or label] = value
                    order += 1
                    steps.append(_submit_step(order))
                    order += 1
                    steps.append(_assert_expected(order, expect))
                    title = point.get("title") or f"{label}边界校验"
                    if len(scenarios) > 1:
                        title = f"{title[: 255 - len(scenario_suffix)]}{scenario_suffix}"
                    case = GeneratedCase(
                        title=title[:255],
                        module=_point_module(point, page, backend),
                        type="boundary",
                        priority=point.get("priority") or "P1",
                        preconditions=[f"已进入{page.name}页面"],
                        steps=[s["action"] for s in steps],
                        expected_results=[expect],
                        structured_steps=steps,
                        test_data=test_data,
                        coverage_refs={"endpointIds": [], "transitionIds": [], "ruleIds": []},
                        test_point_id=point.get("id"),
                    )
                    cases.append(_finish_point_case(case, point, page, backend))
                    made = True
            if made:
                continue

        # ---- 正例 / 接口 / 权限：按页面填具体参数并提交 ----
        if page is not None:
            # 必须填齐页面全部必填字段，否则正例/合法边界无法真正提交成功
            fill_fields = [f for f in page_fields if _is_required(f, required_names) or f in fields]
            fill_fields = fill_fields or fields
            expect = _point_expected(point, success)
            order = 1
            steps = [_open_step(page.route, page.name, order)]
            order += 1
            test_data = {}
            for f in fill_fields[:8]:
                val = _sample_value(f.prop or f.label)
                steps.append(_fill_step(f, order, val))
                test_data[f.prop or f.label] = val
                order += 1
            if fill_fields:
                steps.append(_submit_step(order))
                order += 1
            steps.append(_assert_expected(order, expect))
            case = GeneratedCase(
                title=(point.get("title") or f"{page.name}功能验证")[:255],
                module=_point_module(point, page, backend),
                type=_POINT_TYPE.get(category, "positive"),
                priority=point.get("priority") or "P1",
                preconditions=[f"已进入{page.name}页面"],
                steps=[s["action"] for s in steps],
                expected_results=[expect],
                structured_steps=steps,
                test_data=test_data,
                coverage_refs={"endpointIds": [], "transitionIds": [], "ruleIds": []},
                test_point_id=point.get("id"),
            )
            cases.append(_finish_point_case(case, point, page, backend))
            continue

        cases.append(_generic_point_case(point, backend, frontend, "未匹配到页面，需人工补充步骤"))
    return cases
