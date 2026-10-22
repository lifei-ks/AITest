"""覆盖矩阵：规划覆盖 vs 已通过执行覆盖。

口径固定：
- 规划覆盖：用例的 coverageRefs 是否引用真实端点/已验证状态转换/业务规则。
- 执行覆盖：仅统计 status=passed 的执行记录，并优先读取执行瞬间的用例快照。
- refs 中不存在于最新代码事实的旧 ID 不计数，比例始终限制在 0~1。
"""
from __future__ import annotations

import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.entity.models import (
    CodeAnalysis,
    ExecutionRecord,
    ExecutionRequirement,
    RequirementCase,
    StateMachine,
    TestCase,
)


ACTION_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("create", ("创建", "新增", "新建", "提交", "保存", "create", "add", "save", "submit")),
    ("list", ("列表", "查询", "搜索", "筛选", "list", "query", "search")),
    ("pay", ("支付", "付款", "pay")),
    ("ship", ("发货", "配送", "ship", "deliver")),
    ("complete", ("完成", "确认收货", "complete", "finish")),
    ("cancel", ("取消", "cancel")),
    ("refund", ("退款", "refund")),
    ("update", ("修改", "编辑", "更新", "update", "edit")),
    ("delete", ("删除", "remove", "delete")),
    ("login", ("登录", "login", "signin")),
)

REF_KEYS = ("endpointIds", "transitionIds", "ruleIds", "testPointIds")


def _json(value, default):
    if not value:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _normalise_refs(value: dict | None) -> dict[str, list[str]]:
    raw = value or {}
    return {
        key: list(dict.fromkeys(str(item) for item in (raw.get(key) or []) if item))
        for key in REF_KEYS
    }


def _case_text(
    title: str = "",
    module: str = "",
    steps: list | None = None,
    test_data: dict | None = None,
    point: dict | None = None,
) -> str:
    parts = [title, module]
    for step in steps or []:
        if isinstance(step, dict):
            parts.extend(
                str(step.get(key) or "")
                for key in ("action", "target", "expected", "inputValue")
            )
            selector = step.get("uiSelector") or step.get("ui_selector") or {}
            if isinstance(selector, dict):
                parts.append(str(selector.get("value") or ""))
        else:
            parts.append(str(step or ""))
    if test_data:
        parts.extend(str(key) for key in test_data)
        parts.extend(str(value) for value in test_data.values())
    if point:
        parts.extend(
            str(point.get(key) or "")
            for key in ("title", "category", "description", "pageRoute", "endpoint", "expected")
        )
        parts.extend(str(item) for item in (point.get("fields") or []))
    return " ".join(parts).lower()


def _explicit_endpoint_ids(text: str, point: dict | None, endpoints: list[dict]) -> list[str]:
    del text
    if not point:
        return []
    raw = str(point.get("endpoint") or "").strip()
    if not raw:
        return []
    method = ""
    path = raw
    match = re.match(r"^([A-Za-z]+)\s+(.+)$", raw)
    if match:
        method, path = match.group(1).upper(), match.group(2).strip()
    matched = []
    for endpoint in endpoints:
        ep_path = str(endpoint.get("path") or "")
        if not path or path.lower() not in ep_path.lower():
            continue
        if method and method != str(endpoint.get("method") or "").upper():
            continue
        matched.append(str(endpoint.get("id")))
    return [item for item in matched if item]


def _infer_endpoint_ids(text: str, point: dict | None, endpoints: list[dict]) -> list[str]:
    explicit = _explicit_endpoint_ids(text, point, endpoints)
    if explicit:
        return explicit

    matched: list[str] = []
    for endpoint in endpoints:
        path = str(endpoint.get("path") or "").lower()
        if path and path in text:
            matched.append(str(endpoint.get("id") or ""))

    # 状态用例可能一次走过多条转换，因此逐个动作匹配并合并；同一动作命中
    # 多个端点时视为歧义并跳过，既支持一条用例覆盖多个接口，又不批量误报。
    for action, words in ACTION_ALIASES:
        if not any(word in text for word in words):
            continue
        action_matches = []
        for endpoint in endpoints:
            path = str(endpoint.get("path") or "").lower()
            method_name = str(endpoint.get("methodName") or endpoint.get("method_name") or "").lower()
            ep_text = f"{path} {method_name}"
            has_action = action in ep_text or any(
                word in ep_text for word in words if word.isascii()
            )
            if has_action:
                action_matches.append(str(endpoint.get("id") or ""))
        if len(action_matches) == 1:
            matched.append(action_matches[0])

    return [item for item in dict.fromkeys(matched) if item]


