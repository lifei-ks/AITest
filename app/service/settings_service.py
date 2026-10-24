"""设置服务：多 LLM 配置（文本 / 多模态）+ 兼容旧 .env + 连通性测试。

设计：
- 新配置存 `llm_profiles`，同一 kind 可有多条，默认项由 `is_default` 标记。
- 首次读取时把 .env 的单套文本/多模态配置迁移成默认 profile；
  .env 仍作为“没有任何 DB profile”时的兜底。
- API 只回显掩码 Key；更新时传回掩码值表示保留原 Key。
- 测试点/用例生成可按 profileId 选择；未选择时用默认文本 profile。
"""
from __future__ import annotations

import logging
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.errors import BizError
from app.common.timeutil import to_utc_iso
from app.config import BASE_DIR, get_settings
from app.database import SessionLocal
from app.entity.models import LlmProfile
from app.llm.client import LlmClient, LlmUnavailable

logger = logging.getLogger(__name__)

ENV_KEYS = {"llm_api_key": "LLM_API_KEY", "llm_base_url": "LLM_BASE_URL", "llm_model": "LLM_MODEL"}
KINDS = ("text", "vision")


def _mask(key: str | None) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return key[:4] + "***"
    return key[:4] + "***" + key[-4:]


def _profile_to_dict(profile: LlmProfile) -> dict:
    return {
        "id": profile.id,
        "name": profile.name,
        "kind": profile.kind,
        "apiKey": _mask(profile.api_key),
        "apiKeyConfigured": bool(profile.api_key),
        "baseUrl": profile.base_url or "",
        "model": profile.model or "",
        "enabled": bool(profile.enabled),
        "isDefault": bool(profile.is_default),
        "createdAt": to_utc_iso(profile.created_at),
        "updatedAt": to_utc_iso(profile.updated_at),
    }


def _new_profile(
    *,
    name: str,
    kind: str,
    api_key: str | None,
    base_url: str | None,
    model: str | None,
    enabled: bool = True,
    is_default: bool = False,
) -> LlmProfile:
    return LlmProfile(
        name=name.strip()[:120],
        kind=kind,
        api_key=api_key or "",
        base_url=(base_url or "").strip(),
        model=(model or "").strip(),
        enabled=1 if enabled else 0,
        is_default=1 if is_default else 0,
    )


def ensure_seed_profiles(session: Session) -> list[LlmProfile]:
    """没有任何 DB profile 时，把 .env 单套配置迁移成默认 profile。"""
    rows = list(session.scalars(select(LlmProfile).order_by(LlmProfile.created_at)))
    kinds = {row.kind for row in rows}
    env = get_settings()
    new_rows: list[LlmProfile] = []
    if "text" not in kinds and (env.llm_api_key or env.llm_model):
        new_rows.append(
            _new_profile(
                name="默认文本 LLM",
                kind="text",
                api_key=env.llm_api_key,
                base_url=env.llm_base_url,
                model=env.llm_model,
                is_default=True,
            )
        )
    if "vision" not in kinds and (env.llm_vision_api_key or env.llm_vision_model):
        new_rows.append(
            _new_profile(
                name="默认多模态 LLM",
                kind="vision",
                api_key=env.llm_vision_api_key,
                base_url=env.llm_vision_base_url,
                model=env.llm_vision_model,
                is_default=True,
            )
        )
    if new_rows:
        session.add_all(new_rows)
        session.flush()
        rows.extend(new_rows)

    for kind in KINDS:
        kind_rows = [row for row in rows if row.kind == kind]
        if kind_rows and not any(row.is_default for row in kind_rows):
            kind_rows[0].is_default = 1
    return rows


def _default_profile(rows: Iterable[LlmProfile], kind: str) -> LlmProfile | None:
    candidates = [row for row in rows if row.kind == kind and row.enabled]
    if not candidates:
        return None
    for row in candidates:
        if row.is_default:
            return row
    return candidates[0]


