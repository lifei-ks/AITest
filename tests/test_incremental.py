"""多期需求增量分析：变更识别、增量生成、复用、回归推荐与数据保护。"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select

from app.common.errors import BizError
from app.database import Base, SessionLocal, get_engine
from app.entity.models import (
    ExecutionRecord,
    Project,
    Requirement,
    RequirementCase,
    RequirementCaseChange,
    RequirementChange,
    RequirementSnapshot,
    TestCase,
    TestPoint,
)
from app.service import incremental_service


def _tag() -> str:
    return uuid.uuid4().hex[:8]


def _project_requirement(tag: str, content: str = "二期需求", version: str = "V2.0"):
    project = Project(
        id=uuid.uuid4().hex,
        name=f"incremental-{tag}",
        source_type="local_path",
        source_path=".",
        target_url="http://127.0.0.1:8200",
    )
    requirement = Requirement(
        id=uuid.uuid4().hex,
        project_id=project.id,
        name=f"需求-{tag}",
        version=version,
        content=content,
        status="ready",
    )
    return project, requirement


def _endpoint(
    eid: str,
    method: str,
    path: str,
    *,
    method_name: str = "",
    params: list[dict] | None = None,
    auth: list[str] | None = None,
    rules: list[dict] | None = None,
) -> dict:
    return {
        "id": eid,
        "method": method,
        "path": path,
        "normalizedPath": incremental_service.normalize_path(path),
        "controller": "OrderController",
        "methodName": method_name or path.strip("/").replace("/", "_"),
        "file": "OrderController.java",
        "line": 10,
        "params": params or [],
        "auth": auth or [],
        "rules": rules or [],
        "fingerprint": eid,
    }


def _param(name: str, required: bool, constraints: list[str] | None = None) -> dict:
    return {
        "name": name,
        "type": "String",
        "source": "request_param",
        "required": required,
        "constraints": constraints or [],
    }


def _rule(rid: str, description: str, guard: str = "x > 0") -> dict:
    return {
        "id": rid,
        "description": description,
        "guard": guard,
        "file": "OrderController.java",
        "line": 10,
        "key": f"{description}|{guard}".lower(),
    }


def _snapshot(session, requirement, endpoints, *, rules=None, transitions=None, security=None):
    row = RequirementSnapshot(
        id=uuid.uuid4().hex,
        project_id=requirement.project_id,
        requirement_id=requirement.id,
        source="manual",
        endpoints=json.dumps(endpoints, ensure_ascii=False),
        rules=json.dumps(rules or [], ensure_ascii=False),
        state_transitions=json.dumps(transitions or [], ensure_ascii=False),
        security=json.dumps(
            security or {"available": True, "hash": "same", "signals": []},
            ensure_ascii=False,
        ),
        warnings="[]",
        fingerprint=uuid.uuid4().hex,
    )
    session.add(row)
    session.flush()
    return row


def _baseline_case(session, requirement, endpoint_id: str, title: str, seq: int = 1):
    point = TestPoint(
        requirement_id=requirement.id,
        project_id=requirement.project_id,
        seq=seq,
        title=title,
        category="functional",
        priority="P0",
        endpoint=f"GET {endpoint_id}",
        page_route="/orders/list",
        expected="「操作成功」",
    )
    case = TestCase(
        id=f"TC-INC-{_tag()}",
        project_seq=seq,
        project_id=requirement.project_id,
        title=title,
        module="订单",
        type="positive",
        priority="P0",
        expected_results=json.dumps(["「操作成功」"], ensure_ascii=False),
        steps=json.dumps(["打开页面"], ensure_ascii=False),
        structured_steps=json.dumps([], ensure_ascii=False),
        test_data="{}",
        coverage_refs=json.dumps({"endpointIds": [endpoint_id]}, ensure_ascii=False),
        source="ai",
    )
    session.add_all([point, case])
    session.flush()
    session.add(
        RequirementCase(
            requirement_id=requirement.id,
            test_case_id=case.id,
            test_point_id=point.id,
        )
    )
    session.flush()
    return case


def _setup_with_snapshots(session, *, baseline_eps, current_eps, baseline_rules=None,
                          current_rules=None, baseline_security=None, current_security=None,
                          content="二期需求"):
    project, baseline = _project_requirement("base", content="一期需求", version="V1.0")
    current = Requirement(
        project_id=project.id,
        name="二期需求",
        version="V2.0",
        content=content,
        status="ready",
    )
    session.add_all([project, baseline, current])
    session.flush()
    _snapshot(session, baseline, baseline_eps, rules=baseline_rules, security=baseline_security)
    _snapshot(session, current, current_eps, rules=current_rules, security=current_security)
    return project, baseline, current


def _change_by_type(session, run_id: str, entity_type: str):
    return list(
        session.scalars(
            select(RequirementChange).where(
                RequirementChange.run_id == run_id,
                RequirementChange.entity_type == entity_type,
            )
        )
    )


def test_only_added_endpoints_generate_new_cases():
    Base.metadata.create_all(get_engine())
    tag = _tag()
    baseline_eps = [
        _endpoint(f"ep-{i}", "GET", f"/api/items/item{i}", method_name=f"list{i}")
        for i in range(1, 21)
    ]
    added_eps = [
        _endpoint("ep-21", "POST", "/api/items", method_name="createItem", params=[_param("name", True)]),
        _endpoint("ep-22", "DELETE", "/api/items/{id}", method_name="deleteItem"),
    ]
    with SessionLocal() as session:
        project, baseline, current = _setup_with_snapshots(
            session,
            baseline_eps=baseline_eps,
            current_eps=baseline_eps + added_eps,
            content="二期新增 POST /api/items 和 DELETE /api/items/{id}",
        )
        for index, ep in enumerate(baseline_eps, start=1):
            _baseline_case(session, baseline, ep["id"], f"历史用例-{index}", seq=index)
        session.commit()
        run = incremental_service.analyze_incremental(session, current.id, baseline.id)
        session.commit()
        run_id = run["id"]
        changes = _change_by_type(session, run_id, "endpoint")
        assert sum(1 for c in changes if c.change_type == "added") == 2
        assert sum(1 for c in changes if c.change_type == "unchanged") == 20

        result = incremental_service.generate_incremental(session, run_id, mode="rule")
        session.commit()
        assert result["status"] == "completed"
        rows = list(session.scalars(select(RequirementCaseChange).where(RequirementCaseChange.run_id == run_id)))
        added_ids = {c.id for c in changes if c.change_type == "added"}
        created_for_added = [r for r in rows if r.action == "created" and r.change_id in added_ids]
        assert created_for_added, "新增接口必须生成用例"
        assert not [r for r in rows if r.action == "created" and r.change_id not in added_ids]
        assert sum(1 for r in rows if r.action == "reused") == 20
        # 一期数据未被覆盖：20 条历史用例仍存在
        v1_ids = {case.id for case in session.scalars(select(TestCase).where(TestCase.project_id == project.id))}
        assert len(v1_ids) >= 20


def test_modified_required_param_updates_affected_case():
    Base.metadata.create_all(get_engine())
    old_ep = _endpoint(
        "ep-1", "POST", "/api/orders", method_name="createOrder",
        params=[_param("receiverName", True), _param("phone", False)],
    )
    new_ep = _endpoint(
        "ep-1", "POST", "/api/orders", method_name="createOrder",
        params=[_param("receiverName", True), _param("phone", True)],
    )
    with SessionLocal() as session:
        project, baseline, current = _setup_with_snapshots(
            session,
            baseline_eps=[old_ep],
            current_eps=[new_ep],
            content="二期将 phone 改为必填",
        )
        old_case = _baseline_case(session, baseline, "ep-1", "创建订单历史用例")
        session.commit()
        run = incremental_service.analyze_incremental(session, current.id, baseline.id)
        session.commit()
        modified = _change_by_type(session, run["id"], "endpoint")
        assert len(modified) == 1 and modified[0].change_type == "modified"
        assert "必填性变化" in (modified[0].reason or "")

        incremental_service.generate_incremental(session, run["id"], mode="rule")
        session.commit()
        rows = list(session.scalars(select(RequirementCaseChange).where(RequirementCaseChange.run_id == run["id"])))
        assert any(r.action == "updated" and r.baseline_case_id == old_case.id for r in rows)
        assert session.get(TestCase, old_case.id) is not None
        v2_case_ids = set(session.scalars(
            select(RequirementCase.test_case_id).where(RequirementCase.requirement_id == current.id)
        ))
        assert old_case.id not in v2_case_ids, "历史用例不能被直接搬进二期"
        assert v2_case_ids


def test_unchanged_endpoint_reuses_case_without_new_generation():
    Base.metadata.create_all(get_engine())
    ep = _endpoint("ep-1", "GET", "/api/orders", method_name="listOrders")
    with SessionLocal() as session:
        project, baseline, current = _setup_with_snapshots(
            session, baseline_eps=[ep], current_eps=[ep], content="二期无接口变化"
        )
        old_case = _baseline_case(session, baseline, "ep-1", "列表历史用例")
        session.commit()
        run = incremental_service.analyze_incremental(session, current.id, baseline.id)
        session.commit()
        changes = _change_by_type(session, run["id"], "endpoint")
        assert len(changes) == 1 and changes[0].change_type == "unchanged"

        incremental_service.generate_incremental(session, run["id"], mode="rule")
        session.commit()
        rows = list(session.scalars(select(RequirementCaseChange).where(RequirementCaseChange.run_id == run["id"])))
        assert len(rows) == 1 and rows[0].action == "reused"
        assert rows[0].baseline_case_id == old_case.id
        v2_case = session.get(TestCase, rows[0].test_case_id)
        assert v2_case is not None and v2_case.title == old_case.title
        assert session.get(TestCase, old_case.id) is not None


def test_public_auth_change_recommends_indirect_regression():
    Base.metadata.create_all(get_engine())
    ep = _endpoint("ep-1", "GET", "/api/orders", method_name="listOrders")
    with SessionLocal() as session:
        project, baseline, current = _setup_with_snapshots(
            session,
            baseline_eps=[ep],
            current_eps=[ep],
            baseline_security={"available": True, "hash": "role-admin", "signals": []},
            current_security={"available": True, "hash": "role-user", "signals": []},
            content="二期调整公共鉴权规则",
        )
        old_case = _baseline_case(session, baseline, "ep-1", "列表历史用例")
        session.commit()
        run = incremental_service.analyze_incremental(session, current.id, baseline.id)
        session.commit()
        assert any(c.entity_type == "permission" for c in _change_by_type(session, run["id"], "permission"))

        incremental_service.generate_incremental(session, run["id"], mode="rule")
        session.commit()
        rows = list(session.scalars(select(RequirementCaseChange).where(RequirementCaseChange.run_id == run["id"])))
        row = next(r for r in rows if r.baseline_case_id == old_case.id)
        assert row.regression_impact == "indirect"
        assert "鉴权" in (row.regression_reason or "")


def test_unmentioned_old_endpoint_is_not_deleted():
    Base.metadata.create_all(get_engine())
    old_ep = _endpoint("ep-old", "GET", "/api/old", method_name="oldList")
    new_ep = _endpoint("ep-new", "GET", "/api/new", method_name="newList")
    with SessionLocal() as session:
        project, baseline, current = _setup_with_snapshots(
            session,
            baseline_eps=[old_ep],
            current_eps=[new_ep],
            content="本期新增 GET /api/new",
        )
        old_case = _baseline_case(session, baseline, "ep-old", "旧接口历史用例")
        session.commit()
        run = incremental_service.analyze_incremental(session, current.id, baseline.id)
        session.commit()
        changes = _change_by_type(session, run["id"], "endpoint")
        old_change = next(c for c in changes if c.old_ref == "ep-old")
        assert old_change.change_type == "uncertain"
        assert not any(c.change_type == "deleted" for c in changes)


def test_explicit_delete_preserves_cases_and_execution_records():
    Base.metadata.create_all(get_engine())
    old_ep = _endpoint("ep-old", "GET", "/api/old", method_name="oldList")
    new_ep = _endpoint("ep-new", "GET", "/api/new", method_name="newList")
    with SessionLocal() as session:
        project, baseline, current = _setup_with_snapshots(
            session,
            baseline_eps=[old_ep],
            current_eps=[new_ep],
            content="本期删除 GET /api/old，新增 GET /api/new",
        )
        old_case = _baseline_case(session, baseline, "ep-old", "旧接口历史用例")
        session.add(ExecutionRecord(
            id=f"EX-{_tag()[:12]}",
            project_id=project.id,
            test_case_id=old_case.id,
            test_case_title=old_case.title,
            test_case_snapshot="{}",
            status="passed",
        ))
        session.commit()
        run = incremental_service.analyze_incremental(session, current.id, baseline.id)
        session.commit()
        deleted = next(
            c for c in _change_by_type(session, run["id"], "endpoint")
            if c.old_ref == "ep-old"
        )
        assert deleted.change_type == "deleted"

        incremental_service.generate_incremental(session, run["id"], mode="rule")
        session.commit()
        rows = list(session.scalars(select(RequirementCaseChange).where(RequirementCaseChange.run_id == run["id"])))
        pending = next(r for r in rows if r.baseline_case_id == old_case.id and r.action == "pending_delete")
        assert pending.review_status == "pending"
        assert session.get(TestCase, old_case.id) is not None
        assert session.scalars(select(ExecutionRecord).where(ExecutionRecord.test_case_id == old_case.id)).first() is not None

        incremental_service.review_change(session, run["id"], deleted.id, "confirmed")
        session.commit()
        assert session.get(RequirementCaseChange, pending.id).review_status == "confirmed"
        assert session.get(TestCase, old_case.id) is not None


def test_repeated_generation_is_idempotent():
    Base.metadata.create_all(get_engine())
    ep = _endpoint("ep-1", "GET", "/api/orders", method_name="listOrders")
    added = _endpoint("ep-2", "POST", "/api/orders", method_name="createOrder")
    with SessionLocal() as session:
        project, baseline, current = _setup_with_snapshots(
            session, baseline_eps=[ep], current_eps=[ep, added], content="新增 POST /api/orders"
        )
        _baseline_case(session, baseline, "ep-1", "列表历史用例")
        session.commit()
        run = incremental_service.analyze_incremental(session, current.id, baseline.id)
        session.commit()
        incremental_service.generate_incremental(session, run["id"], mode="rule")
        session.commit()
        first_count = len(list(session.scalars(
            select(RequirementCase).where(RequirementCase.requirement_id == current.id)
        )))
        first_rows = len(list(session.scalars(
            select(RequirementCaseChange).where(RequirementCaseChange.run_id == run["id"])
        )))
        incremental_service.generate_incremental(session, run["id"], mode="rule")
        session.commit()
        second_count = len(list(session.scalars(
            select(RequirementCase).where(RequirementCase.requirement_id == current.id)
        )))
        second_rows = len(list(session.scalars(
            select(RequirementCaseChange).where(RequirementCaseChange.run_id == run["id"])
        )))
        assert second_count == first_count
        assert second_rows == first_rows


def test_missing_baseline_snapshot_has_clear_error():
    Base.metadata.create_all(get_engine())
    with SessionLocal() as session:
        project, baseline = _project_requirement("nobase", version="V1.0")
        current = Requirement(
            project_id=project.id, name="二期", version="V2.0", content="二期需求", status="ready"
        )
        session.add_all([project, baseline, current])
        session.flush()
        _snapshot(session, current, [_endpoint("ep-1", "GET", "/api/orders")])
        session.commit()
        with pytest.raises(BizError) as exc:
            incremental_service.analyze_incremental(session, current.id, baseline.id)
        assert exc.value.code == 40950
        assert "快照" in str(exc.value)


def test_document_code_conflict_is_reported():
    Base.metadata.create_all(get_engine())
    ep = _endpoint("ep-1", "GET", "/api/orders", method_name="listOrders")
    with SessionLocal() as session:
        project, baseline, current = _setup_with_snapshots(
            session,
            baseline_eps=[ep],
            current_eps=[ep],
            content="本期新增 GET /api/ghost 接口",
        )
        _baseline_case(session, baseline, "ep-1", "列表历史用例")
        session.commit()
        run = incremental_service.analyze_incremental(session, current.id, baseline.id)
        session.commit()
        conflicts = _change_by_type(session, run["id"], "conflict")
        assert conflicts
        assert any("代码分析未找到" in (c.reason or "") for c in conflicts)
        incremental_service.generate_incremental(session, run["id"], mode="rule")
        session.commit()
        titles = [
            case.title for case in session.scalars(
                select(TestCase).where(TestCase.project_id == project.id)
            )
        ]
        assert not any("ghost" in title.lower() for title in titles)


def test_generation_failure_does_not_damage_baseline(monkeypatch):
    Base.metadata.create_all(get_engine())
    ep = _endpoint("ep-1", "GET", "/api/orders", method_name="listOrders")
    added = _endpoint("ep-2", "POST", "/api/orders", method_name="createOrder")
    with SessionLocal() as session:
        project, baseline, current = _setup_with_snapshots(
            session, baseline_eps=[ep], current_eps=[ep, added], content="新增 POST /api/orders"
        )
        old_case = _baseline_case(session, baseline, "ep-1", "列表历史用例")
        session.commit()
        run = incremental_service.analyze_incremental(session, current.id, baseline.id)
        session.commit()

        def boom(*args, **kwargs):
            raise RuntimeError("mock save failure")

        monkeypatch.setattr(incremental_service.generation_service, "_save_case", boom)
        with pytest.raises(BizError):
            incremental_service.generate_incremental(session, run["id"], mode="rule")
        session.rollback()
        assert session.get(TestCase, old_case.id) is not None
        assert not list(session.scalars(
            select(RequirementCase).where(RequirementCase.requirement_id == current.id)
        ))
        failed = session.get(incremental_service.RequirementChangeRun, run["id"])
        assert failed is not None and failed.status == "failed"


def test_incremental_api_smoke():
    from fastapi.testclient import TestClient
    from app.main import app

    Base.metadata.create_all(get_engine())
    ep = _endpoint("ep-1", "GET", "/api/orders", method_name="listOrders")
    added = _endpoint("ep-2", "POST", "/api/orders", method_name="createOrder")
    with SessionLocal() as session:
        project, baseline, current = _setup_with_snapshots(
            session, baseline_eps=[ep], current_eps=[ep, added], content="新增 POST /api/orders"
        )
        _baseline_case(session, baseline, "ep-1", "列表历史用例")
        session.commit()
        baseline_id, current_id = baseline.id, current.id

    with TestClient(app) as client:
        snap = client.get(f"/api/requirements/{current_id}/snapshot")
        assert snap.status_code == 200 and snap.json()["data"]["endpoints"]

        resp = client.post(
            f"/api/requirements/{current_id}/incremental/analyze",
            json={"baselineRequirementId": baseline_id, "mode": "rule"},
        )
        assert resp.status_code == 200, resp.text
        run = resp.json()["data"]
        assert run["summary"]["endpointCounts"]["added"] == 1
        assert run["id"]

        detail = client.get(f"/api/incremental/runs/{run['id']}")
        assert detail.status_code == 200
        assert detail.json()["data"]["changes"]

        gen = client.post(
            f"/api/incremental/runs/{run['id']}/generate",
            json={"mode": "rule"},
        )
        assert gen.status_code == 200, gen.text
        data = gen.json()["data"]
        assert data["status"] == "completed"
        assert data["summary"]["caseChanges"]["created"] >= 1

    from app.entity.models import CaseGeneration
    with SessionLocal() as session:
        generation = session.scalars(
            select(CaseGeneration).where(
                CaseGeneration.requirement_id == current_id,
                CaseGeneration.mode == "incremental",
            )
        ).first()
        assert generation is not None
        assert generation.case_count >= 1


def test_snapshot_builder_keeps_params_and_auth(tmp_path):
    from app.entity.models import CodeAnalysis, Project as ProjectModel

    Base.metadata.create_all(get_engine())
    with SessionLocal() as session:
        project = ProjectModel(
            id=uuid.uuid4().hex,
            name=f"snapshot-{_tag()}",
            source_type="local_path",
            source_path=str(tmp_path),
        )
        requirement = Requirement(
            id=uuid.uuid4().hex,
            project_id=project.id,
            name="快照需求",
            version="V1.0",
            content="订单接口",
            status="ready",
        )
        backend = {
            "language": "java-spring",
            "endpoints": [{
                "id": "ep-1",
                "method": "POST",
                "path": "/api/orders",
                "controller": "OrderController",
                "methodName": "create",
                "file": "OrderController.java",
                "line": 10,
                "params": [_param("phone", True)],
                "auth": ["@PreAuthorize('hasRole(ADMIN)')"],
            }],
            "entities": [],
            "enums": [],
            "businessRules": [
                {k: v for k, v in _rule("br-1", "手机号必填").items() if k != "key"}
            ],
            "uiTexts": ["订单创建成功"],
        }
        session.add_all([project, requirement, CodeAnalysis(
            project_id=project.id,
            status="success",
            backend_result=json.dumps(backend, ensure_ascii=False),
            frontend_result="{}",
        )])
        session.commit()
        snap = incremental_service.build_snapshot(session, requirement.id)
        endpoint = snap["endpoints"][0]
        assert endpoint["params"][0]["name"] == "phone"
        assert endpoint["params"][0]["required"] is True
        assert endpoint["auth"] == ["@PreAuthorize('hasRole(ADMIN)')"]
        assert snap["rules"][0]["description"] == "手机号必填"
