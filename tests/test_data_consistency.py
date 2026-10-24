"""数据一致性与执行补偿回归：空生成保留旧用例、需求变更失效旧用例、执行异常不遗留 running。"""
from __future__ import annotations

import json
import uuid

from sqlalchemy import select

from app.analyzer.models import BackendResult, FrontendResult
from app.agent.quality_gate import GateResult
from app.database import Base, SessionLocal, get_engine
from app.entity.models import (
    ExecutionRecord,
    Project,
    Requirement,
    RequirementCase,
    TestCase,
    TestPoint,
)
from app.service import execution_service, generation_service, requirement_service


def _prepare_requirement_with_case(tag: str) -> tuple[str, str, str]:
    Base.metadata.create_all(get_engine())
    with SessionLocal() as session:
        project = Project(name=f"consistency-{tag}-{uuid.uuid4().hex[:6]}", source_type="local_path", source_path=".")
        session.add(project)
        session.flush()
        req = Requirement(project_id=project.id, name=tag, version="V1.0", content="创建订单", status="ready")
        session.add(req)
        session.flush()
        point = TestPoint(requirement_id=req.id, project_id=project.id, title="创建订单", category="functional")
        case = TestCase(
            id=f"TC-9{uuid.uuid4().hex[:5].upper()}",
            project_seq=1,
            project_id=project.id,
            title="旧自动用例",
            source="ai",
            structured_steps=json.dumps([{"type": "state_assert", "expected": "「创建成功」"}]),
            expected_results=json.dumps(["「创建成功」"]),
        )
        session.add_all([point, case])
        session.flush()
        session.add(RequirementCase(requirement_id=req.id, test_case_id=case.id, test_point_id=point.id))
        session.commit()
        return project.id, req.id, case.id


def test_list_generations_backfills_legacy_snapshot_once():
    """改造前的实时用例首次查看时归档为一个只读历史版本，且不会重复归档。"""
    _pid, rid, cid = _prepare_requirement_with_case("legacy")

    with SessionLocal() as session:
        rows = generation_service.list_case_generations(session, rid)
        assert len(rows) == 1
        assert rows[0]["mode"] == "legacy"
        assert rows[0]["caseCount"] == 1
        assert rows[0]["summary"]["legacy"] is True

        detail = generation_service.get_case_generation(session, rid, rows[0]["id"])
        assert detail["cases"][0]["id"] == cid
        assert detail["cases"][0]["expectedResults"] == ["「创建成功」"]

        # 再次查看不能新增第二个归档版本
        again = generation_service.list_case_generations(session, rid)
        assert len(again) == 1
        assert again[0]["id"] == rows[0]["id"]


def test_empty_generation_preserves_existing_cases(monkeypatch):
    pid, rid, cid = _prepare_requirement_with_case("empty")
    monkeypatch.setattr(
        generation_service,
        "get_generation_context",
        lambda session, project_id: (BackendResult(), FrontendResult(), []),
    )
    monkeypatch.setattr(
        generation_service,
        "run_quality_gate",
        lambda cases: GateResult(generated=len(cases), kept=[], dropped=len(cases), dropReasons={}),
    )
    result = generation_service.generate(pid, "rule", requirement_id=rid)
    assert result["kept"] == 0
    assert result["preservedExisting"] is True
    with SessionLocal() as session:
        assert session.get(TestCase, cid) is not None
        mapping = session.scalars(
            select(RequirementCase).where(RequirementCase.requirement_id == rid)
        ).first()
        assert mapping is not None


def test_requirement_content_change_invalidates_generated_cases():
    pid, rid, cid = _prepare_requirement_with_case("revision")
    with SessionLocal() as session:
        manual = TestCase(
            id=f"TC-8{uuid.uuid4().hex[:5].upper()}",
            project_seq=2,
            project_id=pid,
            title="手工用例",
            source="manual",
            structured_steps=json.dumps([]),
            expected_results=json.dumps(["「手工」"]),
        )
        session.add(manual)
        session.flush()
        session.add(RequirementCase(requirement_id=rid, test_case_id=manual.id))
        session.commit()
        manual_id = manual.id
        requirement_service.update_requirement(session, rid, {"content": "新的需求内容"})
        session.commit()
    with SessionLocal() as session:
        assert session.get(TestCase, cid) is None
        assert session.get(TestCase, manual_id) is not None
        assert not list(
            session.scalars(select(RequirementCase).where(RequirementCase.requirement_id == rid))
        )


def test_execute_single_exception_does_not_leave_running(monkeypatch):
    Base.metadata.create_all(get_engine())
    with SessionLocal() as session:
        project = Project(name="exec-failure", source_type="local_path", source_path=".", target_url="http://demo.local")
        session.add(project)
        session.flush()
        case = TestCase(
            id=f"TC-7{uuid.uuid4().hex[:5].upper()}",
            project_seq=1,
            project_id=project.id,
            title="执行异常用例",
            source="rule",
            structured_steps=json.dumps([]),
            expected_results=json.dumps(["「成功」"]),
        )
        session.add(case)
        session.commit()
        pid, cid = project.id, case.id

    monkeypatch.setattr(execution_service, "ensure_target_reachable", lambda target: None)
    monkeypatch.setattr(
        execution_service,
        "execute_case",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("模拟执行器崩溃")),
    )
    result = execution_service.execute_single(pid, cid, "http://demo.local")
    assert result["status"] == "failed"
    assert "异常中断" in result["errorMessage"]
    with SessionLocal() as session:
        records = list(session.scalars(select(ExecutionRecord).where(ExecutionRecord.test_case_id == cid)))
        assert len(records) == 1
        assert records[0].status == "failed"
        assert records[0].end_time is not None
        assert records[0].error_message and "异常中断" in records[0].error_message
        assert session.get(TestCase, cid).execution_status == "failed"
