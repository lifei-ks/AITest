"""覆盖矩阵（6.5.5）：规划覆盖 vs 执行覆盖。

铁律：执行覆盖 = 有执行记录 且 该记录 status='passed'。
"""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.entity.models import CodeAnalysis, ExecutionRecord, StateMachine, TestCase


def _analysis(session: Session, pid: str) -> CodeAnalysis | None:
    return session.scalars(
        select(CodeAnalysis).where(CodeAnalysis.project_id == pid).order_by(CodeAnalysis.created_at.desc()).limit(1)
    ).first()


def coverage_matrix(session: Session, pid: str) -> dict:
    analysis = _analysis(session, pid)
    backend = json.loads(analysis.backend_result) if analysis and analysis.backend_result else {}
    endpoints = backend.get("endpoints", [])
    rules = backend.get("businessRules", [])

    machines = list(session.scalars(select(StateMachine).where(StateMachine.project_id == pid)))
    transitions = []
    sm_details = []
    for m in machines:
        ts = json.loads(m.transitions) if m.transitions else []
        verified = [t for t in ts if t.get("verified")]
        transitions.extend(verified)
        sm_details.append({
            "id": m.id, "name": m.name,
            "states": json.loads(m.states) if m.states else [],
            "transitions": verified,
        })

    # 用例引用 + 执行状态
    cases = list(session.scalars(select(TestCase).where(TestCase.project_id == pid)))
    planned_endpoint = set()
    planned_transition = set()
    planned_rule = set()
    case_by_ref: dict[str, list[str]] = {}  # transitionId -> [caseId]

    for tc in cases:
        refs = json.loads(tc.coverage_refs) if tc.coverage_refs else {}
        planned_endpoint.update(refs.get("endpointIds", []))
        planned_transition.update(refs.get("transitionIds", []))
        planned_rule.update(refs.get("ruleIds", []))
        for tid in refs.get("transitionIds", []):
            case_by_ref.setdefault(tid, []).append(tc.id)

    # 执行覆盖：passed 的执行记录对应的用例引用
    passed_records = list(session.scalars(
        select(ExecutionRecord).where(ExecutionRecord.project_id == pid, ExecutionRecord.status == "passed")
    ))
    passed_case_ids = {r.test_case_id for r in passed_records}
    executed_endpoint: set[str] = set()
    executed_transition: set[str] = set()
    executed_rule: set[str] = set()
    for tc in cases:
        if tc.id not in passed_case_ids:
            continue
        refs = json.loads(tc.coverage_refs) if tc.coverage_refs else {}
        executed_endpoint.update(refs.get("endpointIds", []))
        executed_transition.update(refs.get("transitionIds", []))
        executed_rule.update(refs.get("ruleIds", []))

    def _block(total, planned, executed):
        return {
            "total": total,
            "plannedCovered": len(planned),
            "executedCovered": len(executed),
            "plannedRate": round(len(planned) / total, 4) if total else 0,
            "executedRate": round(len(executed) / total, 4) if total else 0,
        }

    # stateMachines 详情：每条转换的 planned/executed
    for sm in sm_details:
        for t in sm["transitions"]:
            tid = t.get("id", "")
            t["planned"] = tid in planned_transition
            t["plannedCaseIds"] = case_by_ref.get(tid, [])
            t["executed"] = tid in executed_transition

    return {
        "scoped": {"endpoints": len(endpoints), "transitions": len(transitions), "rules": len(rules)},
        "apiEndpoint": _block(len(endpoints), planned_endpoint, executed_endpoint),
        "stateTransition": _block(len(transitions), planned_transition, executed_transition),
        "businessRule": _block(len(rules), planned_rule, executed_rule),
        "stateMachines": sm_details,
    }
