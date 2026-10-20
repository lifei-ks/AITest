"""项目路由：参数校验 + 调 service，不写业务。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.orm import Session

from app.common.response import ok
from app.database import SessionLocal
from app.dto.projects import (
    ProjectCreate,
    ProjectOut,
    ProjectUpdate,
    PrdUpdate,
    project_to_out,
)
from app.service import project_service

router = APIRouter(prefix="/api/projects", tags=["projects"])


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


@router.get("")
def list_projects(
    keyword: str | None = Query(default=None),
    session: Session = Depends(get_session),
):
    projects = project_service.list_projects(session, keyword)
    data = [
        project_to_out(
            p, project_service.get_analysis_status(session, p.id)
        ).model_dump(by_alias=True)
        for p in projects
    ]
    return ok(data)


@router.post("", status_code=201)
def create_project(dto: ProjectCreate, session: Session = Depends(get_session)):
    proj = project_service.create_project(session, dto)
    return ok(project_to_out(proj).model_dump(by_alias=True))


@router.get("/{pid}")
def get_project(pid: str, session: Session = Depends(get_session)):
    proj = project_service.get_project(session, pid)
    return ok(
        project_to_out(
            proj, project_service.get_analysis_status(session, pid)
        ).model_dump(by_alias=True)
    )


@router.put("/{pid}")
def update_project(pid: str, dto: ProjectUpdate, session: Session = Depends(get_session)):
    proj = project_service.update_project(session, pid, dto)
    return ok(project_to_out(proj).model_dump(by_alias=True))


@router.delete("/{pid}")
def delete_project(pid: str, session: Session = Depends(get_session)):
    project_service.delete_project(session, pid)
    return ok(None)


@router.put("/{pid}/prd")
def set_prd(pid: str, dto: PrdUpdate, session: Session = Depends(get_session)):
    project_service.set_prd(session, pid, dto.content)
    return ok(None)


@router.post("/{pid}/prd/upload")
async def upload_prd(pid: str, file: UploadFile = File(...), session: Session = Depends(get_session)):
    raw = await file.read()
    if not raw:
        from app.common.errors import BizError

        raise BizError(40005, "上传文件为空")
    project_service.upload_prd(session, pid, file.filename or "", raw)
    return ok(None)


@router.get("/{pid}/prd")
def get_prd(pid: str, session: Session = Depends(get_session)):
    return ok({"content": project_service.get_prd(session, pid)})
