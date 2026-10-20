"""设置服务：LLM 供应商配置（读写 .env）+ 连通性测试。"""
from __future__ import annotations

import logging

from app.common.errors import BizError
from app.config import BASE_DIR, get_settings
from app.llm.client import LlmClient, LlmUnavailable

logger = logging.getLogger(__name__)

ENV_KEYS = {"llm_api_key": "LLM_API_KEY", "llm_base_url": "LLM_BASE_URL", "llm_model": "LLM_MODEL"}


def _mask(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return key[:4] + "***"
    return key[:4] + "***" + key[-4:]


def get_info() -> dict:
    s = get_settings()
    return {
        "llmApiKey": _mask(s.llm_api_key),
        "llmApiKeyConfigured": bool(s.llm_api_key),
        "llmBaseUrl": s.llm_base_url,
        "llmModel": s.llm_model,
        "llmVisionApiKey": _mask(s.llm_vision_api_key),
        "llmVisionBaseUrl": s.llm_vision_base_url,
        "llmVisionModel": s.llm_vision_model,
        # 多模态配置为空时回退文本 LLM，因此用“有效配置”判断可用性
        "visionConfigured": bool(
            (s.llm_vision_model or s.llm_model or "").strip()
            and (s.llm_vision_api_key or s.llm_api_key)
        ),
        "visionUsesText": not (s.llm_vision_model or "").strip(),
    }


def _update_env(updates: dict[str, str]) -> None:
    """更新 .env 里指定键（保留其他行与注释）。"""
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        env_path.write_text("", encoding="utf-8")
    text = env_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    out: list[str] = []
    updated: set[str] = set()
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else ""
        if key in updates:
            out.append(f"{key}={updates[key]}")
            updated.add(key)
        else:
            out.append(line)
    for k, v in updates.items():
        if k not in updated:
            out.append(f"{k}={v}")
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")


def update(payload: dict) -> dict:
    """更新 LLM 配置。apiKey 传空则清空；不传则保持原值（掩码值不会被当作新值）。"""
    s = get_settings()
    updates: dict[str, str] = {}
    if "llmApiKey" in payload:
        key = payload["llmApiKey"] or ""
        if "***" in key:
            key = s.llm_api_key  # 掩码值未改动，保持原值
        updates["LLM_API_KEY"] = key
    if payload.get("llmBaseUrl") is not None:
        updates["LLM_BASE_URL"] = payload["llmBaseUrl"]
    if payload.get("llmModel") is not None:
        updates["LLM_MODEL"] = payload["llmModel"]

    # 多模态 LLM：留空表示回退文本 LLM
    if "llmVisionApiKey" in payload:
        vkey = payload["llmVisionApiKey"] or ""
        if "***" in vkey:
            vkey = s.llm_vision_api_key
        updates["LLM_VISION_API_KEY"] = vkey
    if payload.get("llmVisionBaseUrl") is not None:
        updates["LLM_VISION_BASE_URL"] = payload["llmVisionBaseUrl"]
    if payload.get("llmVisionModel") is not None:
        updates["LLM_VISION_MODEL"] = payload["llmVisionModel"]

    if updates:
        _update_env(updates)
        get_settings.cache_clear()
    return get_info()


def test_llm() -> dict:
    """连通性测试。未配置返回 400（配置缺失不是服务故障）。"""
    s = get_settings()
    if not s.llm_api_key:
        raise BizError(40030, "未配置 LLM_API_KEY，请先填写")
    client = LlmClient(s.llm_api_key, s.llm_base_url, s.llm_model)
    try:
        client.chat_json([{"role": "user", "content": '只回复 JSON：{"ok": true}'}], max_tokens=64)
        return {"ok": True, "model": s.llm_model}
    except LlmUnavailable as e:
        raise BizError(40031, f"LLM 连通失败: {e}")