def list_profiles(session: Session) -> list[dict]:
    rows = ensure_seed_profiles(session)
    order = {"text": 0, "vision": 1}
    rows = sorted(rows, key=lambda item: (order.get(item.kind, 9), -int(item.is_default), item.created_at))
    return [_profile_to_dict(row) for row in rows]


def get_info(session: Session | None = None) -> dict:
    """设置页信息；保留旧字段，同时返回多 profile 列表。"""
    env = get_settings()
    info = {
        "llmApiKey": _mask(env.llm_api_key),
        "llmApiKeyConfigured": bool(env.llm_api_key),
        "llmBaseUrl": env.llm_base_url,
        "llmModel": env.llm_model,
        "llmVisionApiKey": _mask(env.llm_vision_api_key),
        "llmVisionBaseUrl": env.llm_vision_base_url,
        "llmVisionModel": env.llm_vision_model,
        "visionConfigured": bool(
            (env.llm_vision_model or env.llm_model or "").strip()
            and (env.llm_vision_api_key or env.llm_api_key)
        ),
        "visionUsesText": not (env.llm_vision_model or "").strip(),
        "profiles": [],
        "defaultTextProfileId": None,
        "defaultVisionProfileId": None,
    }
    owns = session is None
    db_session = session or SessionLocal()
    try:
        rows = ensure_seed_profiles(db_session)
        if owns:
            db_session.commit()
        else:
            db_session.flush()
        text_default = _default_profile(rows, "text")
        vision_default = _default_profile(rows, "vision")
        info["profiles"] = list_profiles(db_session)
        info["defaultTextProfileId"] = text_default.id if text_default else None
        info["defaultVisionProfileId"] = vision_default.id if vision_default else None
        if text_default is not None:
            info["llmApiKey"] = _mask(text_default.api_key)
            info["llmApiKeyConfigured"] = bool(text_default.api_key)
            info["llmBaseUrl"] = text_default.base_url or ""
            info["llmModel"] = text_default.model or ""
        usable_vision = bool(
            vision_default
            and (vision_default.model or "").strip()
            and vision_default.api_key
        )
        usable_text_fallback = bool(
            text_default
            and (text_default.model or "").strip()
            and text_default.api_key
        )
        info["visionConfigured"] = usable_vision or usable_text_fallback
        info["visionUsesText"] = not usable_vision
    except Exception as exc:  # noqa: BLE001 - 表未创建/DB 暂不可用时保留 .env 兼容视图
        logger.warning("读取 LLM profiles 失败，回退 .env 视图: %s", exc)
        if owns:
            db_session.rollback()
    finally:
        if owns:
            db_session.close()
    return info


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


def update(payload: dict, session: Session | None = None) -> dict:
    """兼容旧 /api/settings 的 PUT：更新 .env 默认值，并同步默认 DB profile。"""
    env = get_settings()
    updates: dict[str, str] = {}
    text_values: dict[str, str] = {}
    vision_values: dict[str, str] = {}
    if "llmApiKey" in payload:
        key = payload["llmApiKey"] or ""
        if "***" in key:
            key = env.llm_api_key
        updates["LLM_API_KEY"] = key
        text_values["api_key"] = key
    if payload.get("llmBaseUrl") is not None:
        updates["LLM_BASE_URL"] = payload["llmBaseUrl"]
        text_values["base_url"] = payload["llmBaseUrl"]
    if payload.get("llmModel") is not None:
        updates["LLM_MODEL"] = payload["llmModel"]
        text_values["model"] = payload["llmModel"]
    if "llmVisionApiKey" in payload:
        vkey = payload["llmVisionApiKey"] or ""
        if "***" in vkey:
            vkey = env.llm_vision_api_key
        updates["LLM_VISION_API_KEY"] = vkey
        vision_values["api_key"] = vkey
    if payload.get("llmVisionBaseUrl") is not None:
        updates["LLM_VISION_BASE_URL"] = payload["llmVisionBaseUrl"]
        vision_values["base_url"] = payload["llmVisionBaseUrl"]
    if payload.get("llmVisionModel") is not None:
        updates["LLM_VISION_MODEL"] = payload["llmVisionModel"]
        vision_values["model"] = payload["llmVisionModel"]

    if updates:
        _update_env(updates)
        get_settings.cache_clear()

    owns = session is None
    db_session = session or SessionLocal()
    try:
        rows = ensure_seed_profiles(db_session)
        text_default = _default_profile(rows, "text")
        vision_default = _default_profile(rows, "vision")
        if text_default is not None:
            for key, value in text_values.items():
                setattr(text_default, key, value)
        if vision_default is not None:
            for key, value in vision_values.items():
                setattr(vision_default, key, value)
        if owns:
            db_session.commit()
        else:
            db_session.flush()
    except Exception as exc:  # noqa: BLE001
        logger.warning("同步 LLM profile 失败（.env 已更新）: %s", exc)
        if owns:
            db_session.rollback()
    finally:
        if owns:
            db_session.close()
    return get_info(session)


