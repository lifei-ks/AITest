"""V1 冒烟测试：health 接口与统一信封契约。

DATABASE_URL 由 tests/conftest.py 在导入任何 app 模块前注入临时库。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_envelope_code_zero():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["message"] == "ok"
    assert body["data"]["status"] == "up"


def test_validation_error_returns_400_envelope():
    # 用一个不存在的路由不足以触发校验；构造校验失败：query 参数缺省不适用，
    # V1 尚无带必填参数的接口，此处用一个临时路由验证全局处理器已注册。
    from fastapi import Query

    @app.get("/api/_smoke/echo")
    def _echo(value: int = Query(...)):
        return {"value": value}

    resp = client.get("/api/_smoke/echo")  # 缺必填参数
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == 40001
    assert "参数校验失败" in body["message"]


def test_execution_metadata_exposes_labels_and_descriptions():
    resp = client.get("/api/meta/execution")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["caseExecutionStatuses"]["needs_review"]["label"] == "待复核"
    assert data["errorCategories"]["TIMEOUT"]["label"] == "步骤超时"
    assert data["aiReviewVerdicts"]["unavailable"]["description"]
