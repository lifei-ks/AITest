"""设置路由：LLM 供应商配置 + 连通性测试。"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.common.response import ok
from app.service import settings_service

router = APIRouter(tags=["settings"])


class SettingsPayload(BaseModel):
    llmApiKey: str | None = None
    llmBaseUrl: str | None = None
    llmModel: str | None = None


@router.get("/api/settings")
def get_settings_info():
    return ok(settings_service.get_info())


@router.put("/api/settings")
def update_settings(payload: SettingsPayload):
    return ok(settings_service.update(payload.model_dump(exclude_unset=True)))


@router.post("/api/settings/test-llm")
def test_llm():
    return ok(settings_service.test_llm())
