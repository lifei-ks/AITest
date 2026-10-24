"""设置路由：多 LLM 供应商配置（text/vision）+ 连通性测试。"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.common.response import ok
from app.database import SessionLocal
from app.service import settings_service

router = APIRouter(tags=["settings"])


def get_session() -> Session:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class SettingsPayload(BaseModel):
    llmApiKey: str | None = None
    llmBaseUrl: str | None = None
    llmModel: str | None = None
    llmVisionApiKey: str | None = None
    llmVisionBaseUrl: str | None = None
    llmVisionModel: str | None = None


class LlmProfilePayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["text", "vision"] = "text"
    apiKey: str | None = None
    baseUrl: str | None = None
    model: str = Field(min_length=1, max_length=120)
    enabled: bool = True
    isDefault: bool = False


class LlmProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    apiKey: str | None = None
    baseUrl: str | None = None
    model: str | None = Field(default=None, min_length=1, max_length=120)
    enabled: bool | None = None
    isDefault: bool | None = None


class TestProfileBody(BaseModel):
    profileId: str | None = None
    kind: Literal["text", "vision"] = "text"


@router.get("/api/settings")
def get_settings_info(session: Session = Depends(get_session)):
    return ok(settings_service.get_info(session))


@router.put("/api/settings")
def update_settings(payload: SettingsPayload, session: Session = Depends(get_session)):
    return ok(settings_service.update(payload.model_dump(exclude_unset=True), session))


@router.post("/api/settings/test-llm")
def test_llm(body: TestProfileBody | None = None):
    data = body or TestProfileBody()
    return ok(settings_service.test_llm(data.profileId, data.kind))


@router.get("/api/settings/llm-profiles")
def list_llm_profiles(session: Session = Depends(get_session)):
    return ok(settings_service.list_profiles(session))


@router.post("/api/settings/llm-profiles", status_code=201)
def create_llm_profile(payload: LlmProfilePayload, session: Session = Depends(get_session)):
    return ok(settings_service.create_profile(payload.model_dump(), session))


@router.put("/api/settings/llm-profiles/{profile_id}")
def update_llm_profile(
    profile_id: str, payload: LlmProfileUpdate, session: Session = Depends(get_session)
):
    return ok(
        settings_service.update_profile(
            profile_id, payload.model_dump(exclude_unset=True), session
        )
    )


@router.post("/api/settings/llm-profiles/{profile_id}/default")
def set_default_llm_profile(profile_id: str, session: Session = Depends(get_session)):
    return ok(settings_service.set_default_profile(profile_id, session))


@router.post("/api/settings/llm-profiles/{profile_id}/test")
def test_llm_profile(profile_id: str, session: Session = Depends(get_session)):
    profile = settings_service._get_profile_or_404(session, profile_id)
    return ok(settings_service.test_llm(profile_id, profile.kind))


@router.delete("/api/settings/llm-profiles/{profile_id}")
def delete_llm_profile(profile_id: str, session: Session = Depends(get_session)):
    settings_service.delete_profile(profile_id, session)
    return ok(None)
