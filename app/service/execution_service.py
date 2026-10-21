"""执行服务：单条（同步）、批量（异步串行）、批次查询。"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.execution_agent import execute_case
from app.agent.review_agent import (
    VERDICT_OK,
    VERDICT_SUSPECT,
    VERDICT_UNAVAILABLE,
    review_execution,
    vision_enabled,
)
from app.common.timeutil import to_utc_iso
from app.config import BASE_DIR, get_settings
from app.database import SessionLocal
from app.entity.models import (
    ExecutionRecord,
    ExecutionRequirement,
    ExecutionReview,
    ExecutionReviewImage,
    ExecutionReviewIssue,
    ExecutionStep,
    Project,
    Requirement,
    RequirementCase,
    TestCase,
)
from app.service.generation_service import case_to_dict

logger = logging.getLogger(__name__)

# batch_id -> cancel flag（内存标记）
_cancel_flags: dict[str, bool] = {}

# 这些失败属于环境/用例结构问题，即使 AI 说“看起来没问题”也必须人工确认
HARD_ERRORS = {"SELECTOR_NOT_FOUND", "CASE_INVALID", "ENV_ERROR", "TIMEOUT"}


def _abs_output(rel_path: str | None) -> str | None:
    if not rel_path:
        return None
    p = Path(rel_path)
    return str(p if p.is_absolute() else BASE_DIR / rel_path)


def _issue_to_dict(issue: ExecutionReviewIssue) -> dict:
    bbox = None
    if issue.bbox:
        try:
            bbox = json.loads(issue.bbox)
        except (TypeError, ValueError):
            bbox = None
    return {
        "imageIndex": issue.image_index,
        "stepIndex": issue.step_index,
        "label": issue.label,
        "screenshot": issue.screenshot,
        "bbox": bbox,
        "problem": issue.problem,
    }


def _image_review_to_dict(item: ExecutionReviewImage) -> dict:
    bbox = None
    if item.bbox:
        try:
            bbox = json.loads(item.bbox)
        except (TypeError, ValueError):
            bbox = None
    return {
        "imageIndex": item.image_index,
        "stepIndex": item.step_index,
        "label": item.label,
        "screenshot": item.screenshot,
        "verdict": item.verdict,
        "analysis": item.analysis,
        "bbox": bbox,
    }


def review_to_dict(
    review: ExecutionReview | None,
    issues: list | None = None,
    images: list | None = None,
) -> dict | None:
    if review is None:
        return None
    return {
        "verdict": review.verdict,
        "reason": review.reason,
        "model": review.model,
        "imageCount": review.image_count,
        "reviewer": review.reviewer,
        "reviewerNote": review.reviewer_note,
        "reviewedAt": to_utc_iso(review.reviewed_at),
        "createdAt": to_utc_iso(review.created_at),
        "issues": [_issue_to_dict(i) for i in (issues or [])],
        "imageReviews": [_image_review_to_dict(i) for i in (images or [])],
    }


def review_with_issues(session: Session, review: ExecutionReview | None) -> dict | None:
    if review is None:
        return None
    issues = list(
        session.scalars(
            select(ExecutionReviewIssue).where(ExecutionReviewIssue.review_id == review.id)
        )
    )
    images = list(
        session.scalars(
            select(ExecutionReviewImage)
            .where(ExecutionReviewImage.review_id == review.id)
            .order_by(ExecutionReviewImage.image_index)
        )
    )
    return review_to_dict(review, issues, images)


def _decide_status(original_status: str, error_category: str | None, verdict: str) -> str:
    """AI 结论 → 最终状态。

    - suspect / unavailable：一律转人工复核
    - ok + 硬错误（选择器/环境/用例非法）：仍要人工复核，避免把没跑起来判成通过
    - ok + 断言失败/跳过/原通过：直接通过
    """
    if verdict in (VERDICT_SUSPECT, VERDICT_UNAVAILABLE):
        return "needs_review"
    if verdict != VERDICT_OK:
        return "needs_review"
    if original_status == "passed":
        return "passed"
    if (error_category or "") in HARD_ERRORS:
        return "needs_review"
    return "passed"


def _requirement_for_case(session: Session, cid: str) -> Requirement | None:
    mapping = session.scalars(
        select(RequirementCase).where(RequirementCase.test_case_id == cid)
    ).first()
    if mapping is None:
        return None
    return session.get(Requirement, mapping.requirement_id)


def backfill_execution_requirements(session: Session, pid: str) -> None:
    """为历史执行记录补版本快照（幂等）。

    旧记录没有快照时按当前 用例↔版本 映射反查；用例已删除的则记为“未关联版本”。
    """
    existing = set(
        session.scalars(
            select(ExecutionRequirement.execution_id).where(ExecutionRequirement.project_id == pid)
        )
    )
    records = list(session.scalars(select(ExecutionRecord).where(ExecutionRecord.project_id == pid)))
    missing = [r for r in records if r.id not in existing]
    if not missing:
        return

    case_ids = {r.test_case_id for r in missing}
    mappings = list(
        session.scalars(select(RequirementCase).where(RequirementCase.test_case_id.in_(case_ids)))
    )
    requirement_by_case: dict[str, str] = {}
    for m in mappings:
        requirement_by_case.setdefault(m.test_case_id, m.requirement_id)
    req_ids = set(requirement_by_case.values())
    reqs = (
        {r.id: r for r in session.scalars(select(Requirement).where(Requirement.id.in_(req_ids)))}
        if req_ids
        else {}
    )

    for rec in missing:
        rid = requirement_by_case.get(rec.test_case_id)
        req = reqs.get(rid) if rid else None
        session.add(
            ExecutionRequirement(
                execution_id=rec.id,
                project_id=pid,
                requirement_id=req.id if req else None,
                requirement_version=req.version if req else None,
                requirement_name=req.name if req else None,
            )
        )
    session.flush()


def _upsert_review(session: Session, execution_id: str, result: dict) -> ExecutionReview:
    review = session.scalars(
        select(ExecutionReview).where(ExecutionReview.execution_id == execution_id)
    ).first()
    if review is None:
        review = ExecutionReview(execution_id=execution_id, verdict=result["verdict"])
        session.add(review)
    review.verdict = result["verdict"]
    review.reason = result.get("reason")
    review.model = result.get("model")
    # 已废弃：不再采集模型自报的置信度（无校准依据、不参与判定），重审时显式清空旧值
    review.confidence = None
    review.image_count = result.get("imageCount") or 0
    review.reviewer = "llm"
    review.reviewer_note = None
    review.reviewed_at = None
    session.flush()

    # 重审时覆盖旧的问题定位
    session.query(ExecutionReviewIssue).filter(
        ExecutionReviewIssue.execution_id == execution_id
    ).delete(synchronize_session=False)
    for it in result.get("issues") or []:
        session.add(
            ExecutionReviewIssue(
                review_id=review.id,
                execution_id=execution_id,
                image_index=it.get("imageIndex") or 0,
                step_index=it.get("stepIndex"),
                label=(it.get("label") or "")[:255],
                screenshot=_rel_path(it.get("screenshot")),
                bbox=json.dumps(it.get("bbox"), ensure_ascii=False) if it.get("bbox") else None,
                problem=(it.get("problem") or "")[:2000],
            )
        )

    # 重审时覆盖旧的逐图分析
    session.query(ExecutionReviewImage).filter(
        ExecutionReviewImage.execution_id == execution_id
    ).delete(synchronize_session=False)
    for it in result.get("imageReviews") or []:
        session.add(
            ExecutionReviewImage(
                review_id=review.id,
                execution_id=execution_id,
                image_index=it.get("imageIndex") or 0,
                step_index=it.get("stepIndex"),
                label=(it.get("label") or "")[:255],
                screenshot=_rel_path(it.get("screenshot")),
                verdict=it.get("verdict"),
                analysis=(it.get("analysis") or "")[:2000],
                bbox=json.dumps(it.get("bbox"), ensure_ascii=False) if it.get("bbox") else None,
            )
        )
    return review


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _exec_id() -> str:
    return "EX-" + uuid.uuid4().hex[:8]


def _rel_path(abs_path: str | None) -> str | None:
    if not abs_path:
        return None
    try:
        out_root = Path(get_settings().output_dir).resolve()
        p = Path(abs_path).resolve()
        return f"outputs/{p.relative_to(out_root).as_posix()}"
    except (ValueError, OSError):
        return str(abs_path).replace("\\", "/")


def execute_single(pid: str, cid: str, target_url: str | None = None, mode: str = "programmatic") -> dict:
    """同步执行单条用例，落库执行记录 + 步骤，回写用例状态。"""
    session = SessionLocal()
    try:
        tc = session.get(TestCase, cid)
        if tc is None:
            from app.common.errors import BizError

            raise BizError(40402, f"用例不存在: {cid}", http_status=404)
        snapshot = case_to_dict(tc)
        if target_url:
            target = target_url
        else:
            proj = session.get(Project, pid)
            target = (proj.target_url if proj and proj.target_url else None) or "http://127.0.0.1:8200"

        eid = _exec_id()
        record = ExecutionRecord(
            id=eid, project_id=pid, test_case_id=cid,
            test_case_title=tc.title, test_case_snapshot=json.dumps(snapshot, ensure_ascii=False),
            status="running", mode=mode, target_url=target, start_time=_now(),
        )
        session.add(record)
        # 执行时固化需求版本，便于执行结果按 V1/V2 区分
        req = _requirement_for_case(session, cid)
        session.add(
            ExecutionRequirement(
                execution_id=eid,
                project_id=pid,
                requirement_id=req.id if req else None,
                requirement_version=req.version if req else None,
                requirement_name=req.name if req else None,
            )
        )
        session.commit()

        # 产物目录：outputs/{项目名}/{用例id}/（截图+录屏）
        proj = session.get(Project, pid)
        proj_folder = proj.name if proj else f"project_{pid[:8]}"
        out_dir = str(Path(get_settings().output_dir) / proj_folder)
        start = time.time()
        result = execute_case(snapshot, target, out_dir, mode)
        duration_ms = int((time.time() - start) * 1000)

        # AI 截图审核（未配置视觉模型时返回 None，不影响原结果）
        review_result = review_execution(snapshot, result["steps"], target)
        final_status = result["status"]
        if review_result:
            final_status = _decide_status(
                result["status"], result["errorCategory"], review_result["verdict"]
            )

        # 步骤级错误回填到执行记录，便于详情页直接看到失败原因
        error_message = result["errorMessage"]
        if not error_message:
            for s in result["steps"]:
                if s.get("result") == "failed" and s.get("error"):
                    error_message = s["error"]
                    break

        record = session.get(ExecutionRecord, eid)
        record.status = final_status
        record.error_category = result["errorCategory"]
        record.error_message = error_message
        record.video_path = _rel_path(result["videoPath"])
        record.passed_steps = result["passedSteps"]
        record.total_steps = result["totalSteps"]
        record.end_time = _now()
        record.duration_ms = duration_ms

        review_row = None
        if review_result:
            review_row = _upsert_review(session, eid, review_result)

        for s in result["steps"]:
            session.add(ExecutionStep(
                execution_id=eid, step_index=s["stepIndex"], action=s["action"], target=s["target"],
                step_type=s["stepType"], selector_used=s["selectorUsed"], result=s["result"],
                screenshot_before=_rel_path(s["screenshotBefore"]), screenshot_after=_rel_path(s["screenshotAfter"]),
                error=s["error"], duration_ms=0,
            ))

        # 回写用例执行状态
        tc.execution_status = (
            record.status
            if record.status in ("passed", "failed", "skipped", "needs_review")
            else tc.execution_status
        )
        session.commit()

        return {"executionId": eid, "status": record.status, "passedSteps": record.passed_steps,
                "totalSteps": record.total_steps, "errorCategory": record.error_category,
                "errorMessage": record.error_message, "videoPath": record.video_path,
                "durationMs": duration_ms, "llmReview": review_with_issues(session, review_row),
                "requirementId": req.id if req else None,
                "requirementVersion": req.version if req else None,
                "requirementName": req.name if req else None}
    finally:
        session.close()


def manual_review(eid: str, decision: str, note: str | None = None) -> dict:
    """人工复核：pass / fail。会同步回写用例执行状态。"""
    from app.common.errors import BizError

    if decision not in ("pass", "fail"):
        raise BizError(40021, "decision 必须是 pass 或 fail")
    session = SessionLocal()
    try:
        record = session.get(ExecutionRecord, eid)
        if record is None:
            raise BizError(40404, f"执行记录不存在: {eid}", http_status=404)
        review = session.scalars(
            select(ExecutionReview).where(ExecutionReview.execution_id == eid)
        ).first()
        if review is None:
            review = ExecutionReview(execution_id=eid, verdict=VERDICT_UNAVAILABLE)
            session.add(review)
        review.reviewer = "manual"
        review.reviewer_note = (note or "").strip()[:2000] or None
        review.reviewed_at = _now()

        record.status = "passed" if decision == "pass" else "failed"
        tc = session.get(TestCase, record.test_case_id)
        if tc is not None:
            tc.execution_status = record.status
        session.commit()
        return {"executionId": eid, "status": record.status, "llmReview": review_with_issues(session, review)}
    finally:
        session.close()


def _steps_for_review(session: Session, eid: str) -> list[dict]:
    rows = list(
        session.scalars(
            select(ExecutionStep)
            .where(ExecutionStep.execution_id == eid)
            .order_by(ExecutionStep.step_index)
        )
    )
    return [
        {
            "stepIndex": s.step_index,
            "stepType": s.step_type,
            "action": s.action,
            "target": s.target,
            "result": s.result,
            "error": s.error,
            "screenshotBefore": _abs_output(s.screenshot_before),
            "screenshotAfter": _abs_output(s.screenshot_after),
        }
        for s in rows
    ]


def rerun_llm_review(eid: str) -> dict:
    """重新调用视觉模型审核（例如刚配置好视觉模型后）。"""
    from app.common.errors import BizError

    if not vision_enabled():
        raise BizError(40022, "未配置 LLM（model + key），无法进行 AI 审核")
    session = SessionLocal()
    try:
        record = session.get(ExecutionRecord, eid)
        if record is None:
            raise BizError(40404, f"执行记录不存在: {eid}", http_status=404)
        snapshot = json.loads(record.test_case_snapshot) if record.test_case_snapshot else {}
        steps = _steps_for_review(session, eid)
        result = review_execution(snapshot, steps, record.target_url or "")
        if result is None:
            raise BizError(40022, "未配置 LLM（model + key），无法进行 AI 审核")
        review = _upsert_review(session, eid, result)

        original = "passed" if record.passed_steps >= record.total_steps and not record.error_category else "failed"
        record.status = _decide_status(original, record.error_category, result["verdict"])
        tc = session.get(TestCase, record.test_case_id)
        if tc is not None:
            tc.execution_status = record.status
        session.commit()
        return {"executionId": eid, "status": record.status, "llmReview": review_with_issues(session, review)}
    finally:
        session.close()


def batch_llm_review(execution_ids: list[str]) -> dict:
    """批量 AI 审核（串行）。单条失败不影响其它记录。"""
    from app.common.errors import BizError

    ids = [e for e in (execution_ids or []) if e]
    if not ids:
        raise BizError(40023, "请先选择要审核的执行记录")
    if len(ids) > 20:
        raise BizError(40023, "单次最多审核 20 条，请分批选择")
    results = []
    for eid in dict.fromkeys(ids):
        try:
            r = rerun_llm_review(eid)
            review = r.get("llmReview") or {}
            results.append(
                {
                    "executionId": eid,
                    "status": r.get("status"),
                    "verdict": review.get("verdict"),
                }
            )
        except Exception as exc:  # noqa: BLE001 - 单条失败继续
            logger.warning("批量 AI 审核失败 %s: %s", eid, exc)
            results.append({"executionId": eid, "error": str(exc)})
    reviewed = sum(1 for r in results if "error" not in r)
    return {"total": len(results), "reviewed": reviewed, "results": results}


def execute_batch(pid: str, case_ids: list[str], target_url: str | None = None, mode: str = "programmatic") -> str:
    """异步批量执行（串行，上限 100）。返回 batch_id。"""
    if len(case_ids) > 100:
        from app.common.errors import BizError

        raise BizError(40020, "单批上限 100 条")
    batch_id = "B-" + uuid.uuid4().hex[:12]
    _cancel_flags[batch_id] = False

    def run():
        for cid in case_ids:
            if _cancel_flags.get(batch_id):
                break
            try:
                execute_single(pid, cid, target_url, mode)
            except Exception as e:  # noqa: BLE001
                logger.exception("批量执行单条失败 %s", cid)

    threading.Thread(target=run, daemon=True).start()
    return batch_id


def cancel_batch(batch_id: str) -> None:
    _cancel_flags[batch_id] = True


def get_batch(batch_id: str) -> dict:
    session = SessionLocal()
    try:
        records = list(session.scalars(select(ExecutionRecord).where(ExecutionRecord.batch_id == batch_id)))
        total = len(records)
        passed = sum(1 for r in records if r.status == "passed")
        failed = sum(1 for r in records if r.status == "failed")
        skipped = sum(1 for r in records if r.status == "skipped")
        running = any(r.status == "running" for r in records)
        return {
            "batchId": batch_id, "total": total, "passed": passed, "failed": failed, "skipped": skipped,
            "running": running, "durationMs": sum(r.duration_ms for r in records),
            "items": [{"executionId": r.id, "testCaseId": r.test_case_id, "title": r.test_case_title,
                       "status": r.status, "durationMs": r.duration_ms} for r in records],
        }
    finally:
        session.close()
