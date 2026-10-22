"""8 张表：字段与类型严格按 TASK_BOOK 第 4 节。

约定：JSON 结构一律存 Text（json.dumps / json.loads 在 service 层做），
理由：SQLite 对 JSON 列支持有限，且历史快照类字段需要原样保存字符串。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return uuid.uuid4().hex


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # local_path / git
    source_type: Mapped[str] = mapped_column(String(20), nullable=False, default="local_path")
    source_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    git_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    git_branch: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # JSON: {"backend":"java-spring","frontend":"vue",...}
    tech_stack: Mapped[str | None] = mapped_column(Text, nullable=True)
    prd_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class CodeAnalysis(Base):
    __tablename__ = "code_analyses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    # pending / running / success / failed
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    backend_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    frontend_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    warnings: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class StateMachine(Base):
    __tablename__ = "state_machines"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # JSON: [{"name":"CREATED","label":"待支付"}]
    states: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON: [{...transition, evidence, verified}]
    transitions: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)


class TestCase(Base):
    __tablename__ = "test_cases"
    __table_args__ = (Index("ix_test_cases_project_seq", "project_id", "project_seq"),)

    # 全局唯一，格式 TC-{6位递增}，由 service 层分配
    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    project_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    module: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    # positive / negative / boundary / state
    type: Mapped[str] = mapped_column(String(20), nullable=False, default="positive")
    # P0~P2
    priority: Mapped[str] = mapped_column(String(10), nullable=False, default="P1")
    # 以下四个均为 JSON 数组字符串
    preconditions: Mapped[str | None] = mapped_column(Text, nullable=True)
    steps: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_results: Mapped[str | None] = mapped_column(Text, nullable=True)
    structured_steps: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON 对象
    test_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON: {"endpointIds":[],"transitionIds":[],"ruleIds":[]}
    coverage_refs: Mapped[str | None] = mapped_column(Text, nullable=True)
    # rule / ai / manual / import
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="rule")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    quality_score: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    lint_issues: Mapped[str | None] = mapped_column(Text, nullable=True)
    # not_run / running / passed / failed / skipped / needs_review / cancelled
    execution_status: Mapped[str] = mapped_column(String(20), nullable=False, default="not_run")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class TestCaseVersion(Base):
    __tablename__ = "test_case_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    test_case_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    change_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ExecutionRecord(Base):
    __tablename__ = "execution_records"

    # EX-{uuid8}
    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    test_case_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    # 冗余存标题：用例删除后历史仍可读
    test_case_title: Mapped[str] = mapped_column(String(255), nullable=False)
    # 执行瞬间的用例完整快照（强制，非可选）
    test_case_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    batch_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # running / passed / failed / skipped / needs_review / cancelled
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    # programmatic / agent
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default="programmatic")
    target_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_time: Mapped[datetime] = mapped_column(DateTime, default=_now)
    end_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_category: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    video_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    passed_steps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_steps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ExecutionRequirement(Base):
    """执行记录 ↔ 需求版本快照（1:1）。

    执行时固化版本信息：之后需求版本改名/删除、用例重新生成，历史执行仍可按版本区分。
    requirement_id 为空表示该执行对应的用例未关联任何需求版本。
    """

    __tablename__ = "execution_requirements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    execution_id: Mapped[str] = mapped_column(String(20), nullable=False, unique=True, index=True)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    requirement_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    requirement_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    requirement_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ExecutionReview(Base):
    """执行结果的 AI 审核记录（1:1 执行记录）。

    verdict：ok（截图符合预期）/ suspect（疑似异常）/ unavailable（审核不可用）
    reviewer：llm / manual（人工复核后写 manual）
    """

    __tablename__ = "execution_reviews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    execution_id: Mapped[str] = mapped_column(String(20), nullable=False, unique=True, index=True)
    verdict: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # 已废弃：模型自报置信度无校准依据、不参与判定，接口不再输出，新记录恒为 NULL。
    # 保留列仅为避免对已有 MySQL 表做破坏性迁移。
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    image_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reviewer: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reviewer_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ExecutionReviewIssue(Base):
    """AI 审核定位到的问题（可带截图区域坐标，前端画红框）。"""

    __tablename__ = "execution_review_issues"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    review_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    execution_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    image_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    step_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 相对 outputs/ 的截图路径，前端通过 /api/executions/{eid}/file 访问
    screenshot: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # JSON [x1,y1,x2,y2]，0~1 归一化坐标
    bbox: Mapped[str | None] = mapped_column(Text, nullable=True)
    problem: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ExecutionReviewImage(Base):
    """AI 对单张截图的逐图分析（详情报告用）。"""

    __tablename__ = "execution_review_images"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    review_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    execution_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    image_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    step_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    screenshot: Mapped[str | None] = mapped_column(String(500), nullable=True)
    verdict: Mapped[str | None] = mapped_column(String(20), nullable=True)
    analysis: Mapped[str | None] = mapped_column(Text, nullable=True)
    bbox: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ExecutionStep(Base):
    __tablename__ = "execution_steps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    execution_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    target: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    step_type: Mapped[str] = mapped_column(String(20), nullable=False, default="ui_action")
    selector_used: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # passed / failed / skipped
    result: Mapped[str] = mapped_column(String(20), nullable=False, default="passed")
    screenshot_before: Mapped[str | None] = mapped_column(String(500), nullable=True)
    screenshot_after: Mapped[str | None] = mapped_column(String(500), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Requirement(Base):
    """需求版本（一期需求文档 = 一个版本），用例按版本隔离。

    状态：draft（待解析）/ analyzing（解析中）/ ready（已解析出测试点）/ failed
    """

    __tablename__ = "requirements"
    __table_args__ = (Index("ix_requirements_project", "project_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    # 期次名称，如 "订单主流程"；version 如 "V1.0"
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False, default="V1.0")
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_file: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class TestPoint(Base):
    """需求测试点：需求文档 →（LLM/规则）→ 测试点 → 用例。"""

    __tablename__ = "test_points"
    __table_args__ = (Index("ix_test_points_requirement", "requirement_id", "seq"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    requirement_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    # functional / negative / boundary / state / api / permission
    category: Mapped[str] = mapped_column(String(20), nullable=False, default="functional")
    priority: Mapped[str] = mapped_column(String(10), nullable=False, default="P1")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 关联代码事实：页面路由 / 接口 / 字段 / 可观察预期
    page_route: Mapped[str | None] = mapped_column(String(255), nullable=True)
    endpoint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fields: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected: Mapped[str | None] = mapped_column(Text, nullable=True)
    # llm / rule
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="rule")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class RequirementCase(Base):
    """需求版本 ↔ 用例 映射（不改造已有 test_cases 表，避免线上加列）。"""

    __tablename__ = "requirement_cases"
    __table_args__ = (
        Index("ix_requirement_cases_req", "requirement_id"),
        Index("ix_requirement_cases_case", "test_case_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    requirement_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    test_case_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    test_point_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Mindmap(Base):
    __tablename__ = "mindmaps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    tree: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON: {"modules":5,"cases":12,"leaves":82}
    statistics: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
