"""执行路由：单条执行、批量执行、批次查询、证据文件访问。"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.errors import BizError
from app.common.response import ok
from app.common.timeutil import to_utc_iso
from app.config import get_settings
from app.database import SessionLocal
from app.entity.models import ExecutionRecord, ExecutionRequirement, ExecutionReview, ExecutionStep
from app.service import execution_service
from app.service.execution_service import review_with_issues

router = APIRouter(tags=["executions"])


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


class ExecuteBody(BaseModel):
    targetUrl: str | None = None
    mode: str = "programmatic"


class BatchExecuteBody(BaseModel):
    caseIds: list[str]
    targetUrl: str | None = None
    mode: str = "programmatic"


class ReviewBody(BaseModel):
    decision: str  # pass / fail
    note: str | None = None


class BatchReviewBody(BaseModel):
    executionIds: list[str]


@router.post("/api/projects/{pid}/testcases/{cid}/execute")
def execute_single(pid: str, cid: str, body: ExecuteBody):
    return ok(execution_service.execute_single(pid, cid, body.targetUrl, body.mode))


@router.post("/api/projects/{pid}/testcases/batch-execute")
def batch_execute(pid: str, body: BatchExecuteBody):
    batch_id = execution_service.execute_batch(pid, body.caseIds, body.targetUrl, body.mode)
    return ok({"batchId": batch_id})


@router.get("/api/projects/{pid}/executions")
def list_executions(pid: str, page: int = Query(1, ge=1), pageSize: int = Query(20, ge=1, le=200),
                    status: str | None = None, batchId: str | None = None,
                    requirementId: str | None = None,
                    session: Session = Depends(get_session)):
    from sqlalchemy import func

    # 历史记录没有版本快照时按当前映射补齐（幂等）
    execution_service.backfill_execution_requirements(session, pid)

    stmt = select(ExecutionRecord).where(ExecutionRecord.project_id == pid)
    if status:
        stmt = stmt.where(ExecutionRecord.status == status)
    if batchId:
        stmt = stmt.where(ExecutionRecord.batch_id == batchId)
    if requirementId:
        meta_stmt = select(ExecutionRequirement.execution_id).where(
            ExecutionRequirement.project_id == pid
        )
        if requirementId == "__none__":
            meta_stmt = meta_stmt.where(ExecutionRequirement.requirement_id.is_(None))
        else:
            meta_stmt = meta_stmt.where(ExecutionRequirement.requirement_id == requirementId)
        meta_ids = list(session.scalars(meta_stmt))
        stmt = stmt.where(ExecutionRecord.id.in_(meta_ids or ["__none__"]))
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    records = list(session.scalars(stmt.order_by(ExecutionRecord.start_time.desc()).offset((page - 1) * pageSize).limit(pageSize)))
    items = [{"id": r.id, "testCaseId": r.test_case_id, "testCaseTitle": r.test_case_title, "status": r.status,
              "mode": r.mode, "durationMs": r.duration_ms, "startTime": to_utc_iso(r.start_time),
              "batchId": r.batch_id, "errorCategory": r.error_category} for r in records]
    if items:
        eids = [i["id"] for i in items]
        reviews = session.scalars(
            select(ExecutionReview).where(ExecutionReview.execution_id.in_(eids))
        )
        review_map = {r.execution_id: r for r in reviews}
        metas = session.scalars(
            select(ExecutionRequirement).where(ExecutionRequirement.execution_id.in_(eids))
        )
        meta_map = {m.execution_id: m for m in metas}
        for item in items:
            item["llmReview"] = review_with_issues(session, review_map.get(item["id"]))
            meta = meta_map.get(item["id"])
            item["requirementId"] = meta.requirement_id if meta else None
            item["requirementVersion"] = meta.requirement_version if meta else None
            item["requirementName"] = meta.requirement_name if meta else None
    return ok({"total": total, "page": page, "pageSize": pageSize, "items": items})


@router.get("/api/executions/{eid}")
def get_execution(eid: str, session: Session = Depends(get_session)):
    r = session.get(ExecutionRecord, eid)
    if r is None:
        raise BizError(40404, "执行记录不存在", http_status=404)
    review = session.scalars(
        select(ExecutionReview).where(ExecutionReview.execution_id == eid)
    ).first()
    meta = session.scalars(
        select(ExecutionRequirement).where(ExecutionRequirement.execution_id == eid)
    ).first()
    return ok({"id": r.id, "testCaseId": r.test_case_id, "testCaseTitle": r.test_case_title, "status": r.status,
               "mode": r.mode, "targetUrl": r.target_url, "durationMs": r.duration_ms,
               "startTime": to_utc_iso(r.start_time),
               "endTime": to_utc_iso(r.end_time),
               "errorCategory": r.error_category, "errorMessage": r.error_message,
               "videoPath": r.video_path, "passedSteps": r.passed_steps, "totalSteps": r.total_steps,
               "requirementId": meta.requirement_id if meta else None,
               "requirementVersion": meta.requirement_version if meta else None,
               "requirementName": meta.requirement_name if meta else None,
               "llmReview": review_with_issues(session, review)})


@router.post("/api/executions/{eid}/review")
def manual_review(eid: str, body: ReviewBody):
    return ok(execution_service.manual_review(eid, body.decision, body.note))


@router.post("/api/executions/{eid}/llm-review")
def rerun_llm_review(eid: str):
    return ok(execution_service.rerun_llm_review(eid))


@router.post("/api/executions/batch-llm-review")
def batch_llm_review(body: BatchReviewBody):
    return ok(execution_service.batch_llm_review(body.executionIds))


@router.get("/api/executions/{eid}/steps")
def get_steps(eid: str, session: Session = Depends(get_session)):
    steps = list(session.scalars(select(ExecutionStep).where(ExecutionStep.execution_id == eid).order_by(ExecutionStep.step_index)))
    return ok([{"stepIndex": s.step_index, "stepType": s.step_type, "action": s.action, "target": s.target,
                "selectorUsed": s.selector_used, "result": s.result, "screenshotBefore": s.screenshot_before,
                "screenshotAfter": s.screenshot_after, "error": s.error} for s in steps])


@router.get("/api/executions/{eid}/video")
def get_video(eid: str, session: Session = Depends(get_session)):
    r = session.get(ExecutionRecord, eid)
    if r is None or not r.video_path:
        raise BizError(40405, "录屏不存在", http_status=404)
    return _safe_file(r.video_path)


@router.get("/api/executions/{eid}/file")
def get_file(eid: str, path: str = Query(...), session: Session = Depends(get_session)):
    _ = session.get(ExecutionRecord, eid)
    return _safe_file(path)


def _safe_file(rel_path: str) -> FileResponse:
    """证据文件访问，必须做路径穿越校验：realpath 必须在 outputs/ 下。

    兼容两种历史存储格式：
    - 绝对路径（旧数据）
    - 相对路径 "outputs/xxx"（新数据，相对项目根目录）
    """
    out_dir = Path(get_settings().output_dir).resolve()
    normalized = rel_path.replace("\\", "/")
    raw = Path(rel_path)
    if raw.is_absolute():
        full = raw.resolve()
    elif normalized.startswith("outputs/"):
        full = (out_dir.parent / rel_path).resolve()
    else:
        full = (out_dir / rel_path).resolve()
    try:
        full.relative_to(out_dir)
    except ValueError:
        raise BizError(40021, "非法路径", http_status=400) from None
    if not full.exists():
        raise BizError(40405, "文件不存在", http_status=404)
    return FileResponse(full)


@router.get("/api/batches/{bid}")
def get_batch(bid: str):
    return ok(execution_service.get_batch(bid))
