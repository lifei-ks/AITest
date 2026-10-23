"""多 Agent 工作流 API：启动、查询、阶段详情、运行报告。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.common.response import ok
from app.database import SessionLocal
from app.service import report_service, workflow_service
from app.workflow.schemas import WorkflowCreate

router = APIRouter(tags=["workflows"])


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


@router.post("/api/projects/{pid}/workflows")
def create_workflow(pid: str, payload: WorkflowCreate, session: Session = Depends(get_session)):
    """创建并异步启动一次完整工作流，立即返回运行记录（供前端轮询）。"""
    return ok(workflow_service.create_run(session, pid, payload))


@router.get("/api/projects/{pid}/workflows")
def list_workflows(pid: str, limit: int = Query(20, ge=1, le=100), session: Session = Depends(get_session)):
    return ok(workflow_service.list_runs(session, pid, limit))


@router.get("/api/workflows/{run_id}")
def get_workflow(run_id: str, session: Session = Depends(get_session)):
    return ok(workflow_service.get_run_detail(session, run_id))


@router.get("/api/workflows/{run_id}/report")
def workflow_report(run_id: str, session: Session = Depends(get_session)):
    run = workflow_service.get_run(session, run_id)
    html = report_service.build_report(session, run.project_id, workflow_run_id=run.id)
    return Response(content=html.encode("utf-8"), media_type="text/html")
