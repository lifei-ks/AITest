"""AI 质检与修复生成用例：文档 + 源码事实 → 问题/补丁 → 规则校验 → 落版本。"""
from __future__ import annotations

import json
import re
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent import case_review_agent
from app.analyzer.models import to_json_dict
from app.common.errors import BizError
from app.entity.models import Project, Requirement, RequirementCase, TestCase, TestPoint
from app.llm.client import LlmClient, LlmUnavailable
from app.service import settings_service
from app.service.coverage_service import resolve_coverage_refs
from app.service.generation_service import _as_text_list, case_to_dict, get_generation_context

MAX_REVIEW_CASES = 20
SELECTOR_TYPES = {"route", "placeholder", "text", "label", "role", "testid", "css", "xpath"}
ANCHOR_RE = re.compile(r"[「\"“]([^「\"”]{1,120})[」\"”]")


def _case_or_404(session: Session, pid: str, cid: str) -> TestCase:
    tc = session.get(TestCase, cid)
    if tc is None or tc.project_id != pid:
        raise BizError(40402, f"用例不存在: {cid}", http_status=404)
    return tc


def _requirement_point(session: Session, cid: str) -> tuple[Requirement | None, TestPoint | None]:
    mapping = session.scalars(
        select(RequirementCase).where(RequirementCase.test_case_id == cid)
    ).first()
    if mapping is None:
        return None, None
    requirement = session.get(Requirement, mapping.requirement_id)
    point = session.get(TestPoint, mapping.test_point_id) if mapping.test_point_id else None
    return requirement, point


def _source_facts(session: Session, pid: str, requirement: Requirement | None, point: TestPoint | None) -> dict:
    backend, frontend, machines = get_generation_context(session, pid)
    backend_dict = to_json_dict(backend)
    frontend_dict = to_json_dict(frontend)
    return {
        "requirement": {
            "version": requirement.version if requirement else "",
            "name": requirement.name if requirement else "",
            "excerpt": (requirement.content or "")[:12000] if requirement else "",
        },
        "testPoint": {
            "id": point.id if point else "",
            "title": point.title if point else "",
            "category": point.category if point else "",
            "priority": point.priority if point else "",
            "description": point.description if point else "",
            "pageRoute": point.page_route if point else "",
            "endpoint": point.endpoint if point else "",
            "fields": json.loads(point.fields) if point and point.fields else [],
            "expected": point.expected if point else "",
        },
        "pages": frontend_dict.get("pages", []),
        "selectors": frontend_dict.get("selectors", []),
        "uiTexts": frontend_dict.get("uiTexts", []),
        "endpoints": backend_dict.get("endpoints", []),
        "entities": backend_dict.get("entities", []),
        "businessRules": backend_dict.get("businessRules", []),
        "enums": backend_dict.get("enums", []),
        "stateMachines": machines,
    }, backend_dict, frontend_dict, machines


def _unavailable_result(case: dict, exc: Exception, model: str) -> dict:
    return {
        "caseId": case.get("id"),
        "title": case.get("title"),
        "verdict": "unavailable",
        "reason": f"AI 质检不可用：{exc}"[:1200],
        "issues": [],
        "model": model,
    }


def review_cases(session: Session, pid: str, case_ids: list[str]) -> dict:
    ids = list(dict.fromkeys(item for item in (case_ids or []) if item))
    if not ids:
        raise BizError(40025, "请先选择要 AI 质检的用例")
    if len(ids) > MAX_REVIEW_CASES:
        raise BizError(40025, f"单次最多质检 {MAX_REVIEW_CASES} 条用例")

    client = settings_service.get_text_client()
    facts_cache: dict[str, tuple] = {}

    results = []
    for cid in ids:
        tc = _case_or_404(session, pid, cid)
        case = case_to_dict(tc)
        requirement, point = _requirement_point(session, cid)
        key = requirement.id if requirement else "__project__"
        if key not in facts_cache:
            facts_cache[key] = _source_facts(session, pid, requirement, point)
        facts, _backend, _frontend, _machines = facts_cache[key]
        case["requirement"] = facts["requirement"]
        case["testPoint"] = facts["testPoint"]

        if not client.available():
            results.append(_unavailable_result(case, LlmUnavailable("未配置 LLM_API_KEY"), client.model))
            continue
        try:
            # 先释放只读事务，避免长 LLM 调用期间 MySQL 连接空闲超时
            session.commit()
            review = case_review_agent.review_case(client, case, facts)
            results.append({
                "caseId": cid,
                "title": case.get("title"),
                **review,
                "model": client.model,
            })
        except LlmUnavailable as exc:
            results.append(_unavailable_result(case, exc, client.model))

    reviewed = sum(1 for item in results if item["verdict"] != "unavailable")
    problems = sum(1 for item in results if item["verdict"] in ("suspect", "fail"))
    return {
        "total": len(results),
        "reviewed": reviewed,
        "problems": problems,
        "results": results,
    }


