"""项目业务编排：路由层只做参数校验与调用，业务都在这里。"""
from __future__ import annotations

import json
import logging

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.common.errors import BizError
from app.config import get_settings
from app.entity.models import (
    CaseGeneration,
    CodeAnalysis,
    ExecutionRecord,
    ExecutionStep,
    Mindmap,
    Project,
    StateMachine,
    TestCase,
    TestCaseVersion,
)
from app.dto.projects import ProjectCreate, ProjectUpdate

logger = logging.getLogger(__name__)

PRD_ALLOWED_EXTS = {".md", ".txt", ".pdf", ".docx"}


def _get_or_404(session: Session, pid: str) -> Project:
    proj = session.get(Project, pid)
    if proj is None:
        raise BizError(40401, f"项目不存在: {pid}", http_status=404)
    return proj


def list_projects(session: Session, keyword: str | None = None) -> list[Project]:
    stmt = select(Project).order_by(Project.created_at.desc())
    if keyword:
        stmt = stmt.where(Project.name.like(f"%{keyword}%"))
    return list(session.scalars(stmt))


def create_project(session: Session, dto: ProjectCreate) -> Project:
    # 决策：source_type=local_path 时 source_path 必填；git 时 git_url 必填。理由：TASK_BOOK 2.1
    # 要求源码可用（本地路径 / Git 仓库），创建时保底校验，避免后续分析无从下手。
    if dto.source_type == "local_path" and not dto.source_path:
        raise BizError(40002, "sourceType 为 local_path 时必须填写 sourcePath")
    if dto.source_type == "git" and not dto.git_url:
        raise BizError(40003, "sourceType 为 git 时必须填写 gitUrl")

    proj = Project(
        name=dto.name,
        description=dto.description,
        source_type=dto.source_type,
        source_path=dto.source_path,
        git_url=dto.git_url,
        git_branch=dto.git_branch,
        target_url=dto.target_url or get_settings().target_url_default,
    )
    session.add(proj)
    session.flush()
    logger.info("创建项目 %s: %s", proj.id, proj.name)
    return proj


def get_project(session: Session, pid: str) -> Project:
    return _get_or_404(session, pid)


def get_analysis_status(session: Session, pid: str) -> str | None:
    stmt = (
        select(CodeAnalysis.status)
        .where(CodeAnalysis.project_id == pid)
        .order_by(CodeAnalysis.created_at.desc())
        .limit(1)
    )
    return session.scalars(stmt).first()


def update_project(session: Session, pid: str, dto: ProjectUpdate) -> Project:
    proj = _get_or_404(session, pid)
    data = dto.model_dump(exclude_unset=True, exclude_none=True)
    if "prd_content" in data:
        proj.prd_content = data.pop("prd_content")
    for key, value in data.items():
        setattr(proj, key, value)
    session.flush()
    return proj


def delete_project(session: Session, pid: str) -> None:
    """级联删除：分析 / 状态机 / 用例(+版本) / 执行记录(+步骤) / 脑图。"""
    _get_or_404(session, pid)
    case_ids = list(
        session.scalars(select(TestCase.id).where(TestCase.project_id == pid))
    )
    if case_ids:
        session.execute(
            delete(TestCaseVersion).where(TestCaseVersion.test_case_id.in_(case_ids))
        )
    session.execute(delete(TestCase).where(TestCase.project_id == pid))
    exec_ids = list(
        session.scalars(select(ExecutionRecord.id).where(ExecutionRecord.project_id == pid))
    )
    if exec_ids:
        session.execute(
            delete(ExecutionStep).where(ExecutionStep.execution_id.in_(exec_ids))
        )
    session.execute(delete(ExecutionRecord).where(ExecutionRecord.project_id == pid))
    session.execute(delete(CodeAnalysis).where(CodeAnalysis.project_id == pid))
    session.execute(delete(StateMachine).where(StateMachine.project_id == pid))
    session.execute(delete(Mindmap).where(Mindmap.project_id == pid))
    session.execute(delete(CaseGeneration).where(CaseGeneration.project_id == pid))
    session.execute(delete(Project).where(Project.id == pid))
    logger.info("删除项目 %s（级联清理完成）", pid)


def set_prd(session: Session, pid: str, content: str) -> Project:
    proj = _get_or_404(session, pid)
    proj.prd_content = content
    session.flush()
    return proj


def get_prd(session: Session, pid: str) -> str:
    return _get_or_404(session, pid).prd_content or ""


def parse_prd_text(filename: str, raw: bytes) -> str:
    """PRD 文件 → 纯文本。支持 .md/.txt/.pdf/.docx（项目 PRD 与需求版本共用）。"""
    import io
    import os

    ext = os.path.splitext(filename)[1].lower()
    if ext not in PRD_ALLOWED_EXTS:
        raise BizError(40004, f"不支持的文件类型 {ext}，仅支持 {'/'.join(sorted(PRD_ALLOWED_EXTS))}")

    if ext in {".md", ".txt"}:
        return raw.decode("utf-8", errors="replace")
    if ext == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(raw))
        return "\n".join(page.extract_text() or "" for page in reader.pages)

    import docx

    document = docx.Document(io.BytesIO(raw))
    return "\n".join(p.text for p in document.paragraphs)


def upload_prd(session: Session, pid: str, filename: str, raw: bytes) -> Project:
    """解析上传的 PRD 文件为纯文本并写入项目。"""
    _get_or_404(session, pid)
    return set_prd(session, pid, parse_prd_text(filename, raw))


def tech_stack_summary(proj: Project) -> dict | None:
    if not proj.tech_stack:
        return None
    try:
        return json.loads(proj.tech_stack)
    except (TypeError, ValueError):
        return None
