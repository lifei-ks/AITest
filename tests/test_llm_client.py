"""LLM 客户端：不同模型的 content/reasoning 形态、推理预算与参数回退。"""
from __future__ import annotations

import json

import pytest

from app.llm import client as client_module
from app.llm.client import LlmClient, LlmUnavailable


class _Resp:
    def __init__(self, status_code=200, body=None, text=""):
        self.status_code = status_code
        self._body = body or {}
        self.text = text

    def json(self):
        return self._body


def _ok(message: dict, finish: str = "stop", usage: dict | None = None) -> _Resp:
    return _Resp(
        body={
            "choices": [{"message": message, "finish_reason": finish}],
            "usage": usage or {},
        }
    )


def test_reasoning_model_sends_low_effort_and_parses_content(monkeypatch):
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(json)
        return _ok({"content": '{"testCases": [{"title": "x"}]}'})

    monkeypatch.setattr(client_module.httpx, "post", fake_post)
    client = LlmClient("key", "https://example.invalid/v1", "glm-5.3-flash")
    assert client.chat_json([{"role": "user", "content": "x"}]) == {
        "testCases": [{"title": "x"}]
    }
    assert calls[0]["reasoning_effort"] == "low"


def test_non_reasoning_model_does_not_get_reasoning_effort(monkeypatch):
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(json)
        return _ok({"content": '{"ok": true}'})

    monkeypatch.setattr(client_module.httpx, "post", fake_post)
    client = LlmClient("key", "https://api.deepseek.com/v1", "deepseek-flash")
    assert client.chat_json([{"role": "user", "content": "x"}]) == {"ok": True}
    assert "reasoning_effort" not in calls[0]


def test_reasoning_content_json_is_accepted_when_content_empty(monkeypatch):
    monkeypatch.setattr(
        client_module.httpx,
        "post",
        lambda *a, **k: _ok(
            {"content": "", "reasoning_content": '{"testCases": [{"title": "from-reasoning"}]}'}
        ),
    )
    client = LlmClient("key", "https://example.invalid/v1", "reasoning-model")
    assert client.chat_json([{"role": "user", "content": "x"}]) == {
        "testCases": [{"title": "from-reasoning"}]
    }


def test_content_parts_are_joined(monkeypatch):
    monkeypatch.setattr(
        client_module.httpx,
        "post",
        lambda *a, **k: _ok(
            {
                "content": [
                    {"type": "text", "text": '{"a"'},
                    {"type": "text", "text": ": 1}"},
                ]
            }
        ),
    )
    client = LlmClient("key", "https://example.invalid/v1", "model")
    assert client.chat_json([{"role": "user", "content": "x"}]) == {"a": 1}


def test_truncated_reasoning_retries_with_larger_budget(monkeypatch):
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(json)
        if len(calls) == 1:
            return _ok(
                {"content": "", "reasoning_content": "still thinking"},
                finish="length",
                usage={
                    "completion_tokens": 8192,
                    "completion_tokens_details": {"reasoning_tokens": 8189},
                },
            )
        return _ok({"content": '{"testCases": []}'})

    monkeypatch.setattr(client_module.httpx, "post", fake_post)
    client = LlmClient("key", "https://example.invalid/v1", "reasoning-model")
    assert client.chat_json([{"role": "user", "content": "x"}], max_tokens=8192) == {
        "testCases": []
    }
    assert len(calls) == 2
    assert calls[1]["max_tokens"] == 16384


def test_unsupported_extension_params_are_retried_without_them(monkeypatch):
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(json)
        if "reasoning_effort" in json:
            return _Resp(status_code=400, text="invalid parameter: reasoning_effort")
        return _ok({"content": '{"testCases": []}'})

    monkeypatch.setattr(client_module.httpx, "post", fake_post)
    client = LlmClient("key", "https://example.invalid/v1", "glm-5.3-flash")
    assert client.chat_json([{"role": "user", "content": "x"}]) == {"testCases": []}
    assert calls[0]["reasoning_effort"] == "low"
    assert "reasoning_effort" not in calls[-1]


def test_reasoning_text_that_is_not_json_still_fails(monkeypatch):
    monkeypatch.setattr(
        client_module.httpx,
        "post",
        lambda *a, **k: _ok({"content": "", "reasoning_content": "思考过程，不是最终答案"}),
    )
    client = LlmClient("key", "https://example.invalid/v1", "reasoning-model")
    with pytest.raises(LlmUnavailable):
        client.chat_json([{"role": "user", "content": "x"}])
    # 连通性探测仍允许 reasoning 作为“有响应”的证据
    assert client.chat_text([{"role": "user", "content": "ping"}]) == "思考过程，不是最终答案"


def test_json_parser_prefers_business_wrapper_inside_reasoning(monkeypatch):
    reasoning = (
        "先看一个示例：{\"note\":\"example\"}\n"
        "最终答案：{\"testCases\":[{\"title\":\"final\"}]}"
    )
    monkeypatch.setattr(
        client_module.httpx,
        "post",
        lambda *a, **k: _ok({"content": "", "reasoning_content": reasoning}),
    )
    client = LlmClient("key", "https://example.invalid/v1", "reasoning-model")
    assert client.chat_json([{"role": "user", "content": "x"}]) == {
        "testCases": [{"title": "final"}]
    }