def _clean_str(value, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _normalize_steps(raw) -> list[dict]:
    if not isinstance(raw, list):
        raise BizError(40026, "AI 修复的 structuredSteps 必须是数组")
    steps: list[dict] = []
    for item in raw[:15]:
        if not isinstance(item, dict):
            continue
        step_type = str(item.get("type") or "").strip()
        if step_type not in ("ui_action", "input", "state_assert", "capture"):
            continue
        selector = item.get("uiSelector") if isinstance(item.get("uiSelector"), dict) else {}
        steps.append({
            "order": len(steps) + 1,
            "type": step_type,
            "action": _clean_str(item.get("action"), 255),
            "target": _clean_str(item.get("target"), 255),
            "uiSelector": {
                "type": _clean_str(selector.get("type"), 30),
                "value": _clean_str(selector.get("value"), 500),
            },
            "expected": _clean_str(item.get("expected"), 500),
            "inputValue": _clean_str(item.get("inputValue"), 500),
            "variable": _clean_str(item.get("variable"), 60),
            "pattern": _clean_str(item.get("pattern"), 300),
        })
    if not steps:
        raise BizError(40026, "AI 修复没有产生有效的结构化步骤")
    return steps


def _normalize_patch(patch: dict) -> dict:
    out: dict = {}
    if "title" in patch:
        value = _clean_str(patch.get("title"), 255)
        if not value:
            raise BizError(40026, "AI 修复的标题不能为空")
        out["title"] = value
    if "module" in patch:
        out["module"] = _clean_str(patch.get("module"), 120)
    if "type" in patch:
        value = _clean_str(patch.get("type"), 20)
        if value not in ("positive", "negative", "boundary", "state"):
            raise BizError(40026, f"非法用例类型: {value}")
        out["type"] = value
    if "priority" in patch:
        value = _clean_str(patch.get("priority"), 10)
        if value not in ("P0", "P1", "P2"):
            raise BizError(40026, f"非法优先级: {value}")
        out["priority"] = value
    if "preconditions" in patch:
        if not isinstance(patch.get("preconditions"), list):
            raise BizError(40026, "preconditions 必须是数组")
        out["preconditions"] = [_clean_str(item, 500) for item in patch["preconditions"] if _clean_str(item, 500)]
    if "expectedResults" in patch:
        raw_expected = patch.get("expectedResults")
        if isinstance(raw_expected, str):
            # 兼容模型把数组写成单个字符串；不能逐字符拆开
            raw_expected = _as_text_list(raw_expected)
        if not isinstance(raw_expected, list):
            raise BizError(40026, "expectedResults 必须是数组")
        out["expected_results"] = [_clean_str(item, 500) for item in raw_expected if _clean_str(item, 500)]
        if not out["expected_results"]:
            raise BizError(40026, "expectedResults 不能为空")
    if "testData" in patch:
        if not isinstance(patch.get("testData"), dict):
            raise BizError(40026, "testData 必须是对象")
        out["test_data"] = patch["testData"]
    if "structuredSteps" in patch:
        out["structured_steps"] = _normalize_steps(patch.get("structuredSteps"))
    return out


def _validate_case(case: dict, frontend: dict, requirement_text: str, point: TestPoint | None) -> list[str]:
    errors: list[str] = []
    steps = case.get("structuredSteps") or []
    if not steps:
        return ["结构化步骤不能为空"]

    first = steps[0]
    first_selector = first.get("uiSelector") or {}
    if first.get("type") != "ui_action" or first_selector.get("type") != "route":
        errors.append("第一步必须是 ui_action + route")

    page_routes = {
        str(page.get("route") or "").strip()
        for page in frontend.get("pages", [])
        if page.get("route")
    }
    placeholders = {
        str(field.get("placeholder") or "").strip()
        for page in frontend.get("pages", [])
        for form in page.get("forms", [])
        for field in form.get("fields", [])
        if field.get("placeholder")
    }
    labels = {
        str(field.get("label") or "").strip()
        for page in frontend.get("pages", [])
        for form in page.get("forms", [])
        for field in form.get("fields", [])
        if field.get("label")
    }
    selector_values = {
        str(item.get("value") or "").strip()
        for item in frontend.get("selectors", [])
        if item.get("value")
    }
    ui_texts = [str(item) for item in (frontend.get("uiTexts") or [])]
    field_keys = {
        str(field.get(key) or "").strip()
        for page in frontend.get("pages", [])
        for form in page.get("forms", [])
        for field in form.get("fields", [])
        for key in ("prop", "label")
        if field.get(key)
    }
    point_text = " ".join([
        point.title if point else "",
        point.description or "" if point else "",
        point.expected or "" if point else "",
    ])
    text_corpus = "\n".join(ui_texts + [requirement_text, point_text] + list(selector_values))

    if first_selector.get("type") == "route" and first_selector.get("value") not in page_routes:
        errors.append(f"起始路由不存在: {first_selector.get('value')}")

    anchors: list[str] = []
    for index, step in enumerate(steps, 1):
        step_type = step.get("type")
        selector = step.get("uiSelector") or {}
        selector_type = selector.get("type")
        selector_value = str(selector.get("value") or "").strip()
        if step_type not in ("ui_action", "input", "state_assert", "capture"):
            errors.append(f"步骤 {index} 类型非法: {step_type}")
            continue
        if step_type in ("ui_action", "input") and not selector_value:
            errors.append(f"步骤 {index} 缺少 uiSelector.value")
        if step_type == "input" and not str(step.get("inputValue") or "").strip():
            errors.append(f"步骤 {index} 缺少 inputValue")
        if step_type == "capture":
            variable = str(step.get("variable") or "").strip()
            pattern = str(step.get("pattern") or "").strip()
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", variable or ""):
                errors.append(f"步骤 {index} capture 缺少合法 variable")
            if not pattern:
                errors.append(f"步骤 {index} capture 缺少 pattern")
            else:
                try:
                    compiled = re.compile(pattern)
                    if compiled.groups < 1:
                        errors.append(f"步骤 {index} capture pattern 必须包含捕获组")
                except re.error as exc:
                    errors.append(f"步骤 {index} capture pattern 非法: {exc}")
        if step_type == "state_assert":
            expected = str(step.get("expected") or "").strip()
            if not expected:
                errors.append(f"步骤 {index} 缺少断言 expected")
            anchors.extend(ANCHOR_RE.findall(expected))

        # state_assert 由期望锚点断言；capture 由 body 文本 + pattern 提取，不依赖 uiSelector。
        if step_type not in ("state_assert", "capture"):
            if selector_type == "route" and selector_value and selector_value not in page_routes:
                errors.append(f"步骤 {index} 路由不存在: {selector_value}")
            if selector_type == "placeholder" and selector_value not in placeholders:
                errors.append(f"步骤 {index} placeholder 不在源码事实中: {selector_value}")
            if selector_type == "label" and selector_value not in labels:
                errors.append(f"步骤 {index} label 不在源码事实中: {selector_value}")
            if selector_type == "text" and selector_value and selector_value not in text_corpus:
                errors.append(f"步骤 {index} 按钮文案不在源码/需求事实中: {selector_value}")
            if selector_type in ("role", "testid", "css", "xpath") and selector_value not in selector_values:
                errors.append(f"步骤 {index} 选择器不在源码事实中: {selector_value}")

    step_anchors: list[str] = []
    for index, step in enumerate(steps, 1):
        if step.get("type") != "state_assert":
            continue
        anchors_for_step = ANCHOR_RE.findall(str(step.get("expected") or ""))
        if not anchors_for_step:
            errors.append(f"步骤 {index} 缺少「」文案锚点")
        step_anchors.extend(anchors_for_step)
    if not anchors:
        errors.append("没有可观察的「」文案锚点")
    for anchor in step_anchors or anchors:
        # 运行时变量锚点（如「共 ${base_count+1} 条订单」）无法在源码事实中静态匹配
        if "${" in anchor:
            continue
        if anchor not in text_corpus:
            errors.append(f"断言锚点不在源码/需求事实中: {anchor}")

    # 运行时变量：必须由此前 capture 步骤定义；否则只能在执行期报错。
    available_vars: set[str] = set()
    for index, step in enumerate(steps, 1):
        step_type = step.get("type")
        if step_type == "capture":
            variable = str(step.get("variable") or "").strip()
            if variable and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", variable):
                if variable in available_vars:
                    errors.append(f"步骤 {index} 重复定义变量: {variable}")
                available_vars.add(variable)
            continue
        for field in ("expected", "inputValue"):
            raw = str(step.get(field) or "")
            for name in re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?:[+-]\d+)?\}", raw):
                if name not in available_vars:
                    errors.append(f"步骤 {index} 使用了未捕获变量: {name}")

    test_data = case.get("testData") or {}
    if isinstance(test_data, dict):
        for key in test_data:
            normalized_key = str(key).strip()
            if normalized_key and normalized_key not in field_keys and normalized_key not in text_corpus:
                errors.append(f"testData 字段不在源码/需求事实中: {normalized_key}")
    return errors


