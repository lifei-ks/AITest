"""V2 项目管理 API 契约测试：CRUD + PRD 上传/读回 + 级联删除。"""
from __future__ import annotations

import io

from fastapi.testclient import TestClient

from app.main import app

with TestClient(app) as client:
    _ = client.get("/api/health")  # 触发 lifespan 的 create_all


def _create(name="演示项目", source_path="D:/somewhere/src"):
    resp = client.post(
        "/api/projects",
        json={"name": name, "sourceType": "local_path", "sourcePath": source_path},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


def test_create_list_get():
    proj = _create("订单系统")
    assert proj["id"]
    assert proj["targetUrl"] == "http://127.0.0.1:8200"  # 默认靶站地址

    lst = client.get("/api/projects", params={"keyword": "订单"}).json()
    assert lst["code"] == 0
    assert any(p["id"] == proj["id"] for p in lst["data"])

    detail = client.get(f"/api/projects/{proj['id']}").json()["data"]
    assert detail["name"] == "订单系统"
    assert detail["analysisStatus"] is None  # 尚未分析


def test_create_validation_missing_source_path():
    resp = client.post("/api/projects", json={"name": "x", "sourceType": "local_path"})
    assert resp.status_code == 400
    assert resp.json()["code"] == 40002


def test_update_project():
    proj = _create("旧名字")
    resp = client.put(
        f"/api/projects/{proj['id']}",
        json={"name": "新名字", "targetUrl": "http://127.0.0.1:9000"},
    )
    data = resp.json()["data"]
    assert data["name"] == "新名字"
    assert data["targetUrl"] == "http://127.0.0.1:9000"


def test_get_404_envelope():
    resp = client.get("/api/projects/not-exist")
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == 40401
    assert "不存在" in body["message"]


def test_prd_set_and_get():
    proj = _create("带PRD")
    client.put(f"/api/projects/{proj['id']}/prd", json={"content": "需求：支持订单支付"})
    got = client.get(f"/api/projects/{proj['id']}/prd").json()["data"]["content"]
    assert "订单支付" in got


def test_prd_upload_txt_and_reject_bad_ext():
    proj = _create("PRD上传")
    resp = client.post(
        f"/api/projects/{proj['id']}/prd/upload",
        files={"file": ("需求.md", io.BytesIO("需求内容：创建订单".encode()), "text/markdown")},
    )
    assert resp.json()["code"] == 0
    got = client.get(f"/api/projects/{proj['id']}/prd").json()["data"]["content"]
    assert "创建订单" in got

    bad = client.post(
        f"/api/projects/{proj['id']}/prd/upload",
        files={"file": ("virus.exe", io.BytesIO(b"MZ"), "application/octet-stream")},
    )
    assert bad.status_code == 400
    assert bad.json()["code"] == 40004


def test_delete_cascade():
    proj = _create("待删除")
    pid = proj["id"]
    # 造子数据：用例 + 版本 + 执行记录 + 步骤
    from app.database import SessionLocal
    from app.entity.models import ExecutionRecord, ExecutionStep, TestCase, TestCaseVersion

    tc_id = "TC-900001"  # 独立 id，避免与其他测试生成的 TC-000001 冲突
    with SessionLocal() as s:
        s.add(TestCase(id=tc_id, project_seq=1, project_id=pid, title="t"))
        s.add(TestCaseVersion(id="v1", test_case_id=tc_id, version=1, snapshot="{}"))
        s.add(
            ExecutionRecord(
                id="EX-abcd1234",
                project_id=pid,
                test_case_id=tc_id,
                test_case_title="t",
                test_case_snapshot="{}",
            )
        )
        s.add(ExecutionStep(id="st1", execution_id="EX-abcd1234", step_index=1))
        s.commit()

    resp = client.delete(f"/api/projects/{pid}")
    assert resp.json()["code"] == 0
    assert client.get(f"/api/projects/{pid}").status_code == 404

    # 级联校验：子表应无残留
    from sqlalchemy import select

    with SessionLocal() as s:
        assert s.scalars(select(TestCase).where(TestCase.project_id == pid)).all() == []
        assert s.scalars(select(TestCaseVersion).where(TestCaseVersion.test_case_id == tc_id)).all() == []
        assert s.scalars(select(ExecutionRecord).where(ExecutionRecord.project_id == pid)).all() == []
        assert s.scalars(select(ExecutionStep).where(ExecutionStep.execution_id == "EX-abcd1234")).all() == []
