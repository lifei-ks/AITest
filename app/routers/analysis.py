"""代码分析路由：触发 / 读回 / 状态机列表。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.common.response import ok
from app.database import SessionLocal
from app.service import analysis_service

router = APIRouter(prefix="/api/projects", tags=["analysis"])


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


@router.post("/{pid}/analyze", status_code=202)
def trigger_analysis(pid: str):
    analysis_id = analysis_service.trigger_analysis(pid)
    return ok({"analysisId": analysis_id, "message": "已开始分析"})


@router.get("/{pid}/analysis")
def get_analysis(pid: str, session: Session = Depends(get_session)):
    return ok(analysis_service.get_analysis_result(session, pid))


@router.get("/{pid}/state-machines")
def list_state_machines(pid: str, session: Session = Depends(get_session)):
    return ok(analysis_service.list_state_machines(session, pid))
