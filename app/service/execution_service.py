"""执行服务：单条（同步）、批量（异步串行）、批次查询。"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
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
from app.common.execution_enums import (
    error_category_meta,
    execution_status_meta,
    review_verdict_meta,
)
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
    verdict_meta = review_verdict_meta(review.verdict)
    normalized_issues = [_issue_to_dict(issue) for issue in (issues or [])]

    # 兼容历史记录：早期 AI 结论可能只有 suspect/unavailable + reason，
    # 没有落库 issue。这里补一个明确的“复核要点”，避免用户只看到状态不知道看什么。
    if (
        review.reviewer != "manual"
        and review.verdict in (VERDICT_SUSPECT, VERDICT_UNAVAILABLE)
        and not normalized_issues
    ):
        problem = (review.reason or "").strip() or (
            "AI 审核证据不足或不可用，请检查关键截图、执行步骤和录屏后人工判定"
        )
        normalized_issues = [{
            "imageIndex": 0,
            "stepIndex": None,
            "label": "AI 复核要点",
            "screenshot": None,
            "bbox": None,
            "problem": problem,
        }]

    if review.reviewer == "manual":
        recommended_action = review.reviewer_note or "人工复核已完成。"
    elif review.verdict == VERDICT_SUSPECT:
        recommended_action = (
            "逐条检查下方复核要点，打开对应截图和录屏确认；确认正常可选人工通过，确认异常选判为失败。"
        )
    elif review.verdict == VERDICT_UNAVAILABLE:
        recommended_action = (
            "先重新执行 AI 审核；若仍不可用，展开步骤记录并查看录屏后人工判定。"
        )
    else:
        recommended_action = None

    review_meta = {}
    if review.review_meta:
        try:
            review_meta = json.loads(review.review_meta)
        except (TypeError, ValueError):
            review_meta = {}

    return {
        "verdict": review.verdict,
        "verdictLabel": verdict_meta["label"],
        "verdictDescription": verdict_meta["description"],
        "verdictTag": verdict_meta["tag"],
        "reason": review.reason,
        "model": review.model,
        "imageCount": review.image_count,
        "reviewer": review.reviewer,
        "reviewerNote": review.reviewer_note,
        "reviewedAt": to_utc_iso(review.reviewed_at),
        "createdAt": to_utc_iso(review.created_at),
        "issues": normalized_issues,
        "issueCount": len(normalized_issues),
        "reviewPoints": [item["problem"] for item in normalized_issues if item.get("problem")],
        "recommendedAction": recommended_action,
        "imageReviews": [_image_review_to_dict(i) for i in (images or [])],
        "reviewMeta": review_meta,
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

    确定性执行结论优先：AI 可以确认通过或升级为人工复核，但不能把明确的
    failed/cancelled 改判为 passed。只有原始执行无确定失败时，AI ok 才能
    为 skipped（缺少文本锚点）提供补充证据。
    """
    del error_category
    if original_status in ("failed", "cancelled"):
        return original_status
    if original_status == "passed":
        return "passed" if verdict == VERDICT_OK else "needs_review"
    if original_status == "skipped":
        if verdict == VERDICT_OK:
            return "passed"
        if verdict in (VERDICT_SUSPECT, VERDICT_UNAVAILABLE):
            return "needs_review"
    return "needs_review"


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
    review.review_meta = json.dumps(
        {
            "reviewPasses": result.get("reviewPasses", 1),
            "totalSteps": result.get("totalSteps"),
            "reviewedSteps": result.get("reviewedSteps") or [],
            "imageCoverage": result.get("imageCoverage"),
        },
        ensure_ascii=False,
    )
    review.reviewer = "llm"
    review.reviewer_note = None
    review.reviewed_at = None
    session.flush()

    # 重审时覆盖旧的问题定位
    session.query(ExecutionReviewIssue).filter(
        ExecutionReviewIssue.execution_id == execution_id
    ).delete(synchronize_session=False)
    review_issues = list(result.get("issues") or [])
    if not review_issues and result.get("verdict") in (VERDICT_SUSPECT, VERDICT_UNAVAILABLE):
        review_issues = [{
            "label": "AI 复核要点",
            "problem": (result.get("reason") or "").strip()
            or "AI 审核证据不足或不可用，请检查关键截图、执行步骤和录屏后人工判定",
        }]
    for it in review_issues:
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


