"""质量闸：硬门槛 + 分项评分 + 结构化场景去重。

设计原则：
- 硬门槛决定用例是否可自动执行，质量分只做排序、展示和复核优先级。
- 分项评分，每个问题只扣一次；warning 只展示，不参与打分。
- 去重使用结构化场景签名（路由 + 步骤 + 选择器 + 输入值 + 断言锚点）。
- 近似重复只记录 warning，不直接丢弃，避免误杀 20/21 字符这类边界用例。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

# 接口化描述关键词（命中即 lint warning）
FORBIDDEN_API_WORDS = [
    "调用接口", "调用api", "发送请求", "请求接口", "http请求",
    "验证数据库", "检查数据库", "查询数据库", "检查返回值", "api_call",
]

# 不可验证的视觉断言关键词
VAGUE_ASSERT_WORDS = ["显示正常", "符合预期", "页面正常", "正常显示", "没问题"]

# 页面可见文案锚点（与执行器保持一致：中文角括号/弯引号/英文引号）
QUOTE_ANCHOR_RE = re.compile(r"[\u300c\u300e\u201c\u2018\"']([^\u300d\u300f\u201d\u2019\"']+)[\u300d\u300f\u201d\u2019\"']")
COMPOUND_SEPARATOR_RE = re.compile(r"[；;]")
COUNT_ASSERT_RE = re.compile(r"(?:共|总数(?:变为|为)?|数量(?:变为|为)?)\s*\d+\s*(?:条|个|项|行)")
SHARED_DATA_RE = re.compile(r"\b1[3-9]\d{9}\b|\bORD\d{4,}\b|\bPO\d{4,}\b", re.IGNORECASE)
UNIQUE_MARKER_WORDS = ("首行", "第一个", "唯一", "订单号", "指定订单", "按订单号")
ALLOWED_STEP_TYPES = {"ui_action", "input", "capture", "state_assert"}


def _get(case, *names, default=None):
    """兼容对象属性和 camelCase/snake_case dict。"""
    for name in names:
        if isinstance(case, dict):
            if name in case and case[name] is not None:
                return case[name]
        else:
            value = getattr(case, name, None)
            if value is not None:
                return value
    return default


def _steps(case) -> list[dict]:
    value = _get(case, "structured_steps", "structuredSteps", default=[])
    return [step for step in value if isinstance(step, dict)] if isinstance(value, (list, tuple)) else []


def _expected(case) -> list[str]:
    value = _get(case, "expected_results", "expectedResults", default=[])
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    return []


def _title(case) -> str:
    return str(_get(case, "title", default="") or "")


def _test_point_id(case) -> str:
    return str(_get(case, "test_point_id", "testPointId", default="") or "")


def _coverage(case) -> dict:
    value = _get(case, "coverage_refs", "coverageRefs", default={})
    return value if isinstance(value, dict) else {}


def quoted_anchors(text: str) -> list[str]:
    """提取预期中的可见文案锚点，如「订单创建成功」→ ['订单创建成功']。"""
    return [m.strip() for m in QUOTE_ANCHOR_RE.findall(text or "") if m.strip()]


def is_compound_expected(text: str) -> bool:
    """预期是否描述了多个场景/多个可观察结果。"""
    raw = (text or "").strip()
    if not raw:
        return False
    if raw.count("「") >= 2 or raw.count("『") >= 2:
        return True
    anchors = quoted_anchors(raw)
    if len(anchors) >= 2:
        return True
    if any("「" in a or "『" in a for a in anchors):
        return True
    parts = [p.strip() for p in COMPOUND_SEPARATOR_RE.split(raw) if p.strip()]
    return len(parts) >= 2 and all(len(p) >= 4 for p in parts)


def case_has_compound_expected(case) -> bool:
    """用例的 expectedResults 或 state_assert 步骤是否含复合预期。"""
    if any(is_compound_expected(exp) for exp in _expected(case)):
        return True
    for step in _steps(case):
        if step.get("type") != "state_assert":
            continue
        selector = step.get("uiSelector") or {}
        for raw in (step.get("expected"), selector.get("value")):
            if is_compound_expected(str(raw or "")):
                return True
    return False


def steps_have_multiple_inputs(steps) -> bool:
    """同一字段被填入两个不同的值 → 一条用例在验证两个输入场景。"""
    seen: dict[str, str] = {}
    for step in steps or []:
        if step.get("type") != "input":
            continue
        selector = step.get("uiSelector") or {}
        key = str(selector.get("value") or step.get("target") or "").strip()
        value = str(step.get("inputValue") or "").strip()
        if not key or not value:
            continue
        if key in seen and seen[key] != value:
            return True
        seen[key] = value
    return False


def case_has_multiple_inputs(case) -> bool:
    return steps_have_multiple_inputs(_steps(case))


@dataclass
class GateResult:
    generated: int
    kept: list = field(default_factory=list)
    dropped: int = 0
    dropReasons: dict = field(default_factory=dict)
    needs_review: int = 0
    near_duplicate: int = 0


class UiLanguageLinter:
    """闸 1：拦截接口化描述、无 uiSelector 的抽象动作、不可验证断言。"""

    def lint_details(self, case) -> list[dict]:
        issues: list[dict] = []
        for step in _steps(case):
            action = step.get("action", "")
            target = step.get("target", "")
            text = f"{action} {target}".lower()
            for word in FORBIDDEN_API_WORDS:
                if word in text:
                    issues.append({
                        "code": "api_wording",
                        "message": f"接口化描述：{action}",
                        "severity": "warning",
                    })
                    break
            selector = step.get("uiSelector") or {}
            if step.get("type") in ("ui_action", "input") and not selector.get("value"):
                issues.append({
                    "code": "missing_selector",
                    "message": f"缺少 uiSelector：{action}",
                    "severity": "warning",
                })
            if step.get("type") == "capture":
                if not str(step.get("variable") or "").strip():
                    issues.append({
                        "code": "capture_missing_variable",
                        "message": f"capture 缺少 variable：{action}",
                        "severity": "warning",
                    })
                if not str(step.get("pattern") or "").strip():
                    issues.append({
                        "code": "capture_missing_pattern",
                        "message": f"capture 缺少 pattern：{action}",
                        "severity": "warning",
                    })
        for expected in _expected(case):
            for word in VAGUE_ASSERT_WORDS:
                if word in expected:
                    issues.append({
                        "code": "vague_assertion",
                        "message": f"不可验证断言：{expected}",
                        "severity": "warning",
                    })
                    break
        return issues

    def lint(self, case) -> list[str]:
        return [item["message"] for item in self.lint_details(case)]


def _anchors(case) -> list[str]:
    texts = list(_expected(case))
    for step in _steps(case):
        if step.get("type") != "state_assert":
            continue
        texts.append(str(step.get("expected") or ""))
        texts.append(str((step.get("uiSelector") or {}).get("value") or ""))
    anchors: list[str] = []
    for text in texts:
        anchors.extend(quoted_anchors(str(text)))
    return list(dict.fromkeys(item.strip() for item in anchors if item.strip()))


def _has_capture(steps: list[dict]) -> bool:
    return any(
        step.get("type") == "capture" and str(step.get("variable") or "").strip()
        for step in steps
    )


def _has_absolute_count(expected: list[str]) -> bool:
    return any("${" not in text and COUNT_ASSERT_RE.search(text) for text in expected)


def _has_shared_data(steps: list[dict]) -> bool:
    return any(
        step.get("type") == "input" and SHARED_DATA_RE.search(str(step.get("inputValue") or ""))
        for step in steps
    )


def _has_unique_marker(steps: list[dict]) -> bool:
    text = " ".join(
        f"{step.get('action') or ''} {step.get('target') or ''}"
        for step in steps
    )
    return any(word in text for word in UNIQUE_MARKER_WORDS)


def score_case_detailed(case, warnings: list[str] | None = None) -> dict:
    """分项评分：断言 30 + 数据独立性 25 + 选择器 20 + 覆盖 15 + 步骤规模 10。"""
    steps = _steps(case)
    expected = _expected(case)
    case_type = str(_get(case, "type", default="") or "")

    assertion = 30
    assertion_issues: list[str] = []
    anchors = _anchors(case)
    if not anchors:
        assertion -= 15
        assertion_issues.append("缺少可验证的引号锚点")
    elif len(anchors) > 1:
        assertion -= 10
        assertion_issues.append(f"断言锚点过多：{len(anchors)} 个")
    vague = [
        word for word in VAGUE_ASSERT_WORDS
        if any(word in text for text in expected)
    ]
    if vague:
        assertion -= 10
        assertion_issues.append(f"不可验证断言：{', '.join(vague)}")
    assertion = max(assertion, 0)

    data_independence = 25
    data_issues: list[str] = []
    if _has_absolute_count(expected) and not _has_capture(steps):
        data_independence -= 15
        data_issues.append("固定总数断言没有 capture 基线")
    if _has_shared_data(steps):
        data_independence -= 10
        data_issues.append("使用固定手机号/订单号等共享数据")
    if case_type == "state" and not _has_unique_marker(steps) and not _has_capture(steps):
        data_independence -= 10
        data_issues.append("状态用例缺少唯一行标识")
    data_independence = max(data_independence, 0)

    selector_stability = 20
    selector_issues: list[str] = []
    for step in steps:
        if step.get("type") not in ("ui_action", "input"):
            continue
        selector = step.get("uiSelector") or {}
        selector_type = str(selector.get("type") or "")
        if selector_type in ("testid", "role", "route"):
            penalty = 0
        elif selector_type in ("placeholder", "label"):
            penalty = 2
        elif selector_type == "text":
            penalty = 4
        elif selector_type in ("css", "xpath"):
            penalty = 8
        else:
            penalty = 8
        selector_stability -= penalty
        if penalty:
            selector_issues.append(f"{selector_type or '空'} 定位扣 {penalty} 分")
    selector_stability = max(selector_stability, 0)

    coverage_refs = _coverage(case)
    has_refs = bool(coverage_refs) and any(bool(value) for value in coverage_refs.values())
    has_test_point = bool(_test_point_id(case))
    if has_refs and has_test_point:
        coverage_binding = 15
    elif has_refs or has_test_point:
        coverage_binding = 8
    else:
        coverage_binding = 0

    step_count = len(steps)
    if step_count <= 10:
        step_scale = 10
    elif step_count <= 15:
        step_scale = 5
    else:
        step_scale = 0

    score = assertion + data_independence + selector_stability + coverage_binding + step_scale
    return {
        "score": max(0, min(100, score)),
        "dimensions": {
            "assertion": {"score": assertion, "max": 30, "issues": assertion_issues},
            "dataIndependence": {"score": data_independence, "max": 25, "issues": data_issues},
            "selectorStability": {"score": selector_stability, "max": 20, "issues": selector_issues},
            "coverageBinding": {"score": coverage_binding, "max": 15, "issues": []},
            "stepScale": {"score": step_scale, "max": 10, "issues": []},
        },
        "warnings": list(warnings or []),
    }


def score_case(case, lint_issues: list[str] | None = None) -> int:
    """兼容旧调用：返回分项评分总分。lint_issues 作为 warning，不参与扣分。"""
    return int(score_case_detailed(case, warnings=lint_issues).get("score", 0))


def _route(case) -> str:
    for step in _steps(case):
        selector = step.get("uiSelector") or {}
        if selector.get("type") == "route":
            return str(selector.get("value") or "")
    return ""


def scenario_signature(case, scope: str = "") -> str:
    """结构化场景签名：路由 + 每步类型/动作/选择器/输入值 + 断言锚点。"""
    steps = []
    for step in _steps(case):
        selector = step.get("uiSelector") or {}
        steps.append([
            str(step.get("type") or ""),
            str(step.get("action") or ""),
            str(step.get("target") or ""),
            str(selector.get("type") or ""),
            str(selector.get("value") or ""),
            str(step.get("inputValue") or ""),
            str(step.get("expected") or ""),
            str(step.get("variable") or ""),
            str(step.get("pattern") or ""),
        ])
    payload = {
        "scope": scope,
        "testPointId": _test_point_id(case),
        "route": _route(case),
        "steps": steps,
        "anchors": _anchors(case),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def hard_gate_issues(case) -> list[tuple[str, str]]:
    """硬门槛：失败即不可自动执行。"""
    steps = _steps(case)
    issues: list[tuple[str, str]] = []
    if not steps:
        issues.append(("no_steps", "没有结构化步骤，无法自动执行"))
    if not _expected(case):
        issues.append(("no_expected", "缺少预期结果，无法判定成功/失败"))
    if case_has_compound_expected(case):
        issues.append(("compound_expected", "包含复合预期：一条用例判断多个输入/结果，必须拆分"))
    if case_has_multiple_inputs(case):
        issues.append(("multiple_inputs", "同一字段被填入多个不同值：一条用例验证多个输入场景，必须拆分"))
    unknown = sorted({
        str(step.get("type") or "")
        for step in steps
        if str(step.get("type") or "") not in ALLOWED_STEP_TYPES
    })
    if unknown:
        issues.append(("unknown_step_type", f"未知步骤类型：{', '.join(unknown)}"))
    if steps and not any(step.get("type") == "state_assert" for step in steps):
        issues.append(("no_assertion", "没有断言步骤，无法判定执行结果"))
    return issues


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def _scenario_text(case) -> str:
    """近似重复的辅助指纹：包含选择器值，避免同名不同元素的用例被误判。"""
    steps = "".join(
        f"{step.get('action', '')}{step.get('target', '')}"
        f"{(step.get('uiSelector') or {}).get('value', '')}"
        f"{step.get('inputValue', '')}{step.get('expected', '')}"
        f"{step.get('variable', '')}{step.get('pattern', '')}"
        for step in _steps(case)
    )
    return _normalize(f"{_title(case)}{steps}{'|'.join(_expected(case))}")


def _similarity(a, b) -> float:
    return SequenceMatcher(None, _scenario_text(a), _scenario_text(b)).ratio()


def _same_bucket(a, b) -> bool:
    if _route(a) != _route(b):
        return False
    types_a = tuple(str(step.get("type") or "") for step in _steps(a))
    types_b = tuple(str(step.get("type") or "") for step in _steps(b))
    return types_a == types_b


def is_near_duplicate(a, b, threshold: float = 0.98) -> bool:
    """近似重复：仅同路由、同步骤类型序列时判定，避免跨场景误报。"""
    return _same_bucket(a, b) and _similarity(a, b) > threshold


def _same_expected(a, b) -> bool:
    return _normalize("|".join(_expected(a))) == _normalize("|".join(_expected(b)))


def _is_duplicate(case, existing) -> bool:
    """精确判重：场景签名相同才算重复；不同测试点默认保留。"""
    case_point = _test_point_id(case)
    existing_point = _test_point_id(existing)
    if case_point and existing_point and case_point != existing_point:
        return False
    if case_point and case_point == existing_point and not _same_expected(case, existing):
        return False
    return scenario_signature(case) == scenario_signature(existing)


def run_quality_gate(
    cases: list,
    existing_signatures: set[str] | None = None,
    *,
    drop_low_score: bool = False,
    score_threshold: int = 40,
) -> GateResult:
    """硬门槛 → 分项评分 → 结构化去重。低分默认保留为待复核。"""
    linter = UiLanguageLinter()
    dropReasons = {
        "no_steps": 0,
        "no_expected": 0,
        "compound_expected": 0,
        "multiple_inputs": 0,
        "unknown_step_type": 0,
        "no_assertion": 0,
        "low_score": 0,
        "duplicate": 0,
    }
    scored: list = []
    needs_review = 0
    external_signatures = set(existing_signatures or [])

    for case in cases:
        blocking = hard_gate_issues(case)
        if blocking:
            code = blocking[0][0]
            dropReasons[code] = dropReasons.get(code, 0) + 1
            continue

        lint_issues = linter.lint(case)
        detailed = score_case_detailed(case, warnings=lint_issues)
        case.quality_score = detailed["score"]
        case.quality_breakdown = detailed
        case.lint_issues = list(lint_issues)

        if detailed["score"] < score_threshold:
            if drop_low_score:
                dropReasons["low_score"] += 1
                continue
            needs_review += 1
            case.lint_issues.append(
                f"质量分低于 {score_threshold}，建议人工复核"
            )
        scored.append(case)

    kept: list = []
    seen = set(external_signatures)
    near_duplicate = 0
    for case in sorted(scored, key=lambda item: item.quality_score, reverse=True):
        signature = scenario_signature(case)
        if signature in seen:
            dropReasons["duplicate"] += 1
            continue
        if any(_is_duplicate(case, existing) for existing in kept):
            dropReasons["duplicate"] += 1
            continue
        for existing in kept:
            if is_near_duplicate(case, existing):
                case.lint_issues.append(
                    f"近似重复用例：与「{existing.title}」高度相似，建议复核"
                )
                near_duplicate += 1
                break
        seen.add(signature)
        kept.append(case)

    return GateResult(
        generated=len(cases),
        kept=kept,
        dropped=len(cases) - len(kept),
        dropReasons=dropReasons,
        needs_review=needs_review,
        near_duplicate=near_duplicate,
    )