def repair_case(
    session: Session,
    pid: str,
    cid: str,
    issues: list[dict] | None = None,
    client: LlmClient | None = None,
) -> dict:
    tc = _case_or_404(session, pid, cid)
    case = case_to_dict(tc)
    requirement, point = _requirement_point(session, cid)
    facts, backend, frontend, machines = _source_facts(session, pid, requirement, point)
    case["requirement"] = facts["requirement"]
    case["testPoint"] = facts["testPoint"]

    clean_issues = []
    for item in (issues or [])[:12]:
        if isinstance(item, dict) and _clean_str(item.get("problem"), 1000):
            clean_issues.append({
                "severity": _clean_str(item.get("severity"), 20) or "warning",
                "field": _clean_str(item.get("field"), 60) or "case",
                "problem": _clean_str(item.get("problem"), 1000),
                "suggestion": _clean_str(item.get("suggestion"), 1000),
                "evidence": _clean_str(item.get("evidence"), 500),
            })

    if client is None:
        client = settings_service.get_text_client()
    if not client.available():
        raise BizError(40022, "未配置 LLM_API_KEY，无法进行 AI 修复")
    # 修复可能持续数十秒；先释放只读事务连接，避免 MySQL 空闲超时后写回失败
    session.commit()
    try:
        repair = case_review_agent.repair_case(client, case, facts, clean_issues)
    except LlmUnavailable as exc:
        raise BizError(40027, f"AI 修复失败: {exc}") from exc

    try:
        patch = _normalize_patch(repair["patch"])
    except BizError:
        raise
    except Exception as exc:
        raise BizError(40027, f"AI 修复补丁非法: {exc}") from exc

    merged = {
        **case,
        **patch,
        "expectedResults": patch.get("expected_results", case.get("expectedResults", [])),
        "testData": patch.get("test_data", case.get("testData", {})),
        "structuredSteps": patch.get("structured_steps", case.get("structuredSteps", [])),
    }
    if "structured_steps" in patch:
        merged["steps"] = [
            step.get("action", "")
            for step in patch["structured_steps"]
            if isinstance(step, dict) and step.get("action")
        ]
    errors = _validate_case(merged, frontend, facts["requirement"].get("excerpt") or "", point)
    if errors:
        raise BizError(40028, "AI 补丁未通过源码事实校验: " + "；".join(errors[:6]))

    point_dict = None
    if point is not None:
        point_dict = {
            "id": point.id,
            "title": point.title,
            "category": point.category,
            "priority": point.priority,
            "description": point.description or "",
            "pageRoute": point.page_route or "",
            "endpoint": point.endpoint or "",
            "fields": json.loads(point.fields) if point.fields else [],
            "expected": point.expected or "",
        }
    resolved_refs = resolve_coverage_refs(
        {
            "title": merged.get("title"),
            "module": merged.get("module"),
            "coverageRefs": merged.get("coverageRefs") or {},
            "steps": merged.get("steps") or [],
            "structuredSteps": merged.get("structuredSteps") or [],
            "testData": merged.get("testData") or {},
        },
        backend,
        machines,
        point_dict,
    )
    payload = {**patch, "coverage_refs": resolved_refs, "execution_status": "not_run"}
    if tc.source in ("rule", "ai", "import"):
        payload["source"] = "ai"

    from app.service.testcase_service import update_testcase

    updated = update_testcase(
        session,
        cid,
        payload,
        change_note=f"AI 修复：{repair['summary']}",
    )
    return {
        "summary": repair["summary"],
        "patch": patch,
        "testCase": updated,
    }
