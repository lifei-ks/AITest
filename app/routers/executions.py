"""执行路由：单条执行、批量执行、批次查询、证据文件访问。"""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.common.errors import BizError
from app.common.execution_enums import (
    error_category_meta,
    execution_enum_payload,
    execution_status_meta,
)
from app.common.response import ok
from app.common.timeutil import to_utc_iso
from app.config import get_settings
from app.database import SessionLocal
from app.entity.models import ExecutionRecord, ExecutionRequirement, ExecutionReview, ExecutionStep
from app.service import execution_service
from app.service.execution_service import review_with_issues

router = APIRouter(tags=["executions"])


@router.get("/api/meta/execution")
def execution_metadata():
    """完整枚举值及中文含义，供前端、报告和外部集成统一使用。"""
    return ok(execution_enum_payload())


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
                    testCaseId: str | None = None,
                    groupBy: str = Query("case", pattern="^(case|execution)$"),
                    session: Session = Depends(get_session)):
    # 历史记录没有版本快照时按当前映射补齐（幂等）
    execution_service.backfill_execution_requirements(session, pid)

    stmt = select(ExecutionRecord).where(ExecutionRecord.project_id == pid)
    if status:
        stmt = stmt.where(ExecutionRecord.status == status)
    if batchId:
        stmt = stmt.where(ExecutionRecord.batch_id == batchId)
    if testCaseId:
        stmt = stmt.where(ExecutionRecord.test_case_id == testCaseId)
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

    if groupBy == "case":
        filtered = stmt.subquery()
        ranked = (
            select(
                filtered.c.id.label("id"),
                filtered.c.start_time.label("start_time"),
                func.row_number().over(
                    partition_by=filtered.c.test_case_id,
                    order_by=[
                        filtered.c.start_time.desc(),
                        filtered.c.id.desc(),
                    ],
                ).label("row_number"),
            )
            .subquery()
        )
        ranked_stmt = select(ranked.c.id).where(ranked.c.row_number == 1)
        # 先按 status/batch/requirement 过滤候选记录，再对每个用例取其中最新一条。
        total = session.scalar(
            select(func.count())
            .select_from(ranked)
            .where(ranked.c.row_number == 1)
        ) or 0
        latest_ids = list(session.scalars(
            ranked_stmt.order_by(ranked.c.start_time.desc())
            .offset((page - 1) * pageSize)
            .limit(pageSize)
        ))
        records = list(session.scalars(
            select(ExecutionRecord).where(ExecutionRecord.id.in_(latest_ids or ["__none__"]))
        ))
        records.sort(key=lambda item: item.start_time, reverse=True)
    else:
        total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        records = list(session.scalars(
            stmt.order_by(ExecutionRecord.start_time.desc())
            .offset((page - 1) * pageSize)
            .limit(pageSize)
        ))

    items = []
    for record in records:
        status_meta = execution_status_meta(record.status)
        category_meta = error_category_meta(record.error_category)
        items.append({
            "id": record.id,
            "executionId": record.id,
            "testCaseId": record.test_case_id,
            "testCaseTitle": record.test_case_title,
            "status": record.status,
            "statusLabel": status_meta["label"],
            "statusDescription": status_meta["description"],
            "statusTag": status_meta["tag"],
            "mode": record.mode,
            "durationMs": record.duration_ms,
            "passedSteps": record.passed_steps,
            "totalSteps": record.total_steps,
            "startTime": to_utc_iso(record.start_time),
            "batchId": record.batch_id,
            "errorCategory": record.error_category,
            "errorCategoryLabel": category_meta["label"],
            "errorCategoryDescription": category_meta["description"],
            "errorCategoryTag": category_meta["tag"],
        })
    if groupBy == "case" and items:
        case_counts = dict(session.execute(
            select(ExecutionRecord.test_case_id, func.count())
            .where(ExecutionRecord.project_id == pid)
            .group_by(ExecutionRecord.test_case_id)
        ).all())
        for item in items:
            item["executionCount"] = case_counts.get(item["testCaseId"], 1)
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
    return ok({
        "total": total,
        "page": page,
        "pageSize": pageSize,
        "groupedBy": groupBy,
        "items": items,
    })


@router.get("/api/projects/{pid}/testcases/{cid}/executions")
def list_case_executions(
    pid: str,
    cid: str,
    limit: int = Query(50, ge=1, le=200),
    session: Session = Depends(get_session),
):
    execution_service.backfill_execution_requirements(session, pid)
    records = list(session.scalars(
        select(ExecutionRecord)
        .where(ExecutionRecord.project_id == pid, ExecutionRecord.test_case_id == cid)
        .order_by(ExecutionRecord.start_time.desc(), ExecutionRecord.id.desc())
        .limit(limit)
    ))
    items = []
    for record in records:
        status_meta = execution_status_meta(record.status)
        category_meta = error_category_meta(record.error_category)
        review = session.scalars(
            select(ExecutionReview).where(ExecutionReview.execution_id == record.id)
        ).first()
        items.append({
            "id": record.id,
            "status": record.status,
            "statusLabel": status_meta["label"],
            "statusDescription": status_meta["description"],
            "statusTag": status_meta["tag"],
            "startTime": to_utc_iso(record.start_time),
            "durationMs": record.duration_ms,
            "passedSteps": record.passed_steps,
            "totalSteps": record.total_steps,
            "batchId": record.batch_id,
            "errorCategory": record.error_category,
            "errorCategoryLabel": category_meta["label"],
            "errorCategoryDescription": category_meta["description"],
            "errorMessage": record.error_message,
            "llmReview": review_with_issues(session, review),
        })
    return ok(items)


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
    status_meta = execution_status_meta(r.status)
    category_meta = error_category_meta(r.error_category)
    return ok({"id": r.id, "testCaseId": r.test_case_id, "testCaseTitle": r.test_case_title, "status": r.status,
               "statusLabel": status_meta["label"], "statusDescription": status_meta["description"],
               "statusTag": status_meta["tag"],
               "mode": r.mode, "targetUrl": r.target_url, "durationMs": r.duration_ms,
               "startTime": to_utc_iso(r.start_time),
               "endTime": to_utc_iso(r.end_time),
               "errorCategory": r.error_category,
               "errorCategoryLabel": category_meta["label"],
               "errorCategoryDescription": category_meta["description"],
               "errorCategoryTag": category_meta["tag"],
               "errorMessage": r.error_message,
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
                "screenshotAfter": s.screenshot_after, "error": s.error,
                "evidence": _step_evidence(s.evidence)} for s in steps])


def _step_evidence(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


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
