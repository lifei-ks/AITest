"""脑图 + 覆盖矩阵 + 统计路由。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.common.response import ok
from app.database import SessionLocal
from app.entity.models import ExecutionRecord, Project, TestCase
from app.service import coverage_service, mindmap_service, report_service

router = APIRouter(tags=["mindmap"])


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


@router.get("/api/projects/{pid}/mindmap/preview")
def preview(pid: str, requirementId: str | None = None, session: Session = Depends(get_session)):
    return ok(mindmap_service.build_tree(session, pid, requirementId))


@router.get("/api/projects/{pid}/mindmap/download")
def download(pid: str, format: str = "xmind", requirementId: str | None = None, session: Session = Depends(get_session)):
    tree = mindmap_service.build_tree(session, pid, requirementId)
    if format == "md":
        return Response(content=mindmap_service.export_markdown(tree).encode("utf-8"), media_type="text/markdown")
    data = mindmap_service.export_xmind(tree)
    return Response(content=data, media_type="application/zip", headers={"Content-Disposition": "attachment; filename=mindmap.xmind"})


@router.get("/api/projects/{pid}/coverage/matrix")
def matrix(pid: str, session: Session = Depends(get_session)):
    return ok(coverage_service.coverage_matrix(session, pid))


@router.get("/api/projects/{pid}/report")
def project_report(pid: str, requirementId: str | None = None, session: Session = Depends(get_session)):
    html = report_service.build_report(session, pid, requirement_id=requirementId)
    return Response(content=html.encode("utf-8"), media_type="text/html")


@router.get("/api/executions/{eid}/report")
def execution_report(eid: str, session: Session = Depends(get_session)):
    r = session.get(ExecutionRecord, eid)
    if r is None:
        from app.common.errors import BizError

        raise BizError(40404, "执行记录不存在", http_status=404)
    html = report_service.build_report(session, r.project_id)
    return Response(content=html.encode("utf-8"), media_type="text/html")


@router.get("/api/stats/overview")
def overview(session: Session = Depends(get_session)):
    project_count = session.scalar(select(func.count()).select_from(Project)) or 0
    case_count = session.scalar(select(func.count()).select_from(TestCase)) or 0
    exec_count = session.scalar(select(func.count()).select_from(ExecutionRecord)) or 0
    passed = session.scalar(select(func.count()).select_from(ExecutionRecord).where(ExecutionRecord.status == "passed")) or 0
    pass_rate = round(passed / exec_count, 4) if exec_count else 0
    return ok({"projects": project_count, "testCases": case_count, "executions": exec_count, "passRate": pass_rate})