def _get_profile_or_404(session: Session, profile_id: str) -> LlmProfile:
    profile = session.get(LlmProfile, profile_id)
    if profile is None:
        raise BizError(40408, f"LLM 配置不存在: {profile_id}", http_status=404)
    return profile


def _default_for_kind(session: Session, kind: str, exclude: str | None = None) -> LlmProfile | None:
    rows = list(session.scalars(select(LlmProfile).where(LlmProfile.kind == kind)))
    candidates = [row for row in rows if row.id != exclude and row.enabled]
    if not candidates:
        return None
    for row in candidates:
        if row.is_default:
            return row
    return candidates[0]


def create_profile(payload: dict, session: Session) -> dict:
    kind = str(payload.get("kind") or "text").strip()
    if kind not in KINDS:
        raise BizError(40032, "kind 必须是 text 或 vision")
    name = str(payload.get("name") or "").strip()
    if not name:
        raise BizError(40032, "配置名称不能为空")
    model = str(payload.get("model") or "").strip()
    if not model:
        raise BizError(40032, "模型名称不能为空")
    is_default = bool(payload.get("isDefault"))
    if not is_default:
        existing = list(session.scalars(select(LlmProfile).where(LlmProfile.kind == kind)))
        is_default = not any(row.enabled and row.is_default for row in existing)
    if is_default:
        for row in session.scalars(select(LlmProfile).where(LlmProfile.kind == kind)):
            row.is_default = 0
    profile = _new_profile(
        name=name,
        kind=kind,
        api_key=payload.get("apiKey") or "",
        base_url=payload.get("baseUrl"),
        model=model,
        enabled=bool(payload.get("enabled", True)),
        is_default=is_default,
    )
    session.add(profile)
    session.flush()
    return _profile_to_dict(profile)


def update_profile(profile_id: str, payload: dict, session: Session) -> dict:
    profile = _get_profile_or_404(session, profile_id)
    if "name" in payload:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise BizError(40032, "配置名称不能为空")
        profile.name = name[:120]
    if "baseUrl" in payload:
        profile.base_url = str(payload.get("baseUrl") or "").strip()
    if "model" in payload:
        model = str(payload.get("model") or "").strip()
        if not model:
            raise BizError(40032, "模型名称不能为空")
        profile.model = model
    if "apiKey" in payload:
        key = str(payload.get("apiKey") or "")
        if "***" not in key:
            profile.api_key = key
    if "enabled" in payload:
        profile.enabled = 1 if payload.get("enabled") else 0
        if not profile.enabled and profile.is_default:
            fallback = _default_for_kind(session, profile.kind, exclude=profile.id)
            profile.is_default = 0
            if fallback is not None:
                fallback.is_default = 1
    if payload.get("isDefault"):
        for row in session.scalars(select(LlmProfile).where(LlmProfile.kind == profile.kind)):
            row.is_default = 0
        profile.is_default = 1
        profile.enabled = 1
    session.flush()
    return _profile_to_dict(profile)


def set_default_profile(profile_id: str, session: Session) -> dict:
    profile = _get_profile_or_404(session, profile_id)
    for row in session.scalars(select(LlmProfile).where(LlmProfile.kind == profile.kind)):
        row.is_default = 0
    profile.is_default = 1
    profile.enabled = 1
    session.flush()
    return _profile_to_dict(profile)


