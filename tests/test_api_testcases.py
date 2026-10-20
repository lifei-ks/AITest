"""V5 用例管理 API 测试。"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import Base, SessionLocal, get_engine
from app.entity.models import CodeAnalysis, Project
from app.main import app
from app.service import analysis_service, generation_service

ORDER_SYSTEM = Path(__file__).resolve().parent.parent / "scripts" / "order-system"

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def _setup():
    Base.metadata.create_all(get_engine())
    with client:
        client.get("/api/health")
    with SessionLocal() as s:
        p = Project(name="用例管理测试", source_type="local_path", source_path=str(ORDER_SYSTEM))
        s.add(p)
        s.commit()
        pid = p.id
        a = CodeAnalysis(project_id=pid, status="running")
        s.add(a)
        s.commit()
        aid = a.id
    analysis_service.run_analysis(pid, aid)
    generation_service.generate(pid, "rule")
    yield
    # 清理
    with SessionLocal() as s:
        from app.service.project_service import delete_project
        delete_project(s, pid)
        s.commit()


def _pid():
    with SessionLocal() as s:
        p = s.scalars(select(Project).where(Project.name == "用例管理测试")).first()
        return p.id


def test_list_and_filter():
    pid = _pid()
    r = client.get(f"/api/projects/{pid}/testcases", params={"pageSize": 100}).json()["data"]
    assert r["total"] >= 12
    assert r["items"] == r["testCases"]  # 两个键同时返回

    f = client.get(f"/api/projects/{pid}/testcases", params={"type": "state", "pageSize": 100}).json()["data"]
    assert all(t["type"] == "state" for t in f["items"])
    assert f["total"] > 0


def test_update_creates_version_and_reruns_gate():
    pid = _pid()
    r = client.get(f"/api/projects/{pid}/testcases", params={"pageSize": 1}).json()["data"]
    cid = r["items"][0]["id"]

    upd = client.put(f"/api/projects/{pid}/testcases/{cid}", json={"priority": "P0"}).json()["data"]
    assert upd["priority"] == "P0"

    versions = client.get(f"/api/projects/{pid}/testcases/{cid}/versions").json()["data"]
    assert len(versions) >= 1


def test_rollback():
    pid = _pid()
    r = client.get(f"/api/projects/{pid}/testcases", params={"pageSize": 1}).json()["data"]
    cid = r["items"][0]["id"]
    versions = client.get(f"/api/projects/{pid}/testcases/{cid}/versions").json()["data"]
    vid = versions[0]["id"]
    resp = client.post(f"/api/projects/{pid}/testcases/{cid}/versions/{vid}/rollback").json()
    assert resp["code"] == 0


def test_csv_export_import():
    pid = _pid()
    export = client.get(f"/api/projects/{pid}/testcases/export").content.decode("utf-8-sig")
    assert "标题" in export and "模块" in export

    # 导入到新项目
    with SessionLocal() as s:
        p2 = Project(name="CSV导入目标", source_type="local_path", source_path="D:/x")
        s.add(p2)
        s.commit()
        pid2 = p2.id
    resp = client.post(f"/api/projects/{pid2}/testcases/import", files={"file": ("t.csv", export.encode("utf-8"))})
    assert resp.json()["data"]["imported"] > 0


def test_copy_to_and_delete():
    pid = _pid()
    r = client.get(f"/api/projects/{pid}/testcases", params={"pageSize": 3}).json()["data"]
    ids = [t["id"] for t in r["items"]]

    with SessionLocal() as s:
        p2 = Project(name="复制目标", source_type="local_path", source_path="D:/x")
        s.add(p2)
        s.commit()
        pid2 = p2.id

    cp = client.post(f"/api/projects/{pid}/testcases/copy-to", json={"ids": ids, "targetProjectId": pid2}).json()
    assert cp["data"]["copied"] == len(ids)

    # 批量删除
    before = client.get(f"/api/projects/{pid}/testcases", params={"pageSize": 100}).json()["data"]["total"]
    client.request("DELETE", f"/api/projects/{pid}/testcases/batch", json={"ids": ids})
    after = client.get(f"/api/projects/{pid}/testcases", params={"pageSize": 100}).json()["data"]["total"]
    assert after == before - len(ids)


def test_execution_status():
    pid = _pid()
    r = client.get(f"/api/projects/{pid}/testcases", params={"pageSize": 1}).json()["data"]
    cid = r["items"][0]["id"]
    client.put(f"/api/projects/{pid}/testcases/{cid}/execution-status", json={"status": "passed"})
    detail = client.get(f"/api/projects/{pid}/testcases/{cid}").json()["data"]
    assert detail["executionStatus"] == "passed"
