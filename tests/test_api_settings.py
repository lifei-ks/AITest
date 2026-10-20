"""V8 设置接口测试：掩码逻辑 + 信息结构。"""
from __future__ import annotations

from app.service.settings_service import _mask, get_info


def test_mask_empty():
    assert _mask("") == ""


def test_mask_short():
    assert _mask("sk-abc") == "sk-a***"


def test_mask_long():
    assert _mask("sk-1234567890abcdef") == "sk-1***cdef"
    # 掩码不含完整 key
    assert "1234567890" not in _mask("sk-1234567890abcdef")


def test_get_info_structure():
    info = get_info()
    assert set(info.keys()) == {"llmApiKey", "llmApiKeyConfigured", "llmBaseUrl", "llmModel"}
    assert info["llmApiKey"] == "" or "***" in info["llmApiKey"]
    assert isinstance(info["llmApiKeyConfigured"], bool)