def delete_profile(profile_id: str, session: Session) -> None:
    profile = _get_profile_or_404(session, profile_id)
    rows = list(session.scalars(select(LlmProfile).where(LlmProfile.kind == profile.kind)))
    if len(rows) <= 1:
        raise BizError(40032, "至少保留一个该类型的 LLM 配置；可以停用但不要全部删除")
    was_default = bool(profile.is_default)
    session.delete(profile)
    session.flush()
    if was_default:
        fallback = _default_for_kind(session, profile.kind, exclude=profile.id)
        if fallback is not None:
            fallback.is_default = 1


def _profile_client(profile: LlmProfile) -> LlmClient:
    return LlmClient(profile.api_key or "", profile.base_url or "", profile.model or "")


def _env_text_client() -> LlmClient:
    env = get_settings()
    return LlmClient(env.llm_api_key, env.llm_base_url, env.llm_model)


def _env_vision_client() -> LlmClient:
    env = get_settings()
    return LlmClient(
        env.llm_vision_api_key or env.llm_api_key,
        env.llm_vision_base_url or env.llm_base_url,
        env.llm_vision_model or env.llm_model,
    )


def get_text_client(profile_id: str | None = None) -> LlmClient:
    """按 profileId 取文本客户端；未指定时用默认 profile，最后回退 .env。"""
    try:
        with SessionLocal() as session:
            rows = ensure_seed_profiles(session)
            session.commit()
            if profile_id:
                profile = session.get(LlmProfile, profile_id)
                if profile is None or profile.kind != "text":
                    raise BizError(40033, f"文本 LLM 配置不存在或类型不匹配: {profile_id}")
                if not profile.enabled:
                    raise BizError(40033, f"文本 LLM 配置已停用: {profile.name}")
                return _profile_client(profile)
            default = _default_profile(rows, "text")
            if default is not None:
                return _profile_client(default)
    except BizError:
        raise
    except Exception as exc:  # noqa: BLE001 - 表未建/DB 不可用时回退 .env
        logger.warning("读取文本 LLM profile 失败，回退 .env: %s", exc)
    return _env_text_client()


def get_vision_client() -> LlmClient:
    """视觉配置优先；没有可用视觉 profile 时回退默认文本 profile / .env。"""
    try:
        with SessionLocal() as session:
            rows = ensure_seed_profiles(session)
            session.commit()
            vision_default = _default_profile(rows, "vision")
            if vision_default is not None and (vision_default.model or "").strip() and vision_default.api_key:
                return _profile_client(vision_default)
            text_default = _default_profile(rows, "text")
            if text_default is not None:
                return _profile_client(text_default)
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取视觉 LLM profile 失败，回退 .env: %s", exc)
    return _env_vision_client()


def test_llm(profile_id: str | None = None, kind: str = "text") -> dict:
    """连通性测试。未配置返回 400（配置缺失不是服务故障）。"""
    if profile_id:
        with SessionLocal() as session:
            profile = _get_profile_or_404(session, profile_id)
            if profile.kind != kind:
                raise BizError(40033, "LLM 配置类型不匹配")
            client = _profile_client(profile)
            model = profile.model or ""
    else:
        client = get_vision_client() if kind == "vision" else get_text_client()
        model = client.model
    if not client.available():
        raise BizError(40030, "未配置 LLM API Key，请先填写")
    try:
        # 用纯文本 ping，不要求 JSON：推理模型会把 token 花在 reasoning_content 上，
        # 强求 JSON 会把“连接成功但没产出 JSON”误报成配置失败。
        text = client.chat_text(
            [{"role": "user", "content": "请只回复：ok"}],
            max_tokens=256,
        )
        if not (text or "").strip():
            raise LlmUnavailable("模型返回空内容")
        return {"ok": True, "model": model}
    except LlmUnavailable as e:
        raise BizError(40031, f"LLM 连通失败: {e}")
