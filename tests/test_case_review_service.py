"""AI 质检服务：确定性检查接入与 facts 缓存隔离。"""
from __future__ import annotations

import json
import uuid

from app.database import Base, SessionLocal, get_engine
from app.entity.models import (
    CodeAnalysis,
    Project,
    Requirement,
    RequirementCase,
    TestCase,
    TestPoint,
)
from app.service import case_review_service


def _steps(title: str) -> list[dict]:
    return [
        {
            "type": "ui_action",
            "action": "打开新增页面",
            "uiSelector": {"type": "route", "value": "/orders/new"},
        },
        {
            "type": "input",
            "action": "输入收货人",
            "target": "收货人",
            "uiSelector": {"type": "placeholder", "value": "请输入收货人"},
            "inputValue": title,
        },
        {
            "type": "state_assert",
            "action": "验证创建成功",
            "expected": "「订单创建成功」",
        },
    ]


def _prepare(monkeypatch):
    Base.metadata.create_all(get_engine())
    with SessionLocal() as session:
        project = Project(
            name=f"review-{uuid.uuid4().hex[:6]}",
            source_type="local_path",
            source_path=".",
        )
        session.add(project)
        session.flush()
        session.add(
            CodeAnalysis(
                project_id=project.id,
                status="success",
                backend_result=json.dumps({
                    "language": "java-spring",
                    "endpoints": [],
                    "entities": [{
                        "name": "Order",
                        "file": "",
                        "fields": [],
                        "fieldConstraints": [
                            {"field": "receiverName", "rule": "size", "value": 20}
                        ],
                    }],
                    "enums": [],
                    "businessRules": [],
                    "uiTexts": [],
                }, ensure_ascii=False),
                frontend_result=json.dumps({
                    "language": "vue",
                    "pages": [{
                        "name": "新增订单",
                        "route": "/orders/new",
                        "file": "",
                        "forms": [{
                            "fields": [{
                                "label": "收货人",
                                "prop": "receiverName",
                                "type": "input",
                                "placeholder": "请输入收货人",
                                "required": True,
                            }]
                        }],
                    }],
                    "selectors": [{"type": "text", "value": "提交", "page": "/orders/new"}],
                    "uiTexts": ["订单创建成功"],
                    "components": [],
                }, ensure_ascii=False),
            )
        )
        requirement = Requirement(
            project_id=project.id,
            name="订单主流程",
            version="V1",
            content="创建订单提示「订单创建成功」",
            status="ready",
        )
        session.add(requirement)
        session.flush()
        points = [
            TestPoint(
                requirement_id=requirement.id,
                project_id=project.id,
                title=f"测试点{i}",
                category="functional",
                expected="「订单创建成功」",
            )
            for i in (1, 2)
        ]
        session.add_all(points)
        session.flush()
        cases = []
        for index, point in enumerate(points, 1):
            case = TestCase(
                id=f"TC-{uuid.uuid4().hex[:8].upper()}",
                project_seq=index,
                project_id=project.id,
                title=f"新增订单-{index}",
                module="订单",
                type="positive",
                priority="P1",
                structured_steps=json.dumps(_steps(f"张三{index}"), ensure_ascii=False),
                expected_results=json.dumps(["订单创建成功"], ensure_ascii=False),
                coverage_refs=json.dumps({"testPointIds": [point.id]}),
                source="rule",
            )
            session.add(case)
            session.flush()
            session.add(
                RequirementCase(
                    requirement_id=requirement.id,
                    test_case_id=case.id,
                    test_point_id=point.id,
                )
            )
            cases.append(case.id)
        session.commit()
        return project.id, cases


def test_review_cases_uses_one_facts_entry_per_test_point(monkeypatch):
    pid, case_ids = _prepare(monkeypatch)
    seen_points: list[str] = []

    class FakeClient:
        model = "fake-model"

        def available(self):
            return True

    def fake_review(client, case, facts, deterministic=None):
        seen_points.append(facts["testPoint"]["id"])
        return {"verdict": "ok", "reason": "通过", "issues": [], "stepResults": []}

    monkeypatch.setattr(
        case_review_service.settings_service,
        "get_text_client",
        lambda: FakeClient(),
    )
    monkeypatch.setattr(
        case_review_service.case_review_agent,
        "review_case",
        fake_review,
    )

    with SessionLocal() as session:
        result = case_review_service.review_cases(session, pid, case_ids)
        session.commit()

    assert result["reviewed"] == 2
    assert len(set(seen_points)) == 2
    assert all("stepResults" in item for item in result["results"])
    assert all(item["qualityScore"] is not None for item in result["results"])
    with SessionLocal() as session:
        for cid in case_ids:
            tc = session.get(TestCase, cid)
            assert tc.last_review_verdict in ("pass", "revise", "reject")
            assert tc.last_review_score is not None
            assert tc.last_reviewed_at is not None


def test_review_cases_returns_deterministic_result_when_llm_unavailable(monkeypatch):
    pid, case_ids = _prepare(monkeypatch)

    class OfflineClient:
        model = ""

        def available(self):
            return False

    monkeypatch.setattr(
        case_review_service.settings_service,
        "get_text_client",
        lambda: OfflineClient(),
    )

    with SessionLocal() as session:
        result = case_review_service.review_cases(session, pid, case_ids[:1])

    item = result["results"][0]
    assert item["llmAvailable"] is False
    assert item["deterministicVerdict"] in ("pass", "revise", "reject")
    assert item["stepResults"]
