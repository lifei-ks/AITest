"""独立审核 Agent 的确定性检查层。

只做能由源码事实直接判断的检查：页面、字段、选择器、参数、变量、
断言锚点、状态流转和步骤顺序。语义覆盖留给 LLM 审核层。
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from app.agent.quality_gate import (
    hard_gate_issues,
    quoted_anchors,
    score_case_detailed,
)

VARIABLE_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?:[+-]\d+)?\}")
VARIABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
COUNT_ASSERT_RE = re.compile(r"(?:共|总数(?:变为|为)?|数量(?:变为|为)?)\s*\d+\s*(?:条|个|项|行)")
TITLE_NOISE_RE = re.compile(r"[\s，。！？、：；（）()\[\]【】《》“”\"'·,.;:!?/\\|_\-]+")
SUBMIT_WORDS = ("提交", "保存", "创建", "新增", "确认", "添加", "注册", "登录")
STATE_TRIGGER_HINTS = ("支付", "发货", "完成", "取消", "退款", "收货", "创建")


def _issue(
    code: str,
    severity: str,
    problem: str,
    reason: str,
    evidence: str = "",
    suggestion: str = "",
) -> dict:
    return {
        "code": code,
        "severity": severity,
        "problem": problem,
        "reason": reason,
        "evidence": evidence,
        "suggestion": suggestion,
    }


def _normalize_title(text) -> str:
    return TITLE_NOISE_RE.sub("", str(text or "")).lower()


def title_similarity(a, b) -> float:
    """标题相似度：去空白/标点后做序列相似度，不调用 LLM。"""
    return SequenceMatcher(None, _normalize_title(a), _normalize_title(b)).ratio()


def _case_title(case: dict) -> str:
    return str(case.get("title") or "")


def _case_route(case: dict) -> str:
    for step in _steps(case):
        selector = step.get("uiSelector") or {}
        if selector.get("type") == "route":
            return _normalize_route(selector.get("value"))
    return ""


def _case_inputs(case: dict) -> tuple[str, ...]:
    return tuple(
        str(step.get("inputValue") or "")
        for step in _steps(case)
        if step.get("type") == "input" and str(step.get("inputValue") or "")
    )


def _same_expected(a: dict, b: dict) -> bool:
    left = "|".join(sorted(_expected(a)))
    right = "|".join(sorted(_expected(b)))
    return left == right


def find_title_duplicates(
    case: dict,
    peers: list[dict] | None,
    *,
    threshold: float = 0.85,
    limit: int = 10,
) -> list[dict]:
    """用标题做低成本候选筛选，再用预期/输入/路由做一次免费确认。

    这里只返回“候选”，不删除任何用例；审核 Agent 把它作为 warning/info 展示。
    """
    case_id = str(case.get("id") or case.get("caseId") or "")
    case_title = _case_title(case)
    case_route = _case_route(case)
    case_inputs = _case_inputs(case)
    candidates: list[dict] = []
    for peer in peers or []:
        peer_id = str(peer.get("id") or peer.get("caseId") or "")
        if peer_id and case_id and peer_id == case_id:
            continue
        peer_title = _case_title(peer)
        if not case_title or not peer_title:
            continue
        similarity = title_similarity(case_title, peer_title)
        if similarity < threshold:
            continue
        same_route = case_route and case_route == _case_route(peer)
        same_expected = _same_expected(case, peer)
        same_inputs = case_inputs == _case_inputs(peer)
        if case_inputs != _case_inputs(peer):
            verdict = "different_scenario"
            reason = "标题相似，但输入值不同，属于不同场景"
        elif same_expected and (same_route or not case_route):
            verdict = "duplicate_candidate"
            reason = "标题、输入值、预期结果相同，疑似重复"
        elif same_expected:
            verdict = "duplicate_candidate"
            reason = "标题、输入值、预期结果相同，疑似重复"
        else:
            verdict = "similar_title"
            reason = "标题相似，但预期结果不同"
        candidates.append({
            "caseId": peer_id,
            "title": peer_title,
            "similarity": round(similarity, 4),
            "sameRoute": bool(same_route),
            "sameExpected": same_expected,
            "sameInputs": same_inputs,
            "verdict": verdict,
            "reason": reason,
        })
    candidates.sort(key=lambda item: item["similarity"], reverse=True)
    return candidates[:limit]


def dedup_issues(case: dict, peers: list[dict] | None) -> list[dict]:
    """把标题重复候选转成审核问题，不删除用例。"""
    issues: list[dict] = []
    for candidate in find_title_duplicates(case, peers):
        if candidate["verdict"] == "duplicate_candidate":
            issues.append(
                _issue(
                    "NEAR_DUPLICATE",
                    "warning",
                    f"疑似重复用例：与「{candidate['title']}」高度相似",
                    candidate["reason"] + f"（相似度 {candidate['similarity']}）",
                    f"case:{candidate['caseId']}",
                    "人工确认是否合并或删除；审核只标注，不自动删除",
                )
            )
        elif candidate["verdict"] == "similar_title":
            issues.append(
                _issue(
                    "TITLE_SIMILAR",
                    "info",
                    f"标题相似：与「{candidate['title']}」",
                    candidate["reason"] + f"（相似度 {candidate['similarity']}）",
                    f"case:{candidate['caseId']}",
                    "确认是否为同场景的不同预期",
                )
            )
    return issues


def _check(code: str, status: str, detail: str) -> dict:
    return {"code": code, "status": status, "detail": detail}


def _normalize_route(route) -> str:
    value = str(route or "").strip().rstrip("/")
    return value or "/"


def _steps(case: dict) -> list[dict]:
    value = case.get("structuredSteps") or []
    return [step for step in value if isinstance(step, dict)]


def _expected(case: dict) -> list[str]:
    value = case.get("expectedResults") or []
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [str(item) for item in value if str(item).strip()] if isinstance(value, (list, tuple)) else []


def _iter_fields(pages: list[dict]):
    for page in pages or []:
        for form in page.get("forms") or []:
            for field in form.get("fields") or []:
                if isinstance(field, dict):
                    yield page, field


def _field_keys(field: dict) -> list[str]:
    return [
        str(field.get(key) or "").strip()
        for key in ("prop", "label", "placeholder")
        if str(field.get(key) or "").strip()
    ]


def _field_lookup(facts: dict) -> dict[str, dict]:
    lookup: dict[str, dict] = {}
    for _page, field in _iter_fields(facts.get("pages") or []):
        for key in _field_keys(field):
            lookup[key] = field
    return lookup


def _page_routes(facts: dict) -> set[str]:
    return {
        _normalize_route(page.get("route"))
        for page in facts.get("pages") or []
        if page.get("route")
    }


def _text_corpus(facts: dict) -> str:
    parts: list[str] = []
    for page in facts.get("pages") or []:
        parts.append(str(page.get("name") or ""))
        parts.append(str(page.get("route") or ""))
        for _page, field in _iter_fields([page]):
            parts.extend(_field_keys(field))
    parts.extend(str(item.get("value") or "") for item in facts.get("selectors") or [])
    parts.extend(str(item) for item in facts.get("uiTexts") or [])
    parts.append(str((facts.get("requirement") or {}).get("excerpt") or ""))
    point = facts.get("testPoint") or {}
    parts.extend(str(point.get(key) or "") for key in ("title", "description", "expected"))
    return "\n".join(parts)


def _constraints(facts: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for entity in facts.get("entities") or []:
        for constraint in entity.get("fieldConstraints") or []:
            field = str(constraint.get("field") or "").strip()
            rule = str(constraint.get("rule") or "").strip().lower()
            if field and rule:
                out.setdefault(field, []).append({"rule": rule, "value": constraint.get("value")})
    for _page, field in _iter_fields(facts.get("pages") or []):
        key = str(field.get("prop") or field.get("label") or "").strip()
        for rule in field.get("rules") or []:
            if isinstance(rule, dict) and rule.get("rule"):
                out.setdefault(key, []).append(
                    {"rule": str(rule.get("rule")).lower(), "value": rule.get("value")}
                )
    return out


def _required_fields(facts: dict, route: str) -> list[dict]:
    wanted = _normalize_route(route)
    for page in facts.get("pages") or []:
        if _normalize_route(page.get("route")) != wanted:
            continue
        return [
            field for _page, field in _iter_fields([page])
            if field.get("required")
        ]
    return []


def _is_form_submit(steps: list[dict]) -> bool:
    text = " ".join(
        f"{step.get('action') or ''} {step.get('target') or ''}"
        for step in steps
    )
    return any(word in text for word in SUBMIT_WORDS)


def _constraint_violation(field: dict, value: str, constraints: dict[str, list[dict]]) -> list[str]:
    issues: list[str] = []
    keys = _field_keys(field)
    rules: list[dict] = []
    for key in keys:
        rules.extend(constraints.get(key) or [])
    for key in ("maxLength", "maxlength"):
        if field.get(key) is not None:
            rules.append({"rule": "size", "value": field.get(key)})
    for item in rules:
        rule = str(item.get("rule") or "").lower()
        limit = item.get("value")
        try:
            if rule in ("size", "maxlength") and limit is not None and len(value) > int(limit):
                issues.append(f"长度 {len(value)} 超过上限 {limit}")
            elif rule == "min" and limit is not None and value.isdigit() and int(value) < int(limit):
                issues.append(f"数值 {value} 小于下限 {limit}")
            elif rule == "max" and limit is not None and value.isdigit() and int(value) > int(limit):
                issues.append(f"数值 {value} 超过上限 {limit}")
            elif rule == "pattern" and limit:
                if not re.fullmatch(str(limit), value):
                    issues.append(f"值 {value!r} 不匹配格式 {limit}")
            elif rule in ("notblank", "notnull", "notempty") and not value.strip():
                issues.append("必填值不能为空")
        except (TypeError, ValueError, re.error):
            continue
    return issues


def review_case_steps(
    case: dict,
    facts: dict,
    peers: list[dict] | None = None,
) -> dict:
    """对一条用例做逐步确定性检查，返回结构化审核结果。"""
    steps = _steps(case)
    expected = _expected(case)
    case_type = str(case.get("type") or "").lower()
    case_issues: list[dict] = []
    step_results: list[dict] = []
    variables: set[str] = set()
    current_route = ""
    pages = facts.get("pages") or []
    routes = _page_routes(facts)
    fields = _field_lookup(facts)
    corpus = _text_corpus(facts)
    constraints = _constraints(facts)
    field_keys = set(fields)
    input_covered: set[str] = set()

    for code, message in hard_gate_issues(case):
        case_issues.append(
            _issue(code, "blocker", message, message, evidence="用例结构")
        )

    for index, step in enumerate(steps, 1):
        step_type = str(step.get("type") or "")
        selector = step.get("uiSelector") or {}
        selector_type = str(selector.get("type") or "")
        selector_value = str(selector.get("value") or "").strip()
        action = str(step.get("action") or "")
        target = str(step.get("target") or "")
        checks: list[dict] = []
        step_issues: list[dict] = []

        def add(
            code: str,
            status: str,
            detail: str,
            severity: str | None = None,
            problem: str | None = None,
            reason: str | None = None,
            evidence: str = "",
            suggestion: str = "",
        ) -> None:
            checks.append(_check(code, status, detail))
            if severity:
                step_issues.append(
                    _issue(
                        code,
                        severity,
                        problem or detail,
                        reason or detail,
                        evidence,
                        suggestion,
                    )
                )

        if index == 1:
            ok = step_type == "ui_action" and selector_type == "route"
            add(
                "FIRST_STEP_ROUTE",
                "pass" if ok else "fail",
                "第一步是导航" if ok else "第一步不是 ui_action + route",
                None if ok else "blocker",
                None if ok else "用例第一步不是导航",
                None if ok else "执行器依赖首步导航，否则后续选择器没有页面上下文",
                "structuredSteps[0]",
                "把首步改成 ui_action + route",
            )

        if step_type not in ("ui_action", "input", "state_assert", "capture"):
            add(
                "STEP_TYPE",
                "fail",
                f"不支持的步骤类型：{step_type}",
                "blocker",
                "步骤类型不合法",
                f"{step_type} 不在执行器支持的步骤类型内",
                f"step[{index}]",
                "改为 ui_action/input/capture/state_assert",
            )

        if step_type == "ui_action" and selector_type == "route":
            route = _normalize_route(selector_value)
            exists = route in routes
            add(
                "ROUTE_EXISTS",
                "pass" if exists else "fail",
                f"路由 {route}" if exists else f"路由不存在：{route}",
                None if exists else "blocker",
                None if exists else "起始路由不在源码事实中",
                None if exists else f"{route} 不在页面路由列表中",
                f"pages: {', '.join(sorted(routes))}",
                "改用源码事实中的路由",
            )
            current_route = route

        elif step_type == "input":
            field = fields.get(selector_value) or fields.get(target)
            if field is None:
                add(
                    "FIELD_EXISTS",
                    "fail",
                    f"字段不存在：{selector_value or target}",
                    "blocker",
                    "输入字段不在源码事实中",
                    f"{selector_value or target} 没有匹配到页面字段",
                    "pages.forms.fields",
                    "改用真实 placeholder/label/prop",
                )
            else:
                add("FIELD_EXISTS", "pass", f"命中字段：{selector_value or target}")
                input_covered.update(_field_keys(field))
                value = str(step.get("inputValue") or "")
                if not value:
                    add(
                        "INPUT_VALUE",
                        "fail",
                        "input 缺少 inputValue",
                        "blocker",
                        "输入步骤缺少参数",
                        "inputValue 为空，执行器无法填充",
                        f"step[{index}].inputValue",
                        "补充输入值",
                    )
                elif case_type in ("positive", "functional"):
                    violations = _constraint_violation(field, value, constraints)
                    for violation in violations:
                        add(
                            "VALUE_CONSTRAINT",
                            "fail",
                            violation,
                            "blocker",
                            "输入值不满足字段约束",
                            violation,
                            f"field:{selector_value or target}",
                            "调整输入值或拆成边界用例",
                        )
                    if not violations:
                        add("VALUE_CONSTRAINT", "pass", "输入值满足已知约束")

        elif step_type == "capture":
            variable = str(step.get("variable") or "").strip()
            pattern = str(step.get("pattern") or "").strip()
            if not VARIABLE_NAME_RE.fullmatch(variable):
                add(
                    "CAPTURE_VARIABLE",
                    "fail",
                    "capture 缺少合法 variable",
                    "blocker",
                    "capture 变量名非法",
                    f"variable={variable!r} 不匹配变量名规则",
                    f"step[{index}]",
                    "使用字母或下划线开头的变量名",
                )
            elif variable in variables:
                add(
                    "CAPTURE_VARIABLE",
                    "fail",
                    f"变量重复定义：{variable}",
                    "blocker",
                    "capture 变量重复定义",
                    f"{variable} 已在之前的步骤定义",
                    f"step[{index}]",
                    "换一个变量名或删除重复步骤",
                )
            else:
                variables.add(variable)
                add("CAPTURE_VARIABLE", "pass", f"变量 {variable} 已定义")
            if not pattern:
                add(
                    "CAPTURE_PATTERN",
                    "fail",
                    "capture 缺少 pattern",
                    "blocker",
                    "capture 缺少提取规则",
                    "pattern 为空，执行器无法提取变量",
                    f"step[{index}]",
                    "补充正则 pattern",
                )
            else:
                try:
                    regex = re.compile(pattern)
                    if regex.groups < 1:
                        add(
                            "CAPTURE_PATTERN",
                            "fail",
                            "pattern 没有捕获组",
                            "blocker",
                            "capture 正则没有捕获组",
                            "执行器需要 group(1) 作为变量值",
                            f"pattern={pattern}",
                            "在 pattern 中加入 (...) ",
                        )
                    else:
                        add("CAPTURE_PATTERN", "pass", "pattern 含捕获组")
                except re.error as exc:
                    add(
                        "CAPTURE_PATTERN",
                        "fail",
                        f"pattern 非法：{exc}",
                        "blocker",
                        "capture 正则非法",
                        str(exc),
                        f"pattern={pattern}",
                        "修正正则表达式",
                    )

        elif step_type == "state_assert":
            raw = str(step.get("expected") or "")
            anchors = quoted_anchors(raw)
            if not anchors:
                add(
                    "ASSERT_ANCHOR",
                    "fail",
                    "断言缺少「」锚点",
                    "blocker",
                    "断言没有可验证锚点",
                    "state_assert.expected 缺少「」文案或 URL",
                    f"step[{index}].expected",
                    "补充可观察的「」文案锚点",
                )
            elif len(anchors) > 1:
                add(
                    "ASSERT_ANCHOR",
                    "fail",
                    f"断言包含多个锚点：{len(anchors)}",
                    "blocker",
                    "一条断言包含多个结果",
                    "执行器一次只能验证一个锚点",
                    f"step[{index}].expected",
                    "拆成多条断言或用例",
                )
            else:
                anchor = anchors[0]
                if "${" in anchor:
                    for name in VARIABLE_REF_RE.findall(anchor):
                        if name not in variables:
                            add(
                                "VARIABLE_UNDEFINED",
                                "fail",
                                f"使用了未捕获变量：{name}",
                                "blocker",
                                "断言引用了未定义变量",
                                f"{name} 没有在之前的 capture 步骤定义",
                                f"step[{index}].expected",
                                "增加 capture 步骤或修正变量名",
                            )
                elif anchor not in corpus:
                    add(
                        "ANCHOR_IN_FACTS",
                        "warning",
                        f"锚点不在源码事实中：{anchor}",
                        "warning",
                        "断言文案不在源码/需求事实中",
                        f"{anchor} 没有在 uiTexts、需求或测试点中找到",
                        "uiTexts + requirement.excerpt",
                        "确认文案是否真实存在，必要时补充源码事实",
                    )
                else:
                    add("ANCHOR_IN_FACTS", "pass", f"锚点命中：{anchor}")
                if COUNT_ASSERT_RE.search(raw) and not any(
                    s.get("type") == "capture" for s in steps[:index - 1]
                ):
                    add(
                        "FIXED_COUNT_BASELINE",
                        "warning",
                        "固定总数断言没有运行时基线",
                        "warning",
                        "固定总数依赖共享数据",
                        "没有 capture 基线，数据变化时断言会误判",
                        f"step[{index}].expected",
                        "改为 capture + 基线+1，或断言唯一行标识",
                    )

        for field in ("expected", "inputValue"):
            # state_assert 的 expected 已在上面的锚点分支校验过变量，避免重复报同一问题。
            if step_type == "state_assert" and field == "expected":
                continue
            raw = str(step.get(field) or "")
            for name in VARIABLE_REF_RE.findall(raw):
                if name not in variables:
                    add(
                        "VARIABLE_UNDEFINED",
                        "fail",
                        f"使用了未捕获变量：{name}",
                        "blocker",
                        "参数引用了未定义变量",
                        f"{name} 没有在之前的 capture 步骤定义",
                        f"step[{index}].{field}",
                        "增加 capture 步骤或修正变量名",
                    )

        if step_type in ("ui_action", "input") and current_route and selector_type not in ("route",):
            page = next(
                (p for p in pages if _normalize_route(p.get("route")) == current_route),
                None,
            )
            if page is not None and selector_type == "text" and selector_value:
                if selector_value not in corpus:
                    add(
                        "TEXT_IN_FACTS",
                        "warning",
                        f"按钮/文案不在事实中：{selector_value}",
                        "warning",
                        "按钮文案可能编造",
                        f"{selector_value} 没有在 uiTexts/需求中找到",
                        f"page:{current_route}",
                        "改用源码事实中的按钮文案",
                    )

        if step_issues:
            step_verdict = "fail" if any(i["severity"] == "blocker" for i in step_issues) else "warning"
        elif any(c["status"] == "fail" for c in checks):
            step_verdict = "fail"
        elif any(c["status"] == "warning" for c in checks):
            step_verdict = "warning"
        else:
            step_verdict = "pass"
        step_results.append(
            {
                "stepIndex": index,
                "stepType": step_type,
                "action": action,
                "verdict": step_verdict,
                "checks": checks,
                "issues": step_issues,
            }
        )
        case_issues.extend(step_issues)

    if case_type in ("positive", "functional") and _is_form_submit(steps):
        route = next(
            (
                _normalize_route((step.get("uiSelector") or {}).get("value"))
                for step in steps
                if (step.get("uiSelector") or {}).get("type") == "route"
            ),
            "",
        )
        for field in _required_fields(facts, route):
            keys = set(_field_keys(field))
            if keys and not keys.intersection(input_covered):
                label = str(field.get("label") or field.get("prop") or "必填字段")
                case_issues.append(
                    _issue(
                        "REQUIRED_FIELD_MISSING",
                        "warning",
                        f"必填字段未填写：{label}",
                        "正向表单用例提交前必须填齐必填字段",
                        f"field:{label}",
                        "补充该字段的 input 步骤",
                    )
                )

    for key in (case.get("testData") or {}):
        normalized = str(key).strip()
        if normalized and normalized not in field_keys and normalized not in corpus:
            case_issues.append(
                _issue(
                    "TEST_DATA_FIELD",
                    "warning",
                    f"testData 字段不在源码事实中：{normalized}",
                    "测试数据 key 无法对应到页面字段",
                    "pages.forms.fields",
                    "改用真实字段 prop/label",
                )
            )

    # 标题低成本去重：只标注候选，不删除用例。
    case_issues.extend(dedup_issues(case, peers))

    # 去重：同 code + problem 只保留一条
    unique: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for issue in case_issues:
        key = (str(issue.get("code")), str(issue.get("problem")))
        if key in seen:
            continue
        seen.add(key)
        unique.append(issue)
    case_issues = unique

    blocking = [i for i in case_issues if i["severity"] == "blocker"]
    warnings = [i for i in case_issues if i["severity"] == "warning"]
    verdict = "reject" if blocking else "revise" if warnings else "pass"
    detailed = score_case_detailed(case, warnings=[i["problem"] for i in warnings])
    return {
        "caseId": case.get("id"),
        "verdict": verdict,
        "score": detailed["score"],
        "qualityBreakdown": detailed,
        "issues": case_issues,
        "stepResults": step_results,
        "blocking": blocking,
        "warnings": warnings,
        "duplicateCandidates": find_title_duplicates(case, peers),
    }


def _issue_key(issue: dict) -> str:
    code = str(issue.get("code") or "").strip()
    problem = re.sub(r"\s+", "", str(issue.get("problem") or ""))
    return code or problem


def merge_review(deterministic: dict, llm: dict | None) -> dict:
    """确定性结论优先；LLM 只追加去重后的新问题。"""
    det_issues = list(deterministic.get("issues") or [])
    llm_issues = list((llm or {}).get("issues") or [])
    seen = {_issue_key(issue) for issue in det_issues}
    added: list[dict] = []
    for issue in llm_issues:
        key = _issue_key(issue)
        if key in seen:
            continue
        seen.add(key)
        added.append(issue)
    issues = det_issues + added

    det_blocking = [i for i in det_issues if i.get("severity") == "blocker"]
    det_warnings = [i for i in det_issues if i.get("severity") == "warning"]
    llm_verdict = str((llm or {}).get("verdict") or "").lower()
    if det_blocking or llm_verdict == "fail":
        verdict = "reject"
    elif det_warnings or llm_verdict == "suspect":
        verdict = "revise"
    else:
        verdict = "pass"

    score = int(deterministic.get("score") or 0)
    for issue in added:
        score -= 25 if issue.get("severity") == "blocker" else 8 if issue.get("severity") == "warning" else 2
    if det_blocking or llm_verdict == "fail":
        score = min(score, 30)
    score = max(0, min(100, score))

    step_results = list(deterministic.get("stepResults") or [])
    llm_steps = (llm or {}).get("stepResults") or []
    by_index = {int(item.get("stepIndex") or 0): item for item in step_results}
    for item in llm_steps:
        index = int(item.get("stepIndex") or 0)
        if index not in by_index:
            step_results.append(item)
            continue
        target = by_index[index]
        existing = {_issue_key(issue) for issue in target.get("issues") or []}
        for issue in item.get("issues") or []:
            if _issue_key(issue) not in existing:
                target.setdefault("issues", []).append(issue)
                existing.add(_issue_key(issue))
        if any(issue.get("severity") == "blocker" for issue in target.get("issues") or []):
            target["verdict"] = "fail"
        elif any(issue.get("severity") == "warning" for issue in target.get("issues") or []):
            target["verdict"] = "warning"

    reason = str((llm or {}).get("reason") or "")
    if not reason:
        reason = (
            "确定性检查发现阻断问题" if det_blocking
            else "确定性检查发现需要复核的问题" if det_warnings
            else "确定性检查和 AI 语义审核均未发现问题"
        )
    return {
        "caseId": deterministic.get("caseId"),
        "verdict": verdict,
        "score": score,
        "qualityBreakdown": deterministic.get("qualityBreakdown") or {},
        "issues": issues,
        "stepResults": step_results,
        "reason": reason,
        "deterministic": deterministic,
        "llmVerdict": llm_verdict or None,
        "duplicateCandidates": deterministic.get("duplicateCandidates") or [],
    }
