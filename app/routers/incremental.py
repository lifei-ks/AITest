"""增量需求分析：基线快照、变更识别、增量生成与回归推荐。"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.common.response import ok
from app.database import SessionLocal
from app.service import incremental_service

router = APIRouter(tags=["incremental"])


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


class SnapshotPayload(BaseModel):
    analysisId: str | None = None


class IncrementalAnalyzePayload(BaseModel):
    baselineRequirementId: str
    mode: Literal["auto", "llm", "rule"] = "auto"
    profileId: str | None = None


class IncrementalGeneratePayload(BaseModel):
    mode: Literal["auto", "llm", "rule"] = "auto"
    profileId: str | None = None


class ReviewPayload(BaseModel):
    status: Literal["confirmed", "rejected"]


class RegressionReviewPayload(BaseModel):
    status: Literal["accepted", "rejected"]


@router.post("/api/requirements/{rid}/snapshot")
def create_snapshot(
    rid: str,
    payload: SnapshotPayload | None = None,
    session: Session = Depends(get_session),
):
    data = payload or SnapshotPayload()
    return ok(
        incremental_service.build_snapshot(
            session,
            rid,
            analysis_id=data.analysisId,
        )
    )


@router.get("/api/requirements/{rid}/snapshot")
def get_snapshot(rid: str, session: Session = Depends(get_session)):
    row = incremental_service.get_latest_snapshot(session, rid)
    return ok(incremental_service.snapshot_to_dict(row) if row else None)


@router.post("/api/requirements/{rid}/incremental/analyze")
def analyze(
    rid: str,
    payload: IncrementalAnalyzePayload,
    session: Session = Depends(get_session),
):
    return ok(
        incremental_service.analyze_incremental(
            session,
            rid,
            payload.baselineRequirementId,
            mode=payload.mode,
            profile_id=payload.profileId,
        )
    )


@router.get("/api/requirements/{rid}/incremental/runs")
def list_runs(
    rid: str,
    limit: int = Query(20, ge=1, le=100),
    session: Session = Depends(get_session),
):
    return ok(incremental_service.list_runs(session, rid, limit))


@router.get("/api/requirements/{rid}/incremental/latest")
def latest_run(rid: str, session: Session = Depends(get_session)):
    runs = incremental_service.list_runs(session, rid, 1)
    return ok(runs[0] if runs else None)


@router.get("/api/incremental/runs/{run_id}")
def get_run(run_id: str, session: Session = Depends(get_session)):
    return ok(incremental_service.get_run(session, run_id))


@router.post("/api/incremental/runs/{run_id}/generate")
def generate(
    run_id: str,
    payload: IncrementalGeneratePayload | None = None,
    session: Session = Depends(get_session),
):
    data = payload or IncrementalGeneratePayload()
    return ok(
        incremental_service.generate_incremental(
            session,
            run_id,
            mode=data.mode,
            profile_id=data.profileId,
        )
    )


@router.post("/api/incremental/runs/{run_id}/changes/{change_id}/review")
def review_change(
    run_id: str,
    change_id: str,
    payload: ReviewPayload,
    session: Session = Depends(get_session),
):
    return ok(
        incremental_service.review_change(
            session, run_id, change_id, payload.status
        )
    )


@router.post("/api/incremental/case-changes/{case_change_id}/regression")
def review_regression(
    case_change_id: str,
    payload: RegressionReviewPayload,
    session: Session = Depends(get_session),
):
    return ok(
        incremental_service.review_regression(
            session, case_change_id, payload.status
        )
    )