def _infer_rule_ids(text: str, point: dict | None, test_data: dict | None, rules: list[dict]) -> list[str]:
    matched: list[str] = []
    case_fields = {
        str(item).lower()
        for item in list(test_data or {}) + list((point or {}).get("fields") or [])
        if item
    }
    for rule in rules:
        rule_id = str(rule.get("id") or "")
        if not rule_id:
            continue
        description = str(rule.get("description") or "")
        guard = str(rule.get("guard") or "")
        rule_text = f"{description} {guard}".lower()
        if rule_id.lower() in text:
            matched.append(rule_id)
            continue
        if description and description.lower() in text:
            matched.append(rule_id)
            continue
        # 字段型规则：只使用真实业务字段，避免 order/status/id 等泛化 token
        # 把一条规则错误关联到所有用例。
        if any(field and field in rule_text and field in text for field in case_fields):
            matched.append(rule_id)
            continue
        # 状态型规则：规则描述中的动作词与用例动作一致才关联。
        for _action, words in ACTION_ALIASES:
            if any(word in rule_text for word in words) and any(word in text for word in words):
                matched.append(rule_id)
                break
    return list(dict.fromkeys(matched))


def resolve_coverage_refs(
    case: dict,
    backend: dict | None,
    machines: list[dict] | None,
    point: dict | None = None,
) -> dict[str, list[str]]:
    """推断并合并覆盖引用。

    显式 refs 永远优先保留；推断只补充能从代码事实得到高置信匹配的引用。
    """
    refs = _normalise_refs(case.get("coverageRefs") or case.get("coverage_refs") or {})
    backend = backend or {}
    endpoints = backend.get("endpoints", []) or []
    rules = backend.get("businessRules", backend.get("business_rules", [])) or []
    steps = case.get("structuredSteps") or case.get("structured_steps") or case.get("steps") or []
    test_data = case.get("testData") or case.get("test_data") or {}
    text = _case_text(
        title=str(case.get("title") or ""),
        module=str(case.get("module") or ""),
        steps=steps if isinstance(steps, list) else [],
        test_data=test_data if isinstance(test_data, dict) else {},
        point=point,
    )

    # 显式引用是人工/模型给出的精确事实，优先于动作词推断；否则多个 create
    # 端点会被同时误算为已覆盖。只有缺失时才按动作和字段补全。
    if not refs["endpointIds"]:
        refs["endpointIds"] = _infer_endpoint_ids(text, point, endpoints)
    if not refs["ruleIds"]:
        refs["ruleIds"] = _infer_rule_ids(text, point, test_data, rules)

    transition_ids = set(refs["transitionIds"])
    for machine in machines or []:
        for transition in machine.get("transitions", []) or []:
            if not transition.get("verified"):
                continue
            trigger = str(transition.get("trigger") or "")
            if trigger and trigger.lower() in text:
                transition_ids.add(str(transition.get("id") or ""))
    refs["transitionIds"] = [item for item in transition_ids if item]
    return refs


def _analysis(session: Session, pid: str) -> CodeAnalysis | None:
    return session.scalars(
        select(CodeAnalysis)
        .where(CodeAnalysis.project_id == pid)
        .order_by(CodeAnalysis.created_at.desc())
        .limit(1)
    ).first()


def _case_orm_dict(tc: TestCase) -> dict:
    return {
        "id": tc.id,
        "title": tc.title,
        "module": tc.module,
        "coverageRefs": _json(tc.coverage_refs, {}),
        "steps": _json(tc.steps, []),
        "structuredSteps": _json(tc.structured_steps, []),
        "testData": _json(tc.test_data, {}),
    }


def _requirement_case_ids(session: Session, pid: str, requirement_id: str) -> list[str]:
    if requirement_id == "__none__":
        project_ids = list(session.scalars(select(TestCase.id).where(TestCase.project_id == pid)))
        mapped = (
            set(session.scalars(
                select(RequirementCase.test_case_id).where(RequirementCase.test_case_id.in_(project_ids))
            ))
            if project_ids
            else set()
        )
        return [item for item in project_ids if item not in mapped]
    return list(
        session.scalars(
            select(RequirementCase.test_case_id).where(
                RequirementCase.requirement_id == requirement_id
            )
        )
    )


