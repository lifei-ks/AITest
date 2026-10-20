"""项目相关 DTO：请求体用 camelCase（TASK_BOOK 5.2），序列化统一转 camelCase。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer
from pydantic.alias_generators import to_camel

from app.common.timeutil import to_utc_iso


class CamelModel(BaseModel):
    """camelCase 序列化基类：前端契约统一 camelCase。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ProjectCreate(CamelModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    # local_path / git
    source_type: str = Field(default="local_path", pattern="^(local_path|git)$")
    source_path: str | None = None
    git_url: str | None = None
    git_branch: str | None = None
    target_url: str | None = None


class ProjectUpdate(CamelModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    source_type: str | None = Field(default=None, pattern="^(local_path|git)$")
    source_path: str | None = None
    git_url: str | None = None
    git_branch: str | None = None
    target_url: str | None = None
    prd_content: str | None = None


class PrdUpdate(CamelModel):
    content: str


class ProjectOut(CamelModel):
    id: str
    name: str
    description: str | None = None
    source_type: str
    source_path: str | None = None
    git_url: str | None = None
    git_branch: str | None = None
    tech_stack: dict[str, Any] | None = None
    prd_content: str | None = None
    target_url: str | None = None
    created_at: datetime
    updated_at: datetime
    # 派生字段：分析状态（V3 起有值）
    analysis_status: str | None = None

    @field_serializer("created_at", "updated_at")
    def _serialize_time(self, value: datetime) -> str:
        # 数据库存 UTC naive，输出必须带 Z，否则前端按本地时区解析会差 8 小时
        return to_utc_iso(value) or ""


def project_to_out(p, analysis_status: str | None = None) -> ProjectOut:
    """ORM → DTO；tech_stack 是 JSON 字符串，这里解包。"""
    import json

    tech = None
    if p.tech_stack:
        try:
            tech = json.loads(p.tech_stack)
        except (TypeError, ValueError):
            tech = None
    return ProjectOut(
        id=p.id,
        name=p.name,
        description=p.description,
        source_type=p.source_type,
        source_path=p.source_path,
        git_url=p.git_url,
        git_branch=p.git_branch,
        tech_stack=tech,
        prd_content=p.prd_content,
        target_url=p.target_url,
        created_at=p.created_at,
        updated_at=p.updated_at,
        analysis_status=analysis_status,
    )
