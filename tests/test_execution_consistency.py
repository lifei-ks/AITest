"""覆盖矩阵、执行状态、AI 审核与错误分类的一致性测试。"""
from __future__ import annotations

import json

from app.agent.review_agent import _normalize_verdict
from app.common.execution_enums import ERROR_CATEGORIES, execution_enum_payload
from app.database import Base, SessionLocal, get_engine
from app.entity.models import (
    CodeAnalysis,
    ExecutionRecord,
    ExecutionRequirement,
    Project,
    Requirement,
    RequirementCase,
    TestCase,
)
from app.service.coverage_service import coverage_matrix, resolve_coverage_refs
from app.service.execution_service import _decide_status, _original_status_from_steps


def test_coverage_refs_are_inferred_from_real_facts():
    endpoint_id = "ep-create"
    rule_id = "br-phone"
    transition_id = "tr-pay"
    backend = {
        "endpoints": [
            {
                "id": endpoint_id,
                "method": "POST",
                "path": "/api/orders",
                "methodName": "create",
            }
        ],
        "businessRules": [
            {
                "id": rule_id,
                "description": "receiverName 不能为空",
                "guard": 'receiverName == null',
            }
        ],
    }
    machines = [{
        "transitions": [{
            "id": transition_id,
            "trigger": "支付",
            "verified": True,
        }]
    }]

    refs = resolve_coverage_refs(
        {
            "title": "收货人为空时支付订单并提交失败",
            "structuredSteps": [
                {"action": "打开订单页面", "target": "/orders/create"},
                {"action": "提交订单", "target": "提交", "inputValue": ""},
            ],
            "testData": {"receiverName": ""},
            "coverageRefs": {},
        },
        backend,
        machines,
    )

    assert refs["endpointIds"] == [endpoint_id]
    assert refs["ruleIds"] == [rule_id]
    assert refs["transitionIds"] == [transition_id]

    ambiguous = {
        "endpoints": [
            {"id": "ep-a", "method": "POST", "path": "/a", "methodName": "create"},
            {"id": "ep-b", "method": "POST", "path": "/b", "methodName": "create"},
        ]
    }
    ambiguous_refs = resolve_coverage_refs(
        {"title": "提交订单", "structuredSteps": [{"action": "提交订单"}], "coverageRefs": {}},
        ambiguous,
        [],
    )
    assert ambiguous_refs["endpointIds"] == []

    state_refs = resolve_coverage_refs(
        {"title": "支付后发货并完成订单", "structuredSteps": [], "coverageRefs": {}},
        {
            "endpoints": [
                {"id": "ep-pay", "method": "POST", "path": "/orders/pay", "methodName": "pay"},
                {"id": "ep-ship", "method": "POST", "path": "/orders/ship", "methodName": "ship"},
                {"id": "ep-complete", "method": "POST", "path": "/orders/complete", "methodName": "complete"},
            ]
        },
        [],
    )
    assert set(state_refs["endpointIds"]) == {"ep-pay", "ep-ship", "ep-complete"}


