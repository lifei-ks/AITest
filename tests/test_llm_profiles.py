"""多 LLM 配置：CRUD、默认项、掩码与按 profile 解析客户端。"""
from __future__ import annotations

import uuid

import pytest

from fastapi.testclient import TestClient

from app.database import Base, SessionLocal, get_engine
from app.entity.models import LlmProfile
from app.service import settings_service


def _name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def test_llm_profile_crud_mask_default_and_client_resolution():
    from app.main import app

    created: list[str] = []
    with TestClient(app) as client:
        base = client.get("/api/settings/llm-profiles")
        assert base.status_code == 200
        assert isinstance(base.json()["data"], list)

        key_a = "sk-test-profile-a-123456789"
        resp = client.post(
            "/api/settings/llm-profiles",
            json={
                "name": _name("text-a"),
                "kind": "text",
                "apiKey": key_a,
                "baseUrl": "https://a.example/v1",
                "model": "model-a",
                "isDefault": True,
            },
        )
        assert resp.status_code == 201, resp.text
        profile_a = resp.json()["data"]
        created.append(profile_a["id"])
        assert profile_a["isDefault"] is True
        assert key_a not in profile_a["apiKey"]
        assert "***" in profile_a["apiKey"]

        resp = client.post(
            "/api/settings/llm-profiles",
            json={
                "name": _name("text-b"),
                "kind": "text",
                "apiKey": "sk-test-profile-b-987654321",
                "baseUrl": "https://b.example/v1",
                "model": "model-b",
            },
        )
        profile_b = resp.json()["data"]
        created.append(profile_b["id"])

        # 掩码回传 = 保留原 Key，同时更新其他字段
        resp = client.put(
            f"/api/settings/llm-profiles/{profile_a['id']}",
            json={"apiKey": profile_a["apiKey"], "baseUrl": "https://a2.example/v1"},
        )
        assert resp.status_code == 200, resp.text
        with SessionLocal() as session:
            row = session.get(LlmProfile, profile_a["id"])
            assert row.api_key == key_a
            assert row.base_url == "https://a2.example/v1"

        # 显式选择 profile 时，客户端必须使用该 Key/URL/模型
        resolved = settings_service.get_text_client(profile_a["id"])
        assert resolved.api_key == key_a
        assert resolved.base_url == "https://a2.example/v1"
        assert resolved.model == "model-a"

        # 默认项唯一
        resp = client.post(f"/api/settings/llm-profiles/{profile_b['id']}/default")
        assert resp.status_code == 200
        rows = {item["id"]: item for item in client.get("/api/settings/llm-profiles").json()["data"]}
        assert rows[profile_b["id"]]["isDefault"] is True
        assert rows[profile_a["id"]]["isDefault"] is False

        vision = client.post(
            "/api/settings/llm-profiles",
            json={
                "name": _name("vision-a"),
                "kind": "vision",
                "apiKey": "sk-test-vision-123456",
                "baseUrl": "https://v.example/v1",
                "model": "vlm-a",
            },
        ).json()["data"]
        created.append(vision["id"])
        assert vision["kind"] == "vision"

        info = client.get("/api/settings").json()["data"]
        assert info["profiles"]
        assert info["defaultTextProfileId"] == profile_b["id"]

        # 清理：停用测试创建项，避免影响后续测试的默认解析
        for profile_id in created:
            client.put(
                f"/api/settings/llm-profiles/{profile_id}",
                json={"enabled": False},
            )


def test_connectivity_test_uses_text_ping(monkeypatch):
    """推理模型（只有 reasoning_content）不能被 JSON 测试误杀。"""
    calls = []

    class FakeClient:
        model = "glm-5.3-flash"

        def available(self):
            return True

        def chat_text(self, messages, max_tokens=256):
            calls.append(("text", max_tokens))
            return "ok"

        def chat_json(self, *args, **kwargs):
            raise AssertionError("连通性测试不应强制 JSON")

    monkeypatch.setattr(settings_service, "get_text_client", lambda *a, **k: FakeClient())
    result = settings_service.test_llm()
    assert result == {"ok": True, "model": "glm-5.3-flash"}
    assert calls and calls[0][0] == "text"


def test_chat_json_does_not_use_reasoning_content_as_final_answer(monkeypatch):
    import httpx
    from app.llm import client as client_module
    from app.llm.client import LlmClient, LlmUnavailable

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "reasoning_content": "思考过程，不是最终答案",
                        }
                    }
                ]
            }

    monkeypatch.setattr(client_module.httpx, "post", lambda *a, **k: FakeResponse())
    client = LlmClient("fake-key", "https://example.invalid/v1", "reasoning-model")
    with pytest.raises(LlmUnavailable):
        client.chat_json([{"role": "user", "content": "x"}])
    # 只有连通性探测允许把 reasoning 作为“有响应”的证据
    assert client.chat_text([{"role": "user", "content": "ping"}]) == "思考过程，不是最终答案"


def test_legacy_masked_update_keeps_db_key(monkeypatch):
    Base.metadata.create_all(get_engine())
    monkeypatch.setattr(settings_service, "_update_env", lambda updates: None)
    with SessionLocal() as session:
        session.query(LlmProfile).filter(LlmProfile.kind == "text").update({LlmProfile.is_default: 0})
        profile = settings_service._new_profile(
            name=_name("masked-keep"),
            kind="text",
            api_key="sk-db-should-stay",
            base_url="https://db.example/v1",
            model="db-model",
            is_default=True,
        )
        session.add(profile)
        session.commit()
        profile_id = profile.id

    settings_service.update(
        {"llmApiKey": "sk-d***stay", "llmBaseUrl": "https://new.example/v1"}
    )
    with SessionLocal() as session:
        row = session.get(LlmProfile, profile_id)
        assert row.api_key == "sk-db-should-stay"
        assert row.base_url == "https://new.example/v1"


def test_vision_connectivity_sends_an_image(monkeypatch):
    calls = []

    class FakeClient:
        model = "vlm"

        def available(self):
            return True

        def chat_text(self, messages, max_tokens=256):
            calls.append(messages)
            return "ok"

    monkeypatch.setattr(settings_service, "get_vision_client", lambda: FakeClient())
    result = settings_service.test_llm(kind="vision")
    assert result["ok"] is True
    content = calls[0][0]["content"]
    assert isinstance(content, list)
    assert any(part.get("type") == "image_url" for part in content)