def ensure_target_reachable(target_url: str) -> None:
    """执行前探测被测地址；探测失败时不创建浏览器、截图、录屏或执行记录。"""
    url = (target_url or "").strip()
    if not url:
        from app.common.errors import BizError

        raise BizError(40029, "被测地址为空，无法执行", http_status=400)
    try:
        response = httpx.get(url, timeout=5, follow_redirects=True)
    except httpx.TimeoutException as exc:
        from app.common.errors import BizError

        raise BizError(
            40029,
            f"被测地址无法访问（连接超时）：{url}。请先启动被测服务",
            http_status=400,
        ) from exc
    except Exception as exc:  # noqa: BLE001 - 网络错误统一转业务提示
        from app.common.errors import BizError

        raise BizError(
            40029,
            f"被测地址无法访问：{url}。请检查服务是否已启动",
            http_status=400,
        ) from exc

    if response.status_code >= 400:
        from app.common.errors import BizError

        raise BizError(
            40029,
            f"被测地址返回 HTTP {response.status_code}，未开始执行用例：{url}",
            http_status=400,
        )


def _mark_execution_interrupted(eid: str, pid: str, cid: str, exc: Exception) -> None:
    """执行记录已创建但后段异常时，用新 Session 补终态，避免永久 running。"""
    session = SessionLocal()
    try:
        record = session.get(ExecutionRecord, eid)
        if record is None:
            return
        record.status = "failed"
        record.error_category = record.error_category or "ENV_ERROR"
        record.error_message = f"执行过程中异常中断：{exc}"[:2000]
        record.end_time = _now()
        tc = session.get(TestCase, cid)
        if tc is not None:
            tc.execution_status = "failed"
        session.commit()
    except Exception:  # noqa: BLE001
        logger.exception("补偿执行失败状态失败 execution=%s", eid)
        session.rollback()
    finally:
        session.close()


def _execution_failure_payload(eid: str, req: Requirement | None, exc: Exception) -> dict:
    status_meta = execution_status_meta("failed")
    category_meta = error_category_meta("ENV_ERROR")
    message = f"执行过程中异常中断：{exc}"[:2000]
    return {
        "executionId": eid,
        "status": "failed",
        "statusLabel": status_meta["label"],
        "statusDescription": status_meta["description"],
        "passedSteps": 0,
        "totalSteps": 0,
        "errorCategory": "ENV_ERROR",
        "errorCategoryLabel": category_meta["label"],
        "errorCategoryDescription": category_meta["description"],
        "errorMessage": message,
        "videoPath": None,
        "durationMs": 0,
        "llmReview": None,
        "requirementId": req.id if req else None,
        "requirementVersion": req.version if req else None,
        "requirementName": req.name if req else None,
    }


