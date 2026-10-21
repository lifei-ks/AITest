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
    # 必需字段必须在（文本模型 + 视觉模型两组配置）；不锁死全集，避免新增配置项就挂
    required = {
        "llmApiKey", "llmApiKeyConfigured", "llmBaseUrl", "llmModel",
        "llmVisionApiKey", "llmVisionBaseUrl", "llmVisionModel",
        "visionConfigured", "visionUsesText",
    }
    assert required <= set(info.keys())
    assert info["llmApiKey"] == "" or "***" in info["llmApiKey"]
    assert isinstance(info["llmApiKeyConfigured"], bool)
    # 视觉 Key 同样不能明文回显
    assert info["llmVisionApiKey"] == "" or "***" in info["llmVisionApiKey"]
