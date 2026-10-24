"""执行结果按用例去重、执行历史、目标可达性预检与刷新证据测试。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent.execution_agent import _refresh_requested, _reload_and_verify, _StepError
from app.common.errors import BizError
from app.database import Base, SessionLocal, get_engine
from app.entity.models import ExecutionRecord, ExecutionReview, Project
from app.main import app
from app.service import execution_service


client = TestClient(app)


def test_list_executions_groups_by_case_and_exposes_history():
    Base.metadata.create_all(get_engine())
    with SessionLocal() as session:
        project = Project(name="执行去重测试")
        session.add(project)
        session.flush()

        now = datetime.now(timezone.utc)
        records = [
            ExecutionRecord(
                id="EX-GROUP-OLD",
                project_id=project.id,
                test_case_id="TC-GROUP-A",
                test_case_title="重复执行用例",
                test_case_snapshot="{}",
                status="failed",
                start_time=now - timedelta(minutes=2),
                duration_ms=1000,
            ),
            ExecutionRecord(
                id="EX-GROUP-NEW",
                project_id=project.id,
                test_case_id="TC-GROUP-A",
                test_case_title="重复执行用例",
                test_case_snapshot="{}",
                status="passed",
                start_time=now,
                duration_ms=1200,
            ),
            ExecutionRecord(
                id="EX-GROUP-ONE",
                project_id=project.id,
                test_case_id="TC-GROUP-B",
                test_case_title="仅执行一次",
                test_case_snapshot="{}",
                status="failed",
                start_time=now - timedelta(minutes=1),
                duration_ms=800,
            ),
        ]
        session.add_all(records)
        session.commit()
        pid = project.id

    resp = client.get(f"/api/projects/{pid}/executions", params={"groupBy": "case"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["groupedBy"] == "case"
    assert data["total"] == 2
    assert len(data["items"]) == 2
    by_case = {item["testCaseId"]: item for item in data["items"]}
    assert by_case["TC-GROUP-A"]["id"] == "EX-GROUP-NEW"
    assert by_case["TC-GROUP-A"]["status"] == "passed"
    assert by_case["TC-GROUP-A"]["executionCount"] == 2
    assert by_case["TC-GROUP-B"]["executionCount"] == 1

    history = client.get(f"/api/projects/{pid}/testcases/TC-GROUP-A/executions")
    assert history.status_code == 200
    history_items = history.json()["data"]
    assert [item["id"] for item in history_items] == ["EX-GROUP-NEW", "EX-GROUP-OLD"]
    assert history_items[0]["status"] == "passed"
    assert history_items[1]["status"] == "failed"


def test_needs_review_exposes_concrete_review_points():
    Base.metadata.create_all(get_engine())
    with SessionLocal() as session:
        project = Project(name="复核要点测试")
        session.add(project)
        session.flush()
        record = ExecutionRecord(
            id="EX-REVIEW-01",
            project_id=project.id,
            test_case_id="TC-REVIEW-01",
            test_case_title="待复核用例",
            test_case_snapshot="{}",
            status="needs_review",
            start_time=datetime.now(timezone.utc),
            duration_ms=1000,
        )
        review = ExecutionReview(
            execution_id=record.id,
            verdict="unavailable",
            reason="AI 审核调用失败：模型超时",
            model="test-model",
            image_count=2,
            reviewer="llm",
        )
        session.add_all([record, review])
        session.commit()
        pid = project.id
        eid = record.id

    resp = client.get(f"/api/executions/{eid}")
    assert resp.status_code == 200
    review = resp.json()["data"]["llmReview"]
    assert review["verdict"] == "unavailable"
    assert review["issueCount"] == 1
    assert review["issues"][0]["label"] == "AI 复核要点"
    assert "AI 审核调用失败" in review["issues"][0]["problem"]
    assert "重新执行 AI 审核" in review["recommendedAction"]


def test_target_preflight_rejects_http_error_before_browser(monkeypatch):
    def fake_get(url, **_kwargs):
        return httpx.Response(503, request=httpx.Request("GET", url))

    monkeypatch.setattr(execution_service.httpx, "get", fake_get)
    with pytest.raises(BizError) as exc_info:
        execution_service.ensure_target_reachable("http://127.0.0.1:9999")
    assert "HTTP 503" in str(exc_info.value)


def test_target_preflight_rejects_connection_error(monkeypatch):
    def fake_get(url, **_kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(execution_service.httpx, "get", fake_get)
    with pytest.raises(BizError) as exc_info:
        execution_service.ensure_target_reachable("http://127.0.0.1:9999")
    assert "请检查服务是否已启动" in str(exc_info.value)


def test_refresh_step_detection():
    assert _refresh_requested({"type": "ui_action", "action": "刷新页面"})
    assert _refresh_requested({"type": "ui_action", "target": "reload"})
    assert not _refresh_requested({"type": "ui_action", "action": "点击支付"})
    assert not _refresh_requested({"type": "state_assert", "action": "校验跳转后列表总数刷新"})


def test_reload_evidence_uses_navigation_timing():
    calls = []

    class FakePage:
        def reload(self, **_kwargs):
            calls.append("reload")

        def evaluate(self, _script):
            return {"type": "reload", "url": "http://127.0.0.1:8200/orders/list"}

    evidence = _reload_and_verify(FakePage())
    assert calls == ["reload"]
    assert evidence["navigationType"] == "reload"
    assert evidence["url"] == "http://127.0.0.1:8200/orders/list"


def test_reload_failure_is_navigation_error():
    class FakePage:
        def reload(self, **_kwargs):
            pass

        def evaluate(self, _script):
            return {"type": "navigate", "url": "http://127.0.0.1:8200"}

    with pytest.raises(_StepError) as exc_info:
        _reload_and_verify(FakePage())
    assert exc_info.value.category == "NAVIGATION_FAILED"