def execute_single(
    pid: str,
    cid: str,
    target_url: str | None = None,
    mode: str = "programmatic",
    batch_id: str | None = None,
) -> dict:
    """同步执行单条用例，落库执行记录 + 步骤，回写用例状态。"""
    session = SessionLocal()
    try:
        tc = session.get(TestCase, cid)
        if tc is None or tc.project_id != pid:
            from app.common.errors import BizError

            raise BizError(40402, f"用例不存在: {cid}", http_status=404)
        snapshot = case_to_dict(tc)
        if target_url:
            target = target_url
        else:
            proj = session.get(Project, pid)
            target = (proj.target_url if proj and proj.target_url else None) or "http://127.0.0.1:8200"

        # 必须在创建 running 记录前探测，避免目标不可用时产生假执行和无效截图。
        ensure_target_reachable(target)

        eid = _exec_id()
        record = ExecutionRecord(
            id=eid, project_id=pid, test_case_id=cid,
            test_case_title=tc.title, test_case_snapshot=json.dumps(snapshot, ensure_ascii=False),
            status="running", mode=mode, target_url=target, start_time=_now(),
            batch_id=batch_id,
        )
        session.add(record)
        tc.execution_status = "running"
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

        try:
            # 产物目录：outputs/{项目名}/{用例id}/（截图+录屏）
            proj = session.get(Project, pid)
            proj_folder = proj.name if proj else f"project_{pid[:8]}"
            out_dir = str(Path(get_settings().output_dir) / proj_folder)
            start = time.time()
            result = execute_case(snapshot, target, out_dir, mode)
            duration_ms = int((time.time() - start) * 1000)
        except Exception as exc:  # noqa: BLE001 - 已有 running 记录，必须补终态
            logger.exception("执行用例异常中断 execution=%s case=%s", eid, cid)
            _mark_execution_interrupted(eid, pid, cid, exc)
            return _execution_failure_payload(eid, req, exc)

        try:
            # AI 截图审核（未配置视觉模型时返回 None，不影响原结果）
            review_result = review_execution(snapshot, result["steps"], target)
        except Exception as exc:  # noqa: BLE001
            logger.exception("执行后截图审核异常 execution=%s", eid)
            _mark_execution_interrupted(eid, pid, cid, exc)
            return _execution_failure_payload(eid, req, exc)
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

        try:
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
                    evidence=json.dumps(s.get("evidence") or {}, ensure_ascii=False),
                ))

            # 回写用例执行状态
            tc.execution_status = (
                record.status
                if record.status in ("passed", "failed", "skipped", "needs_review")
                else tc.execution_status
            )
            session.commit()
        except Exception as exc:  # noqa: BLE001 - 写回失败也必须结束 running
            logger.exception("执行结果落库异常 execution=%s", eid)
            session.rollback()
            _mark_execution_interrupted(eid, pid, cid, exc)
            return _execution_failure_payload(eid, req, exc)

        status_meta = execution_status_meta(record.status)
        category_meta = error_category_meta(record.error_category)
        return {"executionId": eid, "status": record.status,
                "statusLabel": status_meta["label"],
                "statusDescription": status_meta["description"],
                "passedSteps": record.passed_steps,
                "totalSteps": record.total_steps, "errorCategory": record.error_category,
                "errorCategoryLabel": category_meta["label"],
                "errorCategoryDescription": category_meta["description"],
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
        status_meta = execution_status_meta(record.status)
        return {
            "executionId": eid,
            "status": record.status,
            "statusLabel": status_meta["label"],
            "statusDescription": status_meta["description"],
            "llmReview": review_with_issues(session, review),
        }
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
    out = []
    for s in rows:
        evidence = {}
        if s.evidence:
            try:
                evidence = json.loads(s.evidence)
            except (TypeError, ValueError):
                evidence = {}
        out.append({
            "stepIndex": s.step_index,
            "stepType": s.step_type,
            "action": s.action,
            "target": s.target,
            "result": s.result,
            "error": s.error,
            "evidence": evidence,
            "screenshotBefore": _abs_output(s.screenshot_before),
            "screenshotAfter": _abs_output(s.screenshot_after),
        })
    return out


def _original_status_from_steps(steps: list[dict], fallback: str) -> str:
    """从步骤恢复确定性执行结论，供 AI 重审时使用。"""
    results = [step.get("result") for step in steps]
    if any(result == "failed" for result in results):
        return "failed"
    if any(result == "skipped" for result in results):
        return "skipped"
    if results and all(result == "passed" for result in results):
        return "passed"
    return fallback if fallback in ("failed", "cancelled", "passed", "skipped") else "needs_review"


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
        existing_review = session.scalars(
            select(ExecutionReview).where(ExecutionReview.execution_id == eid)
        ).first()
        if existing_review is not None and existing_review.reviewer == "manual":
            raise BizError(40024, "人工复核结论已锁定，请重新执行人工复核")
        snapshot = json.loads(record.test_case_snapshot) if record.test_case_snapshot else {}
        steps = _steps_for_review(session, eid)
        result = review_execution(snapshot, steps, record.target_url or "")
        if result is None:
            raise BizError(40022, "未配置 LLM（model + key），无法进行 AI 审核")
        review = _upsert_review(session, eid, result)

        original = _original_status_from_steps(steps, record.status)
        record.status = _decide_status(original, record.error_category, result["verdict"])
        tc = session.get(TestCase, record.test_case_id)
        if tc is not None:
            tc.execution_status = record.status
        session.commit()
        status_meta = execution_status_meta(record.status)
        return {
            "executionId": eid,
            "status": record.status,
            "statusLabel": status_meta["label"],
            "statusDescription": status_meta["description"],
            "llmReview": review_with_issues(session, review),
        }
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
    if not case_ids:
        from app.common.errors import BizError

        raise BizError(40030, "请先选择要执行的用例")

    # 批量执行先统一探测一次，避免目标不可用时逐条抛错并生成空批次。
    probe_target = target_url
    if not probe_target:
        with SessionLocal() as session:
            project = session.get(Project, pid)
            probe_target = (
                project.target_url if project and project.target_url else "http://127.0.0.1:8200"
            )
    ensure_target_reachable(probe_target)

    batch_id = "B-" + uuid.uuid4().hex[:12]
    _cancel_flags[batch_id] = False

    def run():
        try:
            for cid in case_ids:
                if _cancel_flags.get(batch_id):
                    break
                try:
                    execute_single(pid, cid, target_url, mode, batch_id=batch_id)
                except Exception:  # noqa: BLE001
                    logger.exception("批量执行单条失败 %s", cid)
        finally:
            # 避免取消标记长期驻留内存；执行记录本身的 batch_id 已持久化。
            _cancel_flags.pop(batch_id, None)

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
        needs_review = sum(1 for r in records if r.status == "needs_review")
        cancelled = sum(1 for r in records if r.status == "cancelled")
        return {
            "batchId": batch_id, "total": total, "passed": passed, "failed": failed, "skipped": skipped,
            "needsReview": needs_review, "cancelled": cancelled,
            "running": running, "durationMs": sum(r.duration_ms for r in records),
            "items": [{"executionId": r.id, "testCaseId": r.test_case_id, "title": r.test_case_title,
                       "status": r.status,
                       "statusLabel": execution_status_meta(r.status)["label"],
                       "statusDescription": execution_status_meta(r.status)["description"],
                       "durationMs": r.duration_ms} for r in records],
        }
    finally:
        session.close()
