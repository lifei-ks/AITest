"""多 Agent 工作流：离线闭环、审核修订上限、阶段失败保留、结果分析保守性。"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent import result_analysis_agent, test_point_agent
from app.agent.prompt_loader import placeholders, render_prompt
from app.database import Base, SessionLocal, get_engine
from app.entity.models import (
    CodeAnalysis,
    ExecutionRecord,
    ExecutionStep,
    Project,
    Requirement,
    RequirementCase,
    TestCase,
    WorkflowCase,
    WorkflowRun,
    WorkflowStage,
)
from app.llm.client import LlmClient, LlmUnavailable
from app.service import analysis_service, generation_service, report_service, requirement_service, workflow_service
from app.workflow.schemas import WorkflowCreate

ORDER_SYSTEM = Path(__file__).resolve().parent.parent / "scripts" / "order-system"

PRD = """# 订单主流程
支持填写收货人、手机号、地址、金额并提交订单，创建成功提示「订单创建成功」。
收货人不能为空，手机号不能为空，地址不能为空，金额不能为空。
收货人长度不能超过20个字符。
订单列表支持支付、发货、取消操作，支付成功提示「支付成功」。
"""


def _offline_client() -> LlmClient:
    return LlmClient("", "", "offline-model")


@pytest.fixture(scope="module")
def analyzed_project():
    Base.metadata.create_all(get_engine())
    with SessionLocal() as session:
        project = Project(
            name=f"工作流测试-{uuid.uuid4().hex[:6]}",
            source_type="local_path",
            source_path=str(ORDER_SYSTEM),
            target_url="http://127.0.0.1:8200",
        )
        session.add(project)
        session.commit()
        pid = project.id
        analysis = CodeAnalysis(project_id=pid, status="running")
        session.add(analysis)
        session.commit()
        aid = analysis.id
    analysis_service.run_analysis(pid, aid)
    return pid


def _create_requirement(pid: str, name: str = "订单主流程") -> str:
    with SessionLocal() as session:
        req = Requirement(
            project_id=pid,
            name=name,
            version=f"V{uuid.uuid4().hex[:4]}",
            content=PRD,
            status="draft",
        )
        session.add(req)
        session.commit()
        return req.id


def _fake_execution(monkeypatch, *, status: str = "failed", error_category: str = "ASSERT_FAILED"):
    calls: list[str] = []

    def fake_execute(pid, cid, target_url=None, mode="programmatic", batch_id=None):
        calls.append(cid)
        with SessionLocal() as session:
            tc = session.get(TestCase, cid)
            eid = "EX-" + uuid.uuid4().hex[:8]
            snapshot = generation_service.case_to_dict(tc)
            now = datetime.now(timezone.utc)
            session.add(
                ExecutionRecord(
                    id=eid,
                    project_id=pid,
                    test_case_id=cid,
                    test_case_title=tc.title,
                    test_case_snapshot=json.dumps(snapshot, ensure_ascii=False),
                    status=status,
                    mode=mode,
                    target_url=target_url or "http://demo.local",
                    start_time=now,
                    end_time=now,
                    duration_ms=12,
                    error_category=error_category,
                    error_message="页面未出现「订单创建成功」",
                    passed_steps=2,
                    total_steps=4,
                )
            )
            session.add(
                ExecutionStep(
                    execution_id=eid,
                    step_index=4,
                    action="验证创建成功",
                    target="",
                    step_type="state_assert",
                    selector_used="订单创建成功",
                    result="failed" if status == "failed" else "passed",
                    error="断言失败" if status == "failed" else None,
                )
            )
            session.commit()
        return {
            "executionId": eid,
            "status": status,
            "passedSteps": 2,
            "totalSteps": 4,
            "errorCategory": error_category,
            "errorMessage": "页面未出现「订单创建成功」",
            "durationMs": 12,
        }

    monkeypatch.setattr(workflow_service.execution_service, "execute_single", fake_execute)
    return calls


class ReviseClient:
    """始终要求修订；修复只改标题，用于验证轮次上限。"""

    model = "fake-review-model"

    def available(self) -> bool:
        return True

    def chat_json(self, messages, max_tokens=4096, json_mode=False):
        content = messages[0]["content"]
        if "修复输入" in content:
            return {"summary": "调整标题", "patch": {"title": "修订后的用例标题"}}
        return {
            "review_status": "revise",
            "summary": "断言还不够明确",
            "issues": [
                {
                    "severity": "warning",
                    "field": "structuralSteps",
                    "problem": "断言不够明确",
                    "suggestion": "补充真实页面文案",
                    "evidence": "需求文档",
                }
            ],
            "missing_coverage": [],
            "suggestions": ["补充断言"],
        }


def test_all_external_prompts_render_without_placeholders():
    values = {
        "REQUIREMENT_VERSION": "V1.0",
        "REQUIREMENT_NAME": "订单",
        "REQUIREMENT_CONTENT": "内容",
        "FACTS_JSON": "{}",
        "POINTS_JSON": "[]",
        "REVIEW_PAYLOAD": "{}",
        "REPAIR_PAYLOAD": "{}",
        "EVIDENCE_JSON": "{}",
    }
    for name in (
        "test_points",
        "test_cases_by_point",
        "project_cases",
        "case_review",
        "case_repair",
        "case_review_workflow",
        "result_analysis",
    ):
        rendered = render_prompt(name, values)
        assert "{{" not in rendered and "}}" not in rendered
        assert placeholders(rendered) == set()


def test_point_agent_drops_invalid_items_and_clamps_fields():
    class BadClient:
        available = True

        def chat_json(self, messages, max_tokens=8192, json_mode=False):
            return {
                "testPoints": [
                    {"title": ""},
                    {"title": "合法测试点", "category": "bogus", "priority": "P9", "fields": "手机号", "confidence": "oops"},
                    {"title": "合法测试点"},
                ]
            }

    points = test_point_agent.extract_points(
        BadClient(),
        requirement_version="V1.0",
        requirement_name="订单",
        content="内容",
        facts={},
    )
    assert len(points) == 1
    assert points[0]["category"] == "functional"
    assert points[0]["priority"] == "P1"
    assert points[0]["fields"] == ["手机号"]
    assert points[0]["confidence"] == 0.85


def test_result_analysis_never_overclaims_without_evidence():
    evidence = {
        "caseId": "TC-1",
        "expectedResults": ["「订单创建成功」"],
        "executionStatus": "failed",
        "errorCategory": "ASSERT_FAILED",
        "errorMessage": "断言失败",
        "steps": [{"index": 3, "result": "failed", "error": "断言失败"}],
        "visionReview": {},
    }
    result = result_analysis_agent.analyze(None, evidence)
    assert result["failure_category"] == "unknown"
    assert result["needs_human_review"] is True
    assert result["confidence"] <= 0.5

    # 模型声称产品缺陷但证据未引用任何步骤/截图 → 降级 unknown
    raw = {
        "failure_category": "product_defect",
        "failure_reason": "明显是产品 bug",
        "evidence": ["我觉得不对"],
        "confidence": 0.99,
        "recommended_action": "修产品",
        "needs_human_review": False,
    }
    guarded = result_analysis_agent.guard_analysis(raw, evidence)
    assert guarded["failure_category"] == "unknown"
    assert guarded["needs_human_review"] is True
    assert guarded["confidence"] <= 0.5

    # 引用真实步骤的缺陷证据可以保留分类，但仍需人工复核
    raw["evidence"] = ["步骤3 断言失败：页面没有出现「订单创建成功」"]
    guarded = result_analysis_agent.guard_analysis(raw, evidence)
    assert guarded["failure_category"] == "product_defect"
    assert guarded["needs_human_review"] is True


def test_result_analysis_routes_locator_failures_to_script_issue():
    evidence = {
        "caseId": "TC-2",
        "expectedResults": ["「订单创建成功」"],
        "executionStatus": "failed",
        "errorCategory": "ENV_ERROR",
        "errorMessage": "Locator.fill: Element is not an <input>",
        "steps": [
            {
                "index": 6,
                "result": "failed",
                "action": "输入备注",
                "selectorUsed": "请输入备注（选填）",
                "error": "Locator.fill: Element is not an <input>",
            }
        ],
        "visionReview": {},
    }
    result = result_analysis_agent.analyze(None, evidence)
    assert result["failure_category"] == "test_script_issue"
    assert result["needs_human_review"] is True
    assert any("步骤6" in item for item in result["evidence"])


def test_llm_json_retry_is_bounded(monkeypatch):
    client = LlmClient("fake-key", "", "fake-model")
    calls: list[int] = []

    def always_bad(messages, max_tokens, json_mode):
        calls.append(1)
        return "not a json object"

    monkeypatch.setattr(client, "_chat", always_bad)
    with pytest.raises(LlmUnavailable):
        client.chat_json([{"role": "user", "content": "x"}], json_mode=True)
    assert len(calls) == 2, "JSON 解析失败只允许普通模式再试一次，不能无限重试"


def test_requirement_points_fall_back_when_llm_unavailable(analyzed_project, monkeypatch):
    rid = _create_requirement(analyzed_project, "LLM 降级")

    class UnavailableClient:
        model = "unavailable"

        def available(self):
            return True

        def chat_json(self, *args, **kwargs):
            raise LlmUnavailable("模拟非法 JSON / 超时")

    monkeypatch.setattr(
        requirement_service.settings_service,
        "get_text_client",
        lambda *a, **k: UnavailableClient(),
    )
    with SessionLocal() as session:
        req = session.get(Requirement, rid)
        points = requirement_service.parse_points(session, req, "auto")
    assert points
    assert all(point["source"] == "rule" for point in points)


def test_pipeline_runs_code_analysis_when_project_has_none(monkeypatch):
    with SessionLocal() as session:
        project = Project(
            name=f"pipeline-analysis-{uuid.uuid4().hex[:6]}",
            source_type="local_path",
            source_path=str(ORDER_SYSTEM),
            target_url="http://127.0.0.1:8200",
        )
        session.add(project)
        session.commit()
        pid = project.id
        req = Requirement(
            project_id=pid,
            name="一键分析",
            version="V1.0",
            content=PRD,
            status="draft",
        )
        session.add(req)
        session.commit()
        rid = req.id

    monkeypatch.setattr(workflow_service, "_text_client", _offline_client)
    with SessionLocal() as session:
        run = workflow_service.create_run(
            session,
            pid,
            WorkflowCreate(
                requirementId=rid,
                mode="rule",
                autoExecute=False,
                analyzeEnabled=False,
                analyzeCode=True,
                maxCases=5,
            ),
            run_async=False,
        )
    with SessionLocal() as session:
        detail = workflow_service.get_run_detail(session, run["id"])
    stages = {stage["stage"]: stage["status"] for stage in detail["stages"]}
    assert stages["code_analysis"] == "completed"
    assert stages["test_points"] == "completed"
    assert stages["case_generation"] == "completed"
    assert stages["case_review"] == "completed"
    assert stages["execution"] == "skipped"
    assert stages["result_analysis"] == "skipped"
    code = detail["summary"]["codeAnalysis"]
    assert code["status"] == "success"
    assert code["endpoints"] > 0
    assert code["pages"] > 0


def test_workflow_offline_closed_loop(analyzed_project, monkeypatch):
    rid = _create_requirement(analyzed_project, "离线闭环")
    monkeypatch.setattr(workflow_service, "_text_client", _offline_client)
    calls = _fake_execution(monkeypatch)

    with SessionLocal() as session:
        run = workflow_service.create_run(
            session,
            analyzed_project,
            WorkflowCreate(
                requirementId=rid,
                mode="rule",
                autoExecute=True,
                maxRevisions=2,
                maxCases=10,
            ),
            run_async=False,
        )
    assert run["status"] == "completed"
    detail = None
    with SessionLocal() as session:
        detail = workflow_service.get_run_detail(session, run["id"])
    stages = {stage["stage"]: stage["status"] for stage in detail["stages"]}
    assert stages == {
        "code_analysis": "skipped",
        "test_points": "completed",
        "case_generation": "completed",
        "case_review": "completed",
        "execution": "completed",
        "result_analysis": "completed",
        "report": "completed",
    }
    assert detail["cases"]
    assert calls, "审核通过的自动化用例应被真实调度"
    assert all(case["reviewStatus"] == "pass" for case in detail["cases"])
    assert all(case["execution"] for case in detail["cases"])
    assert all(case["analysis"] for case in detail["cases"])
    assert all(case["analysis"]["failureCategory"] == "unknown" for case in detail["cases"])

    with SessionLocal() as session:
        for case in detail["cases"]:
            record = session.get(ExecutionRecord, case["execution"]["id"])
            assert record is not None
            assert record.status == "failed", "结果分析不得覆盖真实执行状态"
        report = report_service.build_report(
            session, analyzed_project, workflow_run_id=run["id"]
        )
        assert "多 Agent 测试工作流" in report
        assert "结果分析" in report


def test_review_revision_is_capped_and_never_executes(analyzed_project, monkeypatch):
    rid = _create_requirement(analyzed_project, "修订上限")
    with SessionLocal() as session:
        req = session.get(Requirement, rid)
        points = requirement_service.parse_points(session, req, "rule")
        requirement_service.save_points(session, req, points)
        session.commit()
    generation_service.generate(analyzed_project, "rule", requirement_id=rid)
    monkeypatch.setattr(workflow_service, "_text_client", lambda: ReviseClient())
    _fake_execution(monkeypatch, status="passed", error_category=None)

    with SessionLocal() as session:
        run = workflow_service.create_run(
            session,
            analyzed_project,
            WorkflowCreate(
                requirementId=rid,
                mode="rule",
                autoExecute=True,
                maxRevisions=2,
                reuseExistingCases=True,
            ),
            run_async=False,
        )
    with SessionLocal() as session:
        detail = workflow_service.get_run_detail(session, run["id"])
    rejected_with_execution = [
        case for case in detail["cases"]
        if case["reviewStatus"] == "reject" and case["execution"]
    ]
    assert rejected_with_execution == [], "被拒绝的用例不得执行"
    capped = [case for case in detail["cases"] if case["revisionCount"] == 2]
    assert capped, "至少存在一条用足 2 轮修订后仍被拒绝的用例"
    assert all(case["finalReview"]["reviewStatus"] == "reject" for case in capped)
    assert any(len(case["reviews"]) >= 3 for case in detail["cases"]), "首审、每轮修订后审核和最终拒绝都要保留"
    stages = {stage["stage"]: stage["status"] for stage in detail["stages"]}
    assert stages["execution"] in ("completed", "skipped")
    assert stages["result_analysis"] in ("completed", "skipped")


class BadRepairClient:
    """审核要求修订，但修复补丁会破坏结构化步骤；用于验证保留原用例。"""

    model = "fake-bad-repair"

    def available(self) -> bool:
        return True

    def chat_json(self, messages, max_tokens=4096, json_mode=False):
        content = messages[0]["content"]
        if "分析输入" in content:
            return {
                "failure_category": "none",
                "failure_reason": "执行通过",
                "evidence": ["执行状态=passed"],
                "confidence": 0.9,
                "recommended_action": "",
                "needs_human_review": False,
            }
        if "修复输入" in content:
            return {
                "summary": "错误补丁：新增无锚点断言",
                "patch": {
                    "structuredSteps": [
                        {
                            "order": 1,
                            "type": "ui_action",
                            "action": "打开页面",
                            "target": "页面",
                            "uiSelector": {"type": "route", "value": "/orders/create"},
                            "expected": "",
                            "inputValue": "",
                        },
                        {
                            "order": 2,
                            "type": "state_assert",
                            "action": "验证创建成功",
                            "target": "",
                            "uiSelector": {"type": "text", "value": "订单创建成功"},
                            "expected": "订单创建成功",
                            "inputValue": "",
                        },
                    ]
                },
            }
        return {
            "review_status": "revise",
            "summary": "建议补充更精确的文案",
            "issues": [
                {
                    "severity": "warning",
                    "field": "structuredSteps",
                    "problem": "断言文案可以更精确",
                    "suggestion": "结合页面文案调整",
                    "evidence": "需求文档",
                }
            ],
            "missing_coverage": [],
            "suggestions": [],
        }


def test_invalid_ai_repair_keeps_deterministically_valid_case(analyzed_project, monkeypatch):
    rid = _create_requirement(analyzed_project, "修复回退")
    with SessionLocal() as session:
        req = session.get(Requirement, rid)
        points = requirement_service.parse_points(session, req, "rule")
        requirement_service.save_points(session, req, points)
        session.commit()
    generation_service.generate(analyzed_project, "rule", requirement_id=rid)

    monkeypatch.setattr(workflow_service, "_text_client", lambda: BadRepairClient())
    calls = _fake_execution(monkeypatch, status="passed", error_category=None)

    with SessionLocal() as session:
        run = workflow_service.create_run(
            session,
            analyzed_project,
            WorkflowCreate(
                requirementId=rid,
                mode="rule",
                autoExecute=True,
                maxRevisions=1,
                reuseExistingCases=True,
            ),
            run_async=False,
        )
    with SessionLocal() as session:
        detail = workflow_service.get_run_detail(session, run["id"])

    assert calls, "原用例确定性校验通过，AI 修复补丁无效时仍应允许执行"
    assert any(case["reviewStatus"] == "pass" for case in detail["cases"])
    notes = [
        item.get("problem", "")
        for case in detail["cases"]
        for item in (case.get("finalReview") or {}).get("issues", [])
    ]
    assert any("已保留原用例" in note for note in notes)


def test_review_converts_existing_fixed_count_to_runtime_baseline(analyzed_project, monkeypatch):
    """复用旧用例时，审核阶段也应把固定总数改为 capture + 基线+1。"""
    steps = [
        {"order": 1, "type": "ui_action", "action": "打开新建订单页面", "target": "新建订单", "uiSelector": {"type": "route", "value": "/orders/create"}, "expected": "", "inputValue": ""},
        {"order": 2, "type": "input", "action": "输入收货人", "target": "收货人", "uiSelector": {"type": "placeholder", "value": "请输入收货人"}, "expected": "", "inputValue": "张三"},
        {"order": 3, "type": "input", "action": "输入手机号", "target": "手机号", "uiSelector": {"type": "placeholder", "value": "请输入手机号"}, "expected": "", "inputValue": "13800138000"},
        {"order": 4, "type": "input", "action": "输入收货地址", "target": "收货地址", "uiSelector": {"type": "placeholder", "value": "请输入收货地址"}, "expected": "", "inputValue": "北京市朝阳区测试路1号"},
        {"order": 5, "type": "input", "action": "输入订单金额", "target": "订单金额", "uiSelector": {"type": "placeholder", "value": "请输入订单金额"}, "expected": "", "inputValue": "100.00"},
        {"order": 6, "type": "ui_action", "action": "点击提交", "target": "提交订单", "uiSelector": {"type": "text", "value": "提交订单"}, "expected": "", "inputValue": ""},
        {"order": 7, "type": "state_assert", "action": "验证创建成功", "target": "", "uiSelector": {"type": "text", "value": "「订单创建成功」"}, "expected": "「订单创建成功」"},
        {"order": 8, "type": "state_assert", "action": "验证列表总数", "target": "", "uiSelector": {"type": "text", "value": "「共 5 条订单」"}, "expected": "「共 5 条订单」"},
    ]
    with SessionLocal() as session:
        req = Requirement(project_id=analyzed_project, name="固定总数", version=f"V{uuid.uuid4().hex[:4]}", content=PRD, status="ready")
        session.add(req)
        session.flush()
        tc = TestCase(
            id="TC-900001",
            project_seq=900001,
            project_id=analyzed_project,
            title="新建订单后总数断言",
            type="positive",
            priority="P1",
            preconditions=json.dumps([], ensure_ascii=False),
            steps=json.dumps([s["action"] for s in steps], ensure_ascii=False),
            expected_results=json.dumps(["「订单创建成功」，总数变为 5 条"], ensure_ascii=False),
            structured_steps=json.dumps(steps, ensure_ascii=False),
            test_data=json.dumps({"receiverName": "张三"}, ensure_ascii=False),
            coverage_refs=json.dumps({}, ensure_ascii=False),
            source="ai",
            quality_score=100,
        )
        session.add(tc)
        session.add(RequirementCase(requirement_id=req.id, test_case_id=tc.id))
        run = WorkflowRun(project_id=analyzed_project, requirement_id=req.id, status="running", mode="rule")
        session.add(run)
        session.flush()
        session.add(WorkflowCase(run_id=run.id, case_id=tc.id, position=1, automation_status="automated", review_status="pending"))
        session.commit()
        run_id = run.id
        tc_id = tc.id

    monkeypatch.setattr(workflow_service, "_text_client", _offline_client)
    with SessionLocal() as session:
        run = session.get(WorkflowRun, run_id)
        result = workflow_service._stage_case_review(session, run, {"maxRevisions": 2, "reviewEnabled": True})
    assert result["status"] == "completed"
    with SessionLocal() as session:
        updated = session.get(TestCase, tc_id)
        updated_steps = json.loads(updated.structured_steps)
    assert updated_steps[1]["type"] == "capture"
    assert updated_steps[1]["variable"] == "base_count"
    assert any("${base_count+1}" in (s.get("expected") or "") for s in updated_steps)
    assert not any("共 5 条订单" in json.dumps(s, ensure_ascii=False) for s in updated_steps)


def test_case_generation_failure_preserves_previous_stage(analyzed_project, monkeypatch):
    rid = _create_requirement(analyzed_project, "阶段失败")
    monkeypatch.setattr(workflow_service, "_text_client", _offline_client)

    def boom(*args, **kwargs):
        raise RuntimeError("模拟生成失败")

    monkeypatch.setattr(workflow_service.generation_service, "generate", boom)

    with SessionLocal() as session:
        run = workflow_service.create_run(
            session,
            analyzed_project,
            WorkflowCreate(requirementId=rid, mode="rule", autoExecute=False),
            run_async=False,
        )
    with SessionLocal() as session:
        detail = workflow_service.get_run_detail(session, run["id"])
    stages = {stage["stage"]: stage for stage in detail["stages"]}
    assert stages["test_points"]["status"] == "completed"
    assert stages["case_generation"]["status"] == "failed"
    assert stages["case_review"]["status"] == "skipped"
    assert stages["execution"]["status"] == "skipped"
    assert stages["report"]["status"] == "completed"
    assert run["status"] == "partial"
    assert "用例生成" in (run["errorMessage"] or "")


def test_quick_create_project_and_upload_requirement():
    """Pipeline 快速接入：建项目 → 建需求版本 → 上传 PRD 后内容可读。"""
    from app.main import app

    with TestClient(app) as client:
        resp = client.post(
            "/api/projects",
            json={
                "name": f"quick-pipeline-{uuid.uuid4().hex[:6]}",
                "sourceType": "local_path",
                "sourcePath": str(ORDER_SYSTEM),
                "targetUrl": "http://127.0.0.1:8200",
            },
        )
        assert resp.status_code == 201, resp.text
        pid = resp.json()["data"]["id"]

        resp = client.post(
            f"/api/projects/{pid}/requirements",
            json={"name": "快速需求", "version": "V1.0", "content": ""},
        )
        assert resp.status_code == 201, resp.text
        rid = resp.json()["data"]["id"]

        resp = client.post(
            f"/api/requirements/{rid}/upload",
            files={"file": ("prd.md", PRD.encode("utf-8"), "text/markdown")},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["contentLength"] > 0

        detail = client.get(f"/api/requirements/{rid}").json()["data"]
        assert "订单创建成功" in detail["content"]


def test_recover_interrupted_runs_marks_failed():
    Base.metadata.create_all(get_engine())
    with SessionLocal() as session:
        workflow_run = WorkflowRun(
            project_id="recover-project",
            status="running",
            current_stage="case_review",
        )
        session.add(workflow_run)
        session.flush()
        running = WorkflowStage(
            run_id=workflow_run.id,
            stage="case_review",
            status="running",
            attempt=1,
        )
        pending = WorkflowStage(
            run_id=workflow_run.id,
            stage="execution",
            status="pending",
            attempt=1,
        )
        session.add_all([running, pending])
        session.commit()
        run_id = workflow_run.id

    assert workflow_service.recover_interrupted_runs() >= 1
    with SessionLocal() as session:
        recovered = session.get(WorkflowRun, run_id)
        stages = {
            stage.stage: stage
            for stage in session.scalars(
                select(WorkflowStage).where(WorkflowStage.run_id == run_id)
            )
        }
    assert recovered.status == "failed"
    assert "重启" in (recovered.error_message or "")
    assert stages["case_review"].status == "failed"
    assert stages["execution"].status == "skipped"


def test_workflow_api_and_report(analyzed_project, monkeypatch):
    from app.main import app

    rid = _create_requirement(analyzed_project, "API 闭环")
    monkeypatch.setattr(workflow_service, "_text_client", _offline_client)

    with TestClient(app) as client:
        resp = client.post(
            f"/api/projects/{analyzed_project}/workflows",
            json={
                "requirementId": rid,
                "mode": "rule",
                "autoExecute": False,
                "analyzeEnabled": False,
                "maxRevisions": 1,
            },
        )
        assert resp.status_code == 200, resp.text
        run_id = resp.json()["data"]["id"]

        deadline = time.time() + 30
        detail = None
        while time.time() < deadline:
            detail_resp = client.get(f"/api/workflows/{run_id}")
            assert detail_resp.status_code == 200
            detail = detail_resp.json()["data"]
            if detail["status"] not in ("pending", "running"):
                break
            time.sleep(0.2)
        assert detail and detail["status"] not in ("pending", "running")
        assert detail["stages"]
        report = client.get(f"/api/workflows/{run_id}/report")
        assert report.status_code == 200
        assert "多 Agent 测试工作流" in report.text
        project_report = client.get(f"/api/projects/{analyzed_project}/report?workflowRunId={run_id}")
        assert project_report.status_code == 200
        assert "多 Agent 测试工作流" in project_report.text


def test_parse_points_uses_selected_text_profile(analyzed_project, monkeypatch):
    rid = _create_requirement(analyzed_project, "选择测试点 LLM")
    calls: list[str | None] = []

    class Unavailable:
        model = "offline"

        def available(self):
            return False

    monkeypatch.setattr(
        requirement_service.settings_service,
        "get_text_client",
        lambda profile_id=None: calls.append(profile_id) or Unavailable(),
    )
    with SessionLocal() as session:
        req = session.get(Requirement, rid)
        points = requirement_service.parse_points(session, req, "auto", profile_id="profile-points")
    assert calls == ["profile-points"]
    assert points and all(point["source"] == "rule" for point in points)


def test_generate_uses_selected_text_profile(analyzed_project, monkeypatch):
    rid = _create_requirement(analyzed_project, "选择用例 LLM")
    with SessionLocal() as session:
        req = session.get(Requirement, rid)
        points = requirement_service.parse_points(session, req, "rule")
        requirement_service.save_points(session, req, points)
        session.commit()
    calls: list[str | None] = []

    class Unavailable:
        model = "offline"

        def available(self):
            return False

    monkeypatch.setattr(
        generation_service.settings_service,
        "get_text_client",
        lambda profile_id=None: calls.append(profile_id) or Unavailable(),
    )
    result = generation_service.generate(
        analyzed_project, "llm", requirement_id=rid, profile_id="profile-cases"
    )
    assert calls == ["profile-cases"]
    assert result["kept"] > 0
    assert result["mode"] == "rule"