def test_coverage_matrix_uses_passed_snapshot_and_requirement_scope():
    Base.metadata.create_all(get_engine())
    with SessionLocal() as session:
        project = Project(name="覆盖快照测试")
        session.add(project)
        session.flush()

        backend = {
            "language": "java-spring",
            "endpoints": [
                {"id": "ep-current", "method": "POST", "path": "/current", "methodName": "create"},
                {"id": "ep-snapshot", "method": "POST", "path": "/snapshot", "methodName": "create"},
                {"id": "ep-failed", "method": "POST", "path": "/failed", "methodName": "create"},
            ],
            "businessRules": [],
        }
        session.add(CodeAnalysis(
            project_id=project.id,
            status="success",
            backend_result=json.dumps(backend, ensure_ascii=False),
            frontend_result="{}",
        ))
        case = TestCase(
            id="TC-COV-SNAP",
            project_seq=99001,
            project_id=project.id,
            title="覆盖快照测试",
            module="订单",
            type="positive",
            priority="P1",
            steps=json.dumps(["创建订单"], ensure_ascii=False),
            expected_results=json.dumps(["创建成功"], ensure_ascii=False),
            structured_steps=json.dumps([{
                "order": 1,
                "type": "ui_action",
                "action": "创建订单",
                "target": "创建",
                "uiSelector": {"type": "text", "value": "创建"},
            }], ensure_ascii=False),
            test_data="{}",
            coverage_refs=json.dumps({"endpointIds": ["ep-current"]}),
            execution_status="passed",
        )
        requirement = Requirement(
            project_id=project.id,
            name="覆盖测试需求",
            version="V-COV",
            status="ready",
        )
        session.add_all([case, requirement])
        session.flush()
        session.add(RequirementCase(
            requirement_id=requirement.id,
            test_case_id=case.id,
        ))

        passed = ExecutionRecord(
            id="EX-COV-PASS",
            project_id=project.id,
            test_case_id=case.id,
            test_case_title=case.title,
            test_case_snapshot=json.dumps({
                "title": "覆盖快照测试",
                "structuredSteps": [{"action": "创建订单", "target": "创建"}],
                "coverageRefs": {"endpointIds": ["ep-snapshot"]},
            }, ensure_ascii=False),
            status="passed",
        )
        failed = ExecutionRecord(
            id="EX-COV-FAIL",
            project_id=project.id,
            test_case_id=case.id,
            test_case_title=case.title,
            test_case_snapshot=json.dumps({
                "coverageRefs": {"endpointIds": ["ep-failed"]},
            }),
            status="failed",
        )
        session.add_all([passed, failed])
        session.flush()
        session.add(ExecutionRequirement(
            execution_id=passed.id,
            project_id=project.id,
            requirement_id=requirement.id,
            requirement_version=requirement.version,
            requirement_name=requirement.name,
        ))
        session.flush()

        project_matrix = coverage_matrix(session, project.id)
        requirement_matrix = coverage_matrix(session, project.id, requirement.id)

    # 规划读取当前用例；执行覆盖读取执行瞬间快照，且不统计 failed。
    assert project_matrix["apiEndpoint"]["plannedCovered"] == 1
    assert project_matrix["apiEndpoint"]["executedCovered"] == 1
    assert {item["id"] for item in project_matrix["apiEndpoint"]["uncovered"]} == {
        "ep-snapshot", "ep-failed"
    }
    assert requirement_matrix["scoped"]["cases"] == 1
    assert requirement_matrix["scoped"]["passedExecutions"] == 1
    assert requirement_matrix["apiEndpoint"]["executedCovered"] == 1


def test_ai_review_cannot_override_deterministic_failure():
    assert _decide_status("passed", None, "ok") == "passed"
    assert _decide_status("passed", None, "suspect") == "needs_review"
    assert _decide_status("passed", None, "unavailable") == "needs_review"
    assert _decide_status("failed", "ASSERT_FAILED", "ok") == "failed"
    assert _decide_status("cancelled", None, "ok") == "cancelled"
    assert _decide_status("skipped", None, "ok") == "passed"
    assert _original_status_from_steps(
        [{"result": "passed"}, {"result": "skipped"}],
        "needs_review",
    ) == "skipped"


def test_ai_review_evidence_conflicts_downgrade_to_suspect():
    verdict, reason = _normalize_verdict(
        "ok",
        [{"imageIndex": 1, "verdict": "suspect"}],
        [],
        1,
    )
    assert verdict == "suspect"
    assert "疑似异常" in reason

    verdict, reason = _normalize_verdict("ok", [], [], 2)
    assert verdict == "suspect"
    assert "0/2" in reason


def test_execution_enum_metadata_is_complete():
    payload = execution_enum_payload()
    assert tuple(payload["errorCategories"]) == ERROR_CATEGORIES
    assert set(ERROR_CATEGORIES) == {
        "SELECTOR_NOT_FOUND",
        "ASSERT_FAILED",
        "NAVIGATION_FAILED",
        "TIMEOUT",
        "CASE_INVALID",
        "ENV_ERROR",
    }
    assert "needs_review" in payload["caseExecutionStatuses"]
    assert "not_run" not in payload["executionRecordStatuses"]
    assert payload["aiReviewVerdicts"]["ok"]["description"]