def coverage_matrix(session: Session, pid: str, requirement_id: str | None = None) -> dict:
    analysis = _analysis(session, pid)
    backend = _json(analysis.backend_result, {}) if analysis and analysis.backend_result else {}
    endpoints = backend.get("endpoints", []) or []
    rules = backend.get("businessRules", backend.get("business_rules", [])) or []

    machines = list(session.scalars(select(StateMachine).where(StateMachine.project_id == pid)))
    transitions = []
    machine_specs = []
    for machine in machines:
        verified = [item for item in _json(machine.transitions, []) if item.get("verified")]
        transitions.extend(verified)
        machine_specs.append({
            "id": machine.id,
            "name": machine.name,
            "states": _json(machine.states, []),
            "transitions": verified,
        })

    cases_stmt = select(TestCase).where(TestCase.project_id == pid)
    if requirement_id:
        case_ids = _requirement_case_ids(session, pid, requirement_id)
        cases_stmt = cases_stmt.where(TestCase.id.in_(case_ids or ["__none__"]))
    cases = list(session.scalars(cases_stmt))
    case_by_id = {case.id: case for case in cases}

    valid_endpoints = {str(item.get("id")) for item in endpoints if item.get("id")}
    valid_transitions = {str(item.get("id")) for item in transitions if item.get("id")}
    valid_rules = {str(item.get("id")) for item in rules if item.get("id")}

    planned_endpoint: set[str] = set()
    planned_transition: set[str] = set()
    planned_rule: set[str] = set()
    transition_cases: dict[str, list[str]] = {}

    for case in cases:
        refs = resolve_coverage_refs(_case_orm_dict(case), backend, machine_specs)
        planned_endpoint.update(set(refs["endpointIds"]) & valid_endpoints)
        planned_transition.update(set(refs["transitionIds"]) & valid_transitions)
        planned_rule.update(set(refs["ruleIds"]) & valid_rules)
        for transition_id in refs["transitionIds"]:
            if transition_id in valid_transitions:
                transition_cases.setdefault(transition_id, []).append(case.id)

    passed_stmt = select(ExecutionRecord).where(
        ExecutionRecord.project_id == pid,
        ExecutionRecord.status == "passed",
    )
    if requirement_id:
        linked_ids = set(session.scalars(
            select(ExecutionRequirement.execution_id).where(
                ExecutionRequirement.project_id == pid,
                ExecutionRequirement.requirement_id.is_not(None)
                if requirement_id == "__none__"
                else ExecutionRequirement.requirement_id == requirement_id,
            )
        ))
        project_record_ids = set(session.scalars(
            select(ExecutionRecord.id).where(ExecutionRecord.project_id == pid)
        ))
        allowed_ids = (
            project_record_ids - linked_ids
            if requirement_id == "__none__"
            else linked_ids
        )
        passed_stmt = passed_stmt.where(ExecutionRecord.id.in_(allowed_ids or {"__none__"}))

    passed_records = list(session.scalars(passed_stmt))
    executed_endpoint: set[str] = set()
    executed_transition: set[str] = set()
    executed_rule: set[str] = set()
    for record in passed_records:
        snapshot = _json(record.test_case_snapshot, {})
        # 兼容旧数据：早期记录的 snapshot 可能为空，此时才回退到当前用例。
        if not snapshot and record.test_case_id in case_by_id:
            snapshot = _case_orm_dict(case_by_id[record.test_case_id])
        if not isinstance(snapshot, dict) or not snapshot:
            continue
        refs = resolve_coverage_refs(snapshot, backend, machine_specs)
        executed_endpoint.update(set(refs["endpointIds"]) & valid_endpoints)
        executed_transition.update(set(refs["transitionIds"]) & valid_transitions)
        executed_rule.update(set(refs["ruleIds"]) & valid_rules)

    def block(total: int, planned: set[str], executed: set[str], uncovered: list[dict]) -> dict:
        return {
            "total": total,
            "plannedCovered": len(planned),
            "executedCovered": len(executed),
            "uncoveredCount": max(total - len(planned), 0),
            "plannedRate": round(len(planned) / total, 4) if total else 0,
            "executedRate": round(len(executed) / total, 4) if total else 0,
            "uncovered": uncovered,
        }

    endpoint_uncovered = [
        {
            "id": item.get("id"),
            "label": f"{item.get('method', '')} {item.get('path', '')}".strip(),
        }
        for item in endpoints
        if str(item.get("id")) not in planned_endpoint
    ]
    transition_uncovered = [
        {
            "id": item.get("id"),
            "label": f"{item.get('from', '')} → {item.get('to', '')}（{item.get('trigger', '')}）",
        }
        for item in transitions
        if str(item.get("id")) not in planned_transition
    ]
    rule_uncovered = [
        {
            "id": item.get("id"),
            "label": item.get("description") or item.get("guard") or item.get("id"),
        }
        for item in rules
        if str(item.get("id")) not in planned_rule
    ]

    for machine in machine_specs:
        for transition in machine["transitions"]:
            transition_id = str(transition.get("id") or "")
            transition["planned"] = transition_id in planned_transition
            transition["plannedCaseIds"] = transition_cases.get(transition_id, [])
            transition["executed"] = transition_id in executed_transition

    return {
        "scope": {
            "projectId": pid,
            "requirementId": requirement_id,
            "executionBasis": "只统计 status=passed 的执行记录",
            "snapshotBased": True,
        },
        "scoped": {
            "endpoints": len(endpoints),
            "transitions": len(transitions),
            "rules": len(rules),
            "cases": len(cases),
            "passedExecutions": len(passed_records),
        },
        "apiEndpoint": block(
            len(endpoints), planned_endpoint, executed_endpoint, endpoint_uncovered
        ),
        "stateTransition": block(
            len(transitions), planned_transition, executed_transition, transition_uncovered
        ),
        "businessRule": block(
            len(rules), planned_rule, executed_rule, rule_uncovered
        ),
        "stateMachines": machine_specs,
    }
