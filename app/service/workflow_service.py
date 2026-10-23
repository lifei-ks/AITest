"""多 Agent 测试工作流编排器。

闭环：
需求版本 → 测试点（Agent 1）→ 用例（Agent 2）→ 独立审核/修订（Agent 3）
→ 已通过且可自动化的用例执行（现有 Playwright 引擎）
→ 证据驱动结果分析（Agent 4）→ 汇总报告。

设计原则：
- 不替换现有接口/表；工作流只新增运行、阶段、用例审核、结果分析记录。
- 每个阶段都落库；阶段失败保留前序产物，后续阶段标记 skipped，不静默丢数据。
- 审核最多修订 ``maxRevisions`` 轮；超过上限仍不通过的用例转 reject，不执行。
- 结果分析只写自己的表，绝不回写 execution_records.status。
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent import case_review_agent, result_analysis_agent
from app.agent.quality_gate import (
    case_has_compound_expected,
    case_has_multiple_inputs,
    score_case,
)
from app.common.errors import BizError
from app.common.execution_enums import execution_status_meta
from app.common.timeutil import to_utc_iso
from app.config import get_settings
from app.database import SessionLocal
from app.entity.models import (
    CodeAnalysis,
    ExecutionRecord,
    ExecutionReview,
    ExecutionStep,
    Project,
    Requirement,
    RequirementCase,
    TestCase,
    TestPoint,
    WorkflowCase,
    WorkflowCaseReview,
    WorkflowResultAnalysis,
    WorkflowRun,
    WorkflowStage,
)
from app.llm.client import LlmClient
from app.service import (
    analysis_service,
    case_review_service,
    execution_service,
    generation_service,
    requirement_service,
)
from app.workflow.schemas import CaseReviewResult, ResultAnalysisResult, WorkflowCreate

logger = logging.getLogger(__name__)

STAGES = (
    "code_analysis",
    "test_points",
    "case_generation",
    "case_review",
    "execution",
    "result_analysis",
    "report",
)
STAGE_LABELS = {
    "code_analysis": "代码分析",
    "test_points": "测试点生成",
    "case_generation": "测试用例生成",
    "case_review": "用例审核与修订",
    "execution": "自动执行",
    "result_analysis": "结果分析",
    "report": "报告汇总",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _loads(raw: str | None, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _elapsed_ms(start: datetime | None) -> int:
    if start is None:
        return 0
    now = _now()
    try:
        return max(0, int((now - start).total_seconds() * 1000))
    except TypeError:
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        return max(0, int((now - start).total_seconds() * 1000))


def _text_client() -> LlmClient:
    settings = get_settings()
    return LlmClient(settings.llm_api_key, settings.llm_base_url, settings.llm_model)


# ---------- 序列化 ----------

def stage_to_dict(stage: WorkflowStage) -> dict:
    return {
        "id": stage.id,
        "stage": stage.stage,
        "label": STAGE_LABELS.get(stage.stage, stage.stage),
        "status": stage.status,
        "attempt": stage.attempt,
        "inputSummary": stage.input_summary,
        "outputSummary": stage.output_summary,
        "payload": _loads(stage.payload, {}),
        "errorMessage": stage.error_message,
        "model": stage.model,
        "startedAt": to_utc_iso(stage.started_at),
        "finishedAt": to_utc_iso(stage.finished_at),
        "durationMs": stage.duration_ms,
    }


def run_to_dict(run: WorkflowRun) -> dict:
    return {
        "id": run.id,
        "projectId": run.project_id,
        "requirementId": run.requirement_id,
        "requirementVersion": run.requirement_version,
        "requirementName": run.requirement_name,
        "status": run.status,
        "currentStage": run.current_stage,
        "currentStageLabel": STAGE_LABELS.get(run.current_stage or "", run.current_stage),
        "mode": run.mode,
        "config": _loads(run.config, {}),
        "summary": _loads(run.summary, {}),
        "errorMessage": run.error_message,
        "startedAt": to_utc_iso(run.started_at),
        "finishedAt": to_utc_iso(run.finished_at),
        "createdAt": to_utc_iso(run.created_at),
        "reportUrl": f"/api/workflows/{run.id}/report",
    }


def review_to_dict(row: WorkflowCaseReview) -> dict:
    return {
        "id": row.id,
        "round": row.round,
        "reviewStatus": row.review_status,
        "qualityScore": row.quality_score,
        "issues": _loads(row.issues, []),
        "missingCoverage": _loads(row.missing_coverage, []),
        "suggestions": _loads(row.suggestions, []),
        "deterministicIssues": _loads(row.deterministic_issues, []),
        "source": row.source,
        "model": row.model,
        "createdAt": to_utc_iso(row.created_at),
    }


def analysis_to_dict(row: WorkflowResultAnalysis) -> dict:
    return {
        "id": row.id,
        "executionId": row.execution_id,
        "caseId": row.case_id,
        "executionStatus": row.execution_status,
        "failureCategory": row.failure_category,
        "failureReason": row.failure_reason,
        "evidence": _loads(row.evidence, []),
        "confidence": row.confidence,
        "recommendedAction": row.recommended_action,
        "needsHumanReview": bool(row.needs_human_review),
        "source": row.source,
        "model": row.model,
        "createdAt": to_utc_iso(row.created_at),
    }


# ---------- 启动 ----------

def _ensure_stage_rows(session: Session, run: WorkflowRun) -> None:
    existing = set(
        session.scalars(select(WorkflowStage.stage).where(WorkflowStage.run_id == run.id))
    )
    for stage in STAGES:
        if stage not in existing:
            session.add(
                WorkflowStage(
                    id=uuid.uuid4().hex,
                    run_id=run.id,
                    stage=stage,
                    status="pending",
                    attempt=1,
                )
            )
    session.flush()


def create_run(session: Session, pid: str, payload: WorkflowCreate, run_async: bool = True) -> dict:
    """创建并启动工作流。run_async=False 仅供测试/CLI 同步运行。"""
    project = session.get(Project, pid)
    if project is None:
        raise BizError(40401, f"项目不存在: {pid}", http_status=404)
    req = session.get(Requirement, payload.requirementId)
    if req is None or req.project_id != pid:
        raise BizError(40406, f"需求版本不存在: {payload.requirementId}", http_status=404)
    if not (req.content or "").strip():
        raise BizError(40014, "需求内容为空，请先填写或上传需求文档")

    run = WorkflowRun(
        id=uuid.uuid4().hex,
        project_id=pid,
        requirement_id=req.id,
        requirement_version=req.version,
        requirement_name=req.name,
        status="pending",
        current_stage="test_points",
        mode=payload.mode,
        config=_dumps(payload.model_dump()),
    )
    session.add(run)
    session.flush()
    _ensure_stage_rows(session, run)
    session.commit()

    if run_async:
        threading.Thread(target=_run_workflow, args=(run.id,), daemon=True).start()
    else:
        _run_workflow(run.id)
    session.expire_all()
    refreshed = session.get(WorkflowRun, run.id)
    return run_to_dict(refreshed or run)


def _begin_stage(session: Session, run: WorkflowRun, stage_name: str, input_summary: str) -> WorkflowStage:
    stage = session.scalars(
        select(WorkflowStage).where(
            WorkflowStage.run_id == run.id, WorkflowStage.stage == stage_name
        )
    ).first()
    if stage is None:
        stage = WorkflowStage(
            id=uuid.uuid4().hex, run_id=run.id, stage=stage_name, attempt=1, status="pending"
        )
        session.add(stage)
    if stage.status == "running":
        stage.attempt += 1
    stage.status = "running"
    stage.input_summary = input_summary[:2000]
    stage.output_summary = None
    stage.payload = None
    stage.error_message = None
    stage.model = None
    stage.started_at = _now()
    stage.finished_at = None
    stage.duration_ms = 0
    run.current_stage = stage_name
    session.commit()
    return stage


def _end_stage(
    session: Session,
    stage: WorkflowStage,
    status: str,
    output_summary: str,
    payload: dict | None = None,
    error: str | None = None,
    model: str | None = None,
) -> WorkflowStage:
    stage.status = status
    stage.output_summary = (output_summary or "")[:2000]
    stage.payload = _dumps(payload) if payload is not None else None
    stage.error_message = (error or "")[:4000] or None
    stage.model = (model or "")[:100] or None
    stage.finished_at = _now()
    stage.duration_ms = _elapsed_ms(stage.started_at)
    session.commit()
    return stage


def _mark_remaining_skipped(session: Session, run: WorkflowRun, reason: str) -> None:
    stages = list(
        session.scalars(
            select(WorkflowStage)
            .where(WorkflowStage.run_id == run.id, WorkflowStage.status.in_(["pending", "running"]))
            .order_by(WorkflowStage.created_at)
        )
    )
    for stage in stages:
        stage.status = "skipped"
        stage.output_summary = reason[:2000]
        stage.finished_at = _now()
        stage.duration_ms = _elapsed_ms(stage.started_at) if stage.started_at else 0
    session.commit()


def _persist_stage_failure(run_id: str, stage_name: str | None, error: str) -> None:
    """用新连接记录阶段失败，避免原 Session 因连接丢失/PendingRollback 无法落库。"""
    session = SessionLocal()
    try:
        run = session.get(WorkflowRun, run_id)
        if run is None:
            return
        stmt = select(WorkflowStage).where(WorkflowStage.run_id == run_id)
        if stage_name:
            stmt = stmt.where(WorkflowStage.stage == stage_name)
        else:
            stmt = stmt.where(WorkflowStage.status == "running")
        stage = session.scalars(stmt).first()
        if stage is not None and stage.status == "running":
            stage.status = "failed"
            stage.error_message = error[:4000]
            stage.finished_at = _now()
            stage.duration_ms = _elapsed_ms(stage.started_at)
        run.error_message = (error or "工作流执行失败")[:4000]
        session.commit()
    except Exception:  # noqa: BLE001 - 失败状态落库失败只能记录日志
        logger.exception("无法持久化工作流失败状态 run=%s stage=%s", run_id, stage_name)
        session.rollback()
    finally:
        session.close()


def _persist_run_failure(run_id: str, error: str) -> None:
    session = SessionLocal()
    try:
        run = session.get(WorkflowRun, run_id)
        if run is None:
            return
        run.status = "failed"
        run.current_stage = None
        run.error_message = (error or "工作流执行失败")[:4000]
        run.finished_at = _now()
        stages = list(
            session.scalars(
                select(WorkflowStage).where(
                    WorkflowStage.run_id == run_id,
                    WorkflowStage.status == "running",
                )
            )
        )
        for stage in stages:
            stage.status = "failed"
            stage.error_message = (error or "工作流执行失败")[:4000]
            stage.finished_at = _now()
            stage.duration_ms = _elapsed_ms(stage.started_at)
        session.commit()
    except Exception:  # noqa: BLE001
        logger.exception("无法持久化工作流终态 run=%s", run_id)
        session.rollback()
    finally:
        session.close()


def _run_workflow(run_id: str) -> None:
    session = SessionLocal()
    try:
        run = session.get(WorkflowRun, run_id)
        if run is None:
            return
        config = _loads(run.config, {})
        run.status = "running"
        run.started_at = _now()
        run.error_message = None
        session.commit()

        handlers = (
            ("code_analysis", _stage_code_analysis),
            ("test_points", _stage_test_points),
            ("case_generation", _stage_case_generation),
            ("case_review", _stage_case_review),
            ("execution", _stage_execution),
            ("result_analysis", _stage_result_analysis),
        )
        hard_failure = False
        had_failure = False

        for stage_name, handler in handlers:
            if hard_failure:
                continue
            try:
                result = handler(session, run, config)
                if result.get("status") == "failed":
                    had_failure = True
                    # 分析/生成失败后不再继续依赖后续阶段；报告阶段仍会执行
                    if stage_name in ("code_analysis", "test_points", "case_generation"):
                        hard_failure = True
            except Exception as exc:  # noqa: BLE001 - 阶段失败必须落库
                logger.exception("工作流阶段失败 run=%s stage=%s", run_id, stage_name)
                try:
                    session.rollback()
                except Exception:  # noqa: BLE001 - 连接已断时 rollback 也可能失败
                    logger.warning("阶段失败后 rollback 失败 run=%s stage=%s", run_id, stage_name)
                _persist_stage_failure(
                    run_id,
                    stage_name,
                    f"{STAGE_LABELS.get(stage_name, stage_name)}失败：{exc}",
                )
                # 原连接可能已被远端关闭；换一个 Session 继续完成阶段收尾和报告
                try:
                    session.close()
                except Exception:  # noqa: BLE001
                    pass
                session = SessionLocal()
                run = session.get(WorkflowRun, run_id)
                if run is None:
                    logger.warning("工作流记录已不存在 run=%s", run_id)
                    return
                run.error_message = (
                    f"{STAGE_LABELS.get(stage_name, stage_name)}失败：{exc}"
                )[:4000]
                session.commit()
                hard_failure = True
                had_failure = True

        if hard_failure:
            _mark_remaining_skipped(session, run, "前序阶段失败，未执行")

        # 报告阶段始终尝试执行，保证失败也有可读产物
        try:
            _stage_report(session, run, config)
        except Exception as exc:  # noqa: BLE001
            logger.exception("工作流报告阶段失败 run=%s", run_id)
            try:
                session.rollback()
            except Exception:  # noqa: BLE001
                pass
            _persist_stage_failure(run_id, "report", f"报告生成失败：{exc}")
            try:
                session.close()
            except Exception:  # noqa: BLE001
                pass
            session = SessionLocal()
            run = session.get(WorkflowRun, run_id)
            if run is None:
                return
            had_failure = True

        run = session.get(WorkflowRun, run.id)
        if run is None:
            logger.warning("工作流记录已不存在 run=%s", run_id)
            return
        run.finished_at = _now()
        run.current_stage = None
        if had_failure or hard_failure:
            run.status = "partial" if not hard_failure or _has_completed_work(session, run) else "failed"
        else:
            run.status = "completed"
        run.summary = _dumps(_build_summary(session, run))
        session.commit()
        logger.info("工作流完成 run=%s status=%s", run.id, run.status)
    except Exception as exc:  # noqa: BLE001 - 线程兜底
        logger.exception("工作流执行异常 run=%s", run_id)
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            pass
        _persist_run_failure(run_id, f"工作流执行异常：{exc}")
    finally:
        session.close()


def _has_completed_work(session: Session, run: WorkflowRun) -> bool:
    completed = session.scalars(
        select(WorkflowStage.stage).where(
            WorkflowStage.run_id == run.id, WorkflowStage.status == "completed"
        )
    ).all()
    return any(stage != "report" for stage in completed)


def recover_interrupted_runs() -> int:
    """服务重启后把内存线程已丢失的 running 运行标记为 failed，避免假运行状态。"""
    session = SessionLocal()
    try:
        runs = list(
            session.scalars(
                select(WorkflowRun).where(WorkflowRun.status.in_(["pending", "running"]))
            )
        )
        now = _now()
        for run in runs:
            run.status = "failed"
            run.current_stage = None
            run.error_message = "服务重启，工作流被中断；请重新发起一次运行"[:4000]
            run.finished_at = now
            stages = list(
                session.scalars(select(WorkflowStage).where(WorkflowStage.run_id == run.id))
            )
            for stage in stages:
                if stage.status == "running":
                    stage.status = "failed"
                    stage.error_message = "服务重启，阶段被中断"
                    stage.finished_at = now
                    stage.duration_ms = _elapsed_ms(stage.started_at)
                elif stage.status == "pending":
                    stage.status = "skipped"
                    stage.output_summary = "服务重启，未执行"
                    stage.finished_at = now
        # 先落库本次修正，避免 autoflush=False 的查询读到旧状态
        session.flush()
        # 清理历史不一致：运行已结束但阶段仍停在 running/pending
        stale_stages = list(
            session.scalars(
                select(WorkflowStage).where(WorkflowStage.status.in_(["running", "pending"]))
            )
        )
        for stage in stale_stages:
            parent = session.get(WorkflowRun, stage.run_id)
            if parent is None or parent.status in ("pending", "running"):
                continue
            stage.status = "failed" if stage.status == "running" else "skipped"
            stage.error_message = stage.error_message or "运行已结束，阶段状态被自动修正"
            stage.finished_at = now
            stage.duration_ms = _elapsed_ms(stage.started_at)
        session.commit()
        return len(runs)
    finally:
        session.close()


# ---------- 阶段 0：代码分析 ----------

def _analysis_payload(session: Session, pid: str) -> dict:
    result = analysis_service.get_analysis_result(session, pid)
    backend = result.get("backendResult") or {}
    frontend = result.get("frontendResult") or {}
    return {
        "status": result.get("status"),
        "endpoints": len(backend.get("endpoints") or []),
        "pages": len(frontend.get("pages") or []),
        "stateMachines": len(result.get("stateMachines") or []),
        "warnings": (result.get("warnings") or [])[:20],
        "errorMessage": result.get("errorMessage"),
    }


def _wait_for_analysis(session: Session, analysis_id: str, timeout: int = 180) -> CodeAnalysis | None:
    deadline = time.time() + timeout
    latest: CodeAnalysis | None = None
    while time.time() < deadline:
        session.expire_all()
        latest = session.get(CodeAnalysis, analysis_id)
        if latest is not None and latest.status in ("success", "failed"):
            return latest
        time.sleep(1)
    return latest


def _stage_code_analysis(session: Session, run: WorkflowRun, config: dict) -> dict:
    project = session.get(Project, run.project_id)
    if project is None:
        raise BizError(40401, "项目不存在")
    stage = _begin_stage(
        session,
        run,
        "code_analysis",
        "自动静态分析源码，为测试点和用例提供页面/字段/接口事实",
    )
    if not config.get("analyzeCode", True):
        _end_stage(session, stage, "skipped", "未启用自动代码分析", {"analyzeCode": False})
        return {"status": "skipped"}

    latest = analysis_service.get_latest_analysis(session, run.project_id)
    if latest is not None and latest.status == "running":
        latest = _wait_for_analysis(session, latest.id)
    if latest is not None and latest.status == "success" and not config.get("reanalyzeCode", False):
        payload = _analysis_payload(session, run.project_id)
        _end_stage(
            session,
            stage,
            "skipped",
            f"复用已有代码分析：{payload['endpoints']} 接口 / {payload['pages']} 页面",
            payload,
        )
        return {"status": "skipped", **payload}
    if latest is not None and latest.status == "running":
        payload = _analysis_payload(session, run.project_id)
        _end_stage(session, stage, "failed", "代码分析仍在进行", payload, "等待已有分析超时，请稍后重试")
        return {"status": "failed", **payload}
    if project.source_type != "local_path" or not project.source_path:
        payload = {"status": "failed", "errorMessage": "仅支持本地路径项目进行分析"}
        _end_stage(
            session,
            stage,
            "failed",
            "代码分析未执行",
            payload,
            "仅支持本地路径项目，请先在项目设置中填写 sourcePath",
        )
        return {"status": "failed", **payload}

    analysis = CodeAnalysis(project_id=run.project_id, status="running", created_at=_now())
    session.add(analysis)
    session.commit()
    analysis_service.run_analysis(run.project_id, analysis.id)
    session.expire_all()
    latest = session.get(CodeAnalysis, analysis.id) or analysis_service.get_latest_analysis(
        session, run.project_id
    )
    payload = _analysis_payload(session, run.project_id)
    payload["analysisId"] = analysis.id
    if latest is None or latest.status != "success":
        error = (latest.error_message if latest else "") or "代码分析未完成"
        _end_stage(session, stage, "failed", "代码分析失败", payload, error)
        return {"status": "failed", **payload}
    _end_stage(
        session,
        stage,
        "completed",
        f"代码分析完成：{payload['endpoints']} 接口 / {payload['pages']} 页面 / {payload['stateMachines']} 状态机",
        payload,
    )
    return {"status": "completed", **payload}


# ---------- 阶段 1：测试点 ----------

def _stage_test_points(session: Session, run: WorkflowRun, config: dict) -> dict:
    req = session.get(Requirement, run.requirement_id)
    if req is None:
        raise BizError(40406, "需求版本已不存在")
    existing = list(
        session.scalars(select(TestPoint).where(TestPoint.requirement_id == req.id))
    )
    stage = _begin_stage(
        session,
        run,
        "test_points",
        f"需求 {req.version} {req.name}；已有测试点 {len(existing)} 条；模式 {config.get('mode', 'auto')}",
    )
    if existing and not config.get("reanalyzePoints", False):
        summary = {"testPointCount": len(existing), "reused": True}
        _end_stage(session, stage, "skipped", f"复用已有测试点 {len(existing)} 条", summary)
        return {"status": "skipped", **summary}

    points = requirement_service.parse_points(session, req, config.get("mode", "auto"))
    if not points:
        _end_stage(session, stage, "failed", "没有解析出任何测试点", {"testPointCount": 0}, "需求文档无法解析出可执行测试点")
        return {"status": "failed", "testPointCount": 0}
    requirement_service.save_points(session, req, points)
    session.commit()

    by_category: dict[str, int] = {}
    by_source: dict[str, int] = {}
    for point in points:
        by_category[point["category"]] = by_category.get(point["category"], 0) + 1
        by_source[point.get("source", "rule")] = by_source.get(point.get("source", "rule"), 0) + 1
    payload = {
        "testPointCount": len(points),
        "reused": False,
        "byCategory": by_category,
        "bySource": by_source,
    }
    _end_stage(
        session,
        stage,
        "completed",
        f"生成 {len(points)} 条测试点（{', '.join(f'{k}:{v}' for k, v in by_source.items())}）",
        payload,
        model=_text_client().model if "llm" in by_source else None,
    )
    return {"status": "completed", **payload}


# ---------- 阶段 2：用例生成 ----------

def _cases_for_requirement(session: Session, pid: str, rid: str) -> list[tuple[TestCase, RequirementCase]]:
    """两步查询，避免 MySQL 下 test_cases.id 与 requirement_cases.test_case_id
    collation 不一致导致的 1267（0900_ai_ci vs general_ci）。

    现有 testcase_service.list_testcases 也采用同样策略：只在 Python 里拼接，
    不做 requirement_cases JOIN test_cases。
    """
    mappings = list(
        session.scalars(select(RequirementCase).where(RequirementCase.requirement_id == rid))
    )
    if not mappings:
        return []
    mapping_by_case: dict[str, RequirementCase] = {}
    for mapping in mappings:
        mapping_by_case.setdefault(mapping.test_case_id, mapping)
    cases = list(
        session.scalars(
            select(TestCase)
            .where(
                TestCase.project_id == pid,
                TestCase.id.in_(list(mapping_by_case)),
            )
            .order_by(TestCase.priority.asc(), TestCase.project_seq.asc())
        )
    )
    return [(tc, mapping_by_case[tc.id]) for tc in cases if tc.id in mapping_by_case]


def _automation_status(tc: TestCase) -> str:
    if tc.source in ("manual", "import"):
        return "manual"
    steps = _loads(tc.structured_steps, [])
    return "automated" if steps else "blocked"


def _stage_case_generation(session: Session, run: WorkflowRun, config: dict) -> dict:
    req = session.get(Requirement, run.requirement_id)
    if req is None:
        raise BizError(40406, "需求版本已不存在")
    existing = _cases_for_requirement(session, run.project_id, req.id)
    max_cases = int(config.get("maxCases") or 20)
    stage = _begin_stage(
        session,
        run,
        "case_generation",
        f"模式 {config.get('mode', 'auto')}；已有用例 {len(existing)} 条；上限 {max_cases}",
    )

    reused = bool(config.get("reuseExistingCases", True)) and not config.get("reanalyzePoints", False)
    payload: dict = {"reused": reused, "maxCases": max_cases}
    generation = None
    if reused and existing:
        pairs = existing[:max_cases]
        payload["caseCount"] = len(pairs)
        payload["generation"] = None
    else:
        generation = generation_service.generate(
            run.project_id,
            config.get("mode", "auto"),
            session=session,
            requirement_id=req.id,
        )
        pairs = _cases_for_requirement(session, run.project_id, req.id)[:max_cases]
        payload.update(
            {
                "caseCount": len(pairs),
                "generation": generation,
                "dropped": generation.get("dropped", 0),
                "dropReasons": generation.get("dropReasons", {}),
            }
        )
        if not pairs:
            payload["byAutomation"] = {}
            _end_stage(
                session,
                stage,
                "failed",
                "没有生成可用用例",
                payload,
                "质量闸拦截或 LLM/规则均未产出用例",
            )
            return {"status": "failed", **payload}

    # 建立本次运行的用例索引（已存在则复用行，避免重复运行产生重复关联）
    existing_wcs = {
        wc.case_id: wc
        for wc in session.scalars(select(WorkflowCase).where(WorkflowCase.run_id == run.id))
    }
    for position, (tc, mapping) in enumerate(pairs, start=1):
        wc = existing_wcs.get(tc.id)
        if wc is None:
            wc = WorkflowCase(
                id=uuid.uuid4().hex,
                run_id=run.id,
                case_id=tc.id,
                test_point_id=mapping.test_point_id,
                position=position,
                automation_status=_automation_status(tc),
                review_status="pending",
                quality_score=tc.quality_score or 0,
            )
            session.add(wc)
        else:
            wc.position = position
            wc.test_point_id = mapping.test_point_id
            wc.automation_status = _automation_status(tc)
    session.commit()

    payload["byAutomation"] = _counts(
        wc.automation_status
        for wc in session.scalars(select(WorkflowCase).where(WorkflowCase.run_id == run.id))
    )
    status = "skipped" if reused and existing else "completed"
    if status == "skipped":
        _end_stage(session, stage, status, f"复用已有用例 {len(pairs)} 条", payload)
    else:
        _end_stage(
            session,
            stage,
            status,
            f"生成并保留 {len(pairs)} 条用例（丢弃 {payload.get('dropped', 0)} 条）",
            payload,
            model=_text_client().model if generation and generation.get("mode") == "llm" else None,
        )
    return {"status": status, **payload}


def _counts(values) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        key = value or "unknown"
        out[key] = out.get(key, 0) + 1
    return out


# ---------- 阶段 3：用例审核 ----------

def _case_proxy(case: dict, test_point_id: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        title=case.get("title") or "",
        structured_steps=case.get("structuredSteps") or [],
        expected_results=case.get("expectedResults") or [],
        coverage_refs=case.get("coverageRefs") or {},
        test_point_id=test_point_id,
    )


def _required_fields(facts: dict) -> list[dict]:
    out: list[dict] = []
    for page in facts.get("pages") or []:
        for form in page.get("forms") or []:
            for field in form.get("fields") or []:
                if field.get("required"):
                    out.append(field)
    return out


def _missing_required_fields(case: dict, facts: dict) -> list[str]:
    steps = case.get("structuredSteps") or []
    corpus_parts: list[str] = []
    for step in steps:
        if step.get("type") != "input":
            continue
        selector = (step.get("uiSelector") or {}).get("value") or ""
        corpus_parts.append(f"{selector} {step.get('target') or ''} {step.get('action') or ''}")
    corpus = " ".join(corpus_parts)
    missing: list[str] = []
    for field in _required_fields(facts):
        tokens = [field.get("placeholder"), field.get("label"), field.get("prop")]
        if not any(token and str(token) in corpus for token in tokens):
            missing.append(str(field.get("label") or field.get("prop") or field.get("placeholder") or "必填字段"))
    return missing


def _deterministic_review(
    case: dict,
    facts: dict,
    frontend_dict: dict,
    requirement_text: str,
    point: TestPoint | None,
) -> dict:
    blocking: list[str] = []
    fixable: list[str] = []
    warnings: list[str] = []
    steps = case.get("structuredSteps") or []
    proxy = _case_proxy(case, point.id if point else None)

    if not steps:
        blocking.append("没有结构化步骤，无法自动执行")
    if not case.get("expectedResults"):
        blocking.append("缺少预期结果，无法判定成功/失败")
    if case_has_compound_expected(proxy):
        blocking.append("包含复合预期：一条用例判断多个输入/结果，必须拆分")
    if case_has_multiple_inputs(proxy):
        blocking.append("同一字段被填入多个不同值：一条用例验证多个输入场景，必须拆分")

    # 源码/需求事实里找不到的“页面文案”可能是分析器漏采，也可能是编造；
    # 没有运行时证据时只能记为非阻塞风险，不能直接判定用例不可执行。
    advisory_markers = (
        "按钮文案不在源码/需求事实中",
        "断言锚点不在源码/需求事实中",
        "testData 字段不在源码/需求事实中",
    )
    for err in case_review_service._validate_case(case, frontend_dict, requirement_text, point):
        bucket = warnings if any(marker in err for marker in advisory_markers) else fixable
        if err not in bucket and err not in blocking:
            bucket.append(err)

    for label in _missing_required_fields(case, facts):
        message = f"必填字段未填写：{label}"
        if message not in fixable:
            fixable.append(message)

    all_issues = blocking + fixable + warnings
    score = score_case(proxy, all_issues)
    if blocking:
        score = min(score, 30)
    return {
        "blocking": blocking,
        "fixable": fixable,
        "warnings": warnings,
        "qualityScore": score,
        "issues": all_issues,
    }


def _issues_for_review(
    blocking: list[str],
    fixable: list[str],
    warnings: list[str] | None,
    llm_issues: list[dict],
) -> list[dict]:
    issues: list[dict] = []
    seen: set[str] = set()
    for problem in blocking:
        if problem in seen:
            continue
        seen.add(problem)
        issues.append(
            {
                "severity": "blocker",
                "field": "structuredSteps",
                "problem": problem,
                "suggestion": "按单点单场景拆分为独立用例，或修复为可执行步骤后重新审核",
                "evidence": "确定性校验",
            }
        )
    for problem in fixable:
        if problem in seen:
            continue
        seen.add(problem)
        issues.append(
            {
                "severity": "warning",
                "field": "structuredSteps",
                "problem": problem,
                "suggestion": "补齐必填字段、真实选择器和可观察断言后重新审核",
                "evidence": "确定性校验",
            }
        )
    for problem in warnings or []:
        if problem in seen:
            continue
        seen.add(problem)
        issues.append(
            {
                "severity": "info",
                "field": "structuredSteps",
                "problem": problem,
                "suggestion": "运行时确认页面实际文案；若源码事实缺失可补充代码分析",
                "evidence": "确定性校验（非阻塞）",
            }
        )
    for item in llm_issues or []:
        problem = str(item.get("problem") or "").strip()
        if not problem or problem in seen:
            continue
        seen.add(problem)
        issues.append(item)
    return issues[:20]


def _llm_deduction(issues: list[dict]) -> int:
    deduction = 0
    for item in issues:
        severity = item.get("severity")
        deduction += 25 if severity == "blocker" else 8 if severity == "warning" else 2
    return deduction


def _compose_review_status(blocking: list[str], fixable: list[str], llm_review: dict | None, can_repair: bool) -> str:
    if blocking:
        return "reject"
    llm_status = (llm_review or {}).get("review_status")
    if llm_status == "reject":
        return "revise" if can_repair else "reject"
    if fixable or llm_status == "revise":
        return "revise" if can_repair else "reject"
    return "pass"


def _record_review(
    session: Session,
    run: WorkflowRun,
    wc: WorkflowCase,
    round_no: int,
    status: str,
    score: int,
    issues: list[dict],
    missing_coverage: list[str],
    suggestions: list[str],
    deterministic_issues: list[str],
    source: str,
    model: str,
) -> WorkflowCaseReview:
    validated = CaseReviewResult.model_validate(
        {
            "case_id": wc.case_id,
            "review_status": status,
            "quality_score": max(0, min(100, int(score))),
            "issues": issues,
            "missing_coverage": missing_coverage,
            "suggestions": suggestions,
            "deterministic_issues": deterministic_issues,
            "source": source,
            "model": model,
        }
    )
    row = WorkflowCaseReview(
        id=uuid.uuid4().hex,
        run_id=run.id,
        case_id=wc.case_id,
        round=round_no,
        review_status=validated.review_status,
        quality_score=validated.quality_score,
        issues=_dumps([item.model_dump() for item in validated.issues]),
        missing_coverage=_dumps(validated.missing_coverage),
        suggestions=_dumps(validated.suggestions),
        deterministic_issues=_dumps(validated.deterministic_issues),
        source=validated.source,
        model=validated.model,
    )
    session.add(row)
    session.flush()
    if wc.first_review_id is None:
        wc.first_review_id = row.id
    wc.final_review_id = row.id
    wc.review_status = validated.review_status
    wc.quality_score = validated.quality_score
    return row


def _stage_case_review(session: Session, run: WorkflowRun, config: dict) -> dict:
    requirement = session.get(Requirement, run.requirement_id)
    if requirement is None:
        raise BizError(40406, "需求版本已不存在")
    stage = _begin_stage(
        session,
        run,
        "case_review",
        f"审核本次 #{run.id[:8]} 的用例；最多修订 {config.get('maxRevisions', 2)} 轮",
    )
    if not config.get("reviewEnabled", True):
        _end_stage(session, stage, "skipped", "配置关闭了用例审核", {"reviewEnabled": False})
        # 关闭审核时仍生成审核记录，明确标记未审核，不静默当作通过
        for wc in session.scalars(select(WorkflowCase).where(WorkflowCase.run_id == run.id)):
            if wc.final_review_id is None:
                row = _record_review(
                    session, run, wc, 1, "revise", wc.quality_score or 0,
                    [{"severity": "warning", "field": "case", "problem": "审核被配置关闭，未执行审核", "suggestion": "如需执行请开启 reviewEnabled", "evidence": "配置"}],
                    [], ["审核被配置关闭"], [], "deterministic", "",
                )
                _ = row
        session.commit()
        return {"status": "skipped"}

    client = _text_client()
    max_revisions = max(0, min(2, int(config.get("maxRevisions", 2))))
    facts_cache: dict[tuple[str, str | None], tuple] = {}
    counts = {"pass": 0, "revise": 0, "reject": 0, "manual": 0, "revisions": 0, "errors": 0}

    wcs = list(
        session.scalars(
            select(WorkflowCase).where(WorkflowCase.run_id == run.id).order_by(WorkflowCase.position)
        )
    )
    for wc in wcs:
        try:
            tc = session.get(TestCase, wc.case_id)
            if tc is None:
                _record_review(
                    session, run, wc, 1, "reject", 0,
                    [{"severity": "blocker", "field": "case", "problem": "用例已不存在", "suggestion": "重新生成用例", "evidence": "数据库"}],
                    [], [], ["用例已不存在"], "deterministic", "",
                )
                counts["reject"] += 1
                continue

            mapping = session.scalars(
                select(RequirementCase).where(RequirementCase.test_case_id == tc.id)
            ).first()
            point = session.get(TestPoint, mapping.test_point_id) if mapping and mapping.test_point_id else None
            cache_key = (requirement.id, point.id if point else None)
            if cache_key not in facts_cache:
                facts_cache[cache_key] = case_review_service._source_facts(
                    session, run.project_id, requirement, point
                )
            facts, _backend_dict, frontend_dict, _machines = facts_cache[cache_key]
            point_id = point.id if point else (mapping.test_point_id if mapping else None)

            if wc.automation_status == "manual":
                _record_review(
                    session, run, wc, 1, "pass", tc.quality_score or 100,
                    [], [], ["手工用例，不进入自动执行"], [], "deterministic", "",
                )
                wc.review_status = "pass"
                wc.automation_status = "manual"
                counts["manual"] += 1
                session.commit()
                continue

            if wc.automation_status == "blocked":
                _record_review(
                    session, run, wc, 1, "reject", 0,
                    [{"severity": "blocker", "field": "structuredSteps", "problem": "没有结构化步骤，无法自动执行", "suggestion": "补充可执行步骤或改为手工用例", "evidence": "用例快照"}],
                    [], [], ["没有结构化步骤"], "deterministic", "",
                )
                counts["reject"] += 1
                session.commit()
                continue

            revisions = 0
            round_no = 0
            repair_error = ""
            fallback_pass = False
            while True:
                round_no += 1
                case = generation_service.case_to_dict(tc)
                case["requirement"] = facts.get("requirement") or {}
                case["testPoint"] = facts.get("testPoint") or {}
                deterministic = _deterministic_review(
                    case,
                    facts,
                    frontend_dict,
                    (facts.get("requirement") or {}).get("excerpt") or "",
                    point,
                )
                llm_review = None
                llm_note = ""
                if client.available() and not deterministic["blocking"]:
                    try:
                        # 不要把 MySQL 连接握在手里等 LLM：远端 wait_timeout / 网络设备
                        # 可能在几十秒后关闭空闲连接，等模型返回后再 INSERT 会 2013。
                        session.commit()
                        llm_review = case_review_agent.review_case_workflow(
                            client,
                            case,
                            facts,
                            deterministic_issues=deterministic["issues"],
                            deterministic_blocking=deterministic["blocking"],
                            quality_score=deterministic["qualityScore"],
                        )
                    except Exception as exc:  # noqa: BLE001 - 审核失败按确定性结果继续
                        logger.warning("用例 %s AI 审核失败: %s", wc.case_id, exc)
                        llm_note = f"AI 审核不可用：{exc}"
                elif not client.available():
                    llm_note = "未配置 LLM，仅使用确定性校验"

                can_repair = revisions < max_revisions and client.available()
                llm_status = (llm_review or {}).get("review_status")
                status = _compose_review_status(
                    deterministic["blocking"],
                    deterministic["fixable"],
                    llm_review,
                    can_repair,
                )
                llm_issues = (llm_review or {}).get("issues") or []
                issues = _issues_for_review(
                    deterministic["blocking"],
                    deterministic["fixable"],
                    deterministic["warnings"],
                    llm_issues,
                )
                score = max(0, deterministic["qualityScore"] - _llm_deduction(llm_issues))
                if deterministic["blocking"]:
                    score = min(score, 30)
                suggestions = list((llm_review or {}).get("suggestions") or [])
                if llm_note:
                    suggestions.append(llm_note)
                source = "mixed" if llm_review else "deterministic"
                _record_review(
                    session,
                    run,
                    wc,
                    round_no,
                    status,
                    score,
                    issues,
                    (llm_review or {}).get("missing_coverage") or [],
                    suggestions,
                    deterministic["issues"],
                    source,
                    client.model if llm_review else "",
                )
                session.commit()

                if status != "revise":
                    break
                if revisions >= max_revisions:
                    break
                if not client.available():
                    break

                repair_issues = _issues_for_review(
                    deterministic["blocking"], deterministic["fixable"], [], llm_issues
                )
                revisions += 1
                try:
                    case_review_service.repair_case(
                        session, run.project_id, wc.case_id, repair_issues, client=client
                    )
                    session.commit()
                except Exception as exc:  # noqa: BLE001 - 修复失败不丢用例
                    logger.warning("用例 %s 自动修复失败: %s", wc.case_id, exc)
                    repair_error = str(exc)
                    # 修复服务未提交时可能留下部分修改，先丢弃
                    run_id_local = run.id
                    wc_id_local = wc.id
                    session.rollback()
                    run = session.get(WorkflowRun, run_id_local)
                    wc = session.get(WorkflowCase, wc_id_local)
                    if run is None or wc is None:
                        break
                    # 原用例本身已通过确定性校验、只是 AI 建议修订而补丁无效时，
                    # 不应把原用例判死；保留原用例并记录回退说明，让执行结果说话。
                    if (
                        not deterministic["blocking"]
                        and not deterministic["fixable"]
                        and llm_status == "revise"
                    ):
                        round_no += 1
                        note = (
                            "AI 修复补丁未通过源码事实校验，已保留原用例并继续执行："
                            f"{repair_error}"
                        )
                        _record_review(
                            session,
                            run,
                            wc,
                            round_no,
                            "pass",
                            deterministic["qualityScore"],
                            [
                                {
                                    "severity": "info",
                                    "field": "case",
                                    "problem": note,
                                    "suggestion": "原用例已通过确定性校验；请结合执行结果判断是否仍需人工修订",
                                    "evidence": "工作流修复回退",
                                }
                            ],
                            [],
                            [note],
                            [],
                            "deterministic",
                            "",
                        )
                        fallback_pass = True
                        session.commit()
                    break
                tc = session.get(TestCase, wc.case_id)
                if tc is None:
                    repair_error = "修复后用例不存在"
                    break

            wc.revision_count = revisions
            counts["revisions"] += revisions
            if not fallback_pass and wc.review_status == "revise":
                round_no += 1
                reason = repair_error or (
                    f"已达到最大修订轮次 {max_revisions}，仍未通过审核"
                    if revisions >= max_revisions
                    else "AI 审核/修复不可用，存在未修复问题"
                )
                _record_review(
                    session,
                    run,
                    wc,
                    round_no,
                    "reject",
                    min(wc.quality_score, 40),
                    [{"severity": "blocker", "field": "case", "problem": reason, "suggestion": "人工检查后重新生成或手工修复", "evidence": "工作流审核收敛规则"}],
                    [], [reason], [reason], "deterministic", "",
                )
                repair_error = reason

            if wc.review_status == "pass":
                wc.automation_status = "automated"
                counts["pass"] += 1
            else:
                wc.automation_status = "blocked"
                counts["reject"] += 1
            session.commit()
        except Exception as exc:  # noqa: BLE001 - 单条审核失败不拖垮整个阶段
            logger.exception("用例 %s 审核异常", wc.case_id)
            session.rollback()
            wc = session.get(WorkflowCase, wc.id)
            if wc is not None:
                try:
                    _record_review(
                        session, run, wc, 1, "reject", 0,
                        [{"severity": "blocker", "field": "case", "problem": f"审核异常：{exc}", "suggestion": "人工检查用例和 LLM 配置", "evidence": "工作流异常"}],
                        [], [], [f"审核异常：{exc}"], "deterministic", "",
                    )
                    wc.automation_status = "blocked"
                    session.commit()
                except Exception:  # noqa: BLE001
                    session.rollback()
            counts["errors"] += 1
            counts["reject"] += 1

    payload = {
        **counts,
        "avgQuality": (
            round(
                sum(wc.quality_score for wc in wcs if wc.review_status == "pass")
                / max(1, sum(1 for wc in wcs if wc.review_status == "pass")),
                1,
            )
            if any(wc.review_status == "pass" for wc in wcs)
            else 0
        ),
        "maxRevisions": max_revisions,
        "llmAvailable": client.available(),
    }
    _end_stage(
        session,
        stage,
        "completed",
        f"审核 {len(wcs)} 条：通过 {counts['pass']}，拒绝 {counts['reject']}，手工 {counts['manual']}，修订 {counts['revisions']} 轮",
        payload,
        model=client.model if client.available() else None,
    )
    return {"status": "completed", **payload}


# ---------- 阶段 4：自动执行 ----------

def _stage_execution(session: Session, run: WorkflowRun, config: dict) -> dict:
    stage = _begin_stage(session, run, "execution", "执行审核通过且自动化的用例")
    if not config.get("autoExecute", True):
        _end_stage(session, stage, "skipped", "配置关闭了自动执行", {"autoExecute": False})
        return {"status": "skipped"}

    approved = list(
        session.scalars(
            select(WorkflowCase)
            .where(
                WorkflowCase.run_id == run.id,
                WorkflowCase.review_status == "pass",
                WorkflowCase.automation_status == "automated",
            )
            .order_by(WorkflowCase.position)
        )
    )
    if not approved:
        _end_stage(session, stage, "skipped", "没有审核通过且可自动执行的用例", {"approved": 0})
        return {"status": "skipped", "approved": 0}

    executed: list[dict] = []
    errors: list[dict] = []
    for wc in approved:
        try:
            # 执行单条用例可能持续几十秒：先释放工作流 Session 的连接
            session.commit()
            result = execution_service.execute_single(
                run.project_id, wc.case_id, config.get("targetUrl") or None
            )
            wc.execution_id = result["executionId"]
            session.commit()
            executed.append(
                {
                    "caseId": wc.case_id,
                    "executionId": result["executionId"],
                    "status": result.get("status"),
                    "durationMs": result.get("durationMs"),
                }
            )
        except Exception as exc:  # noqa: BLE001 - 单条执行失败不影响其它用例
            logger.warning("工作流执行用例失败 %s: %s", wc.case_id, exc)
            errors.append({"caseId": wc.case_id, "error": str(exc)[:1000]})
            session.rollback()

    payload = {
        "approved": len(approved),
        "executed": len(executed),
        "failedToStart": len(errors),
        "errors": errors,
        "byStatus": _counts(item.get("status") for item in executed),
        "executions": executed,
    }
    if not executed and errors:
        _end_stage(
            session,
            stage,
            "failed",
            f"执行未启动：{len(errors)} 条用例全部失败",
            payload,
            error=errors[0]["error"],
        )
        return {"status": "failed", **payload}
    _end_stage(
        session,
        stage,
        "completed",
        f"执行 {len(executed)}/{len(approved)} 条；启动失败 {len(errors)} 条",
        payload,
    )
    return {"status": "completed", **payload}


# ---------- 阶段 5：结果分析 ----------

def _build_evidence(session: Session, wc: WorkflowCase, requirement: Requirement | None) -> dict:
    record = session.get(ExecutionRecord, wc.execution_id) if wc.execution_id else None
    tc = session.get(TestCase, wc.case_id)
    snapshot = _loads(record.test_case_snapshot, {}) if record else (
        generation_service.case_to_dict(tc) if tc else {}
    )
    steps = []
    if record is not None:
        rows = list(
            session.scalars(
                select(ExecutionStep)
                .where(ExecutionStep.execution_id == record.id)
                .order_by(ExecutionStep.step_index)
            )
        )
        steps = [
            {
                "index": row.step_index,
                "type": row.step_type,
                "action": row.action,
                "target": row.target,
                "result": row.result,
                "selectorUsed": row.selector_used,
                "error": row.error,
                "screenshotBefore": row.screenshot_before,
                "screenshotAfter": row.screenshot_after,
            }
            for row in rows[:20]
        ]

    vision = {}
    if record is not None:
        review = session.scalars(
            select(ExecutionReview).where(ExecutionReview.execution_id == record.id)
        ).first()
        if review is not None:
            review_data = execution_service.review_with_issues(session, review) or {}
            vision = {
                "verdict": review_data.get("verdict"),
                "reason": review_data.get("reason"),
                "issues": (review_data.get("issues") or [])[:8],
                "imageReviews": (review_data.get("imageReviews") or [])[:8],
            }

    point = session.get(TestPoint, wc.test_point_id) if wc.test_point_id else None
    return {
        "caseId": wc.case_id,
        "caseTitle": snapshot.get("title") or (record.test_case_title if record else ""),
        "caseType": snapshot.get("type"),
        "expectedResults": snapshot.get("expectedResults") or [],
        "executionStatus": record.status if record else "unknown",
        "errorCategory": record.error_category if record else None,
        "errorMessage": record.error_message if record else None,
        "targetUrl": record.target_url if record else None,
        "durationMs": record.duration_ms if record else 0,
        "passedSteps": record.passed_steps if record else 0,
        "totalSteps": record.total_steps if record else 0,
        "steps": steps,
        "visionReview": vision,
        "testPoint": {
            "id": point.id if point else None,
            "title": point.title if point else "",
            "category": point.category if point else "",
            "expected": point.expected if point else "",
        },
        "requirement": {
            "version": requirement.version if requirement else "",
            "name": requirement.name if requirement else "",
            "excerpt": (requirement.content or "")[:4000] if requirement else "",
        },
    }


def _stage_result_analysis(session: Session, run: WorkflowRun, config: dict) -> dict:
    stage = _begin_stage(session, run, "result_analysis", "基于步骤/截图审核/错误码分析失败原因")
    if not config.get("analyzeEnabled", True):
        _end_stage(session, stage, "skipped", "配置关闭了结果分析", {"analyzeEnabled": False})
        return {"status": "skipped"}

    requirement = session.get(Requirement, run.requirement_id)
    analyzed_cases = list(
        session.scalars(
            select(WorkflowCase)
            .where(WorkflowCase.run_id == run.id, WorkflowCase.execution_id.is_not(None))
            .order_by(WorkflowCase.position)
        )
    )
    if not analyzed_cases:
        _end_stage(session, stage, "skipped", "本次没有可分析的执行记录", {"analyzed": 0})
        return {"status": "skipped", "analyzed": 0}

    client = _text_client()
    rows = 0
    errors = 0
    by_category: dict[str, int] = {}
    sources: dict[str, int] = {}
    for wc in analyzed_cases:
        try:
            evidence = _build_evidence(session, wc, requirement)
            # 结果分析可能调用 LLM：先结束只读事务，避免连接空闲被 MySQL 关闭
            session.commit()
            result = result_analysis_agent.analyze(client, evidence)
            validated = ResultAnalysisResult.model_validate(
                {
                    "case_id": wc.case_id,
                    "execution_status": evidence.get("executionStatus") or "unknown",
                    **result,
                }
            )
            row = WorkflowResultAnalysis(
                id=uuid.uuid4().hex,
                run_id=run.id,
                execution_id=wc.execution_id,
                case_id=wc.case_id,
                execution_status=validated.execution_status,
                failure_category=validated.failure_category,
                failure_reason=validated.failure_reason,
                evidence=_dumps(validated.evidence),
                confidence=validated.confidence,
                recommended_action=validated.recommended_action,
                needs_human_review=1 if validated.needs_human_review else 0,
                source=validated.source,
                model=validated.model,
                raw_response=_dumps(evidence)[:20000],
            )
            session.add(row)
            session.flush()
            wc.analysis_id = row.id
            rows += 1
            by_category[row.failure_category] = by_category.get(row.failure_category, 0) + 1
            sources[row.source] = sources.get(row.source, 0) + 1
            session.commit()
        except Exception as exc:  # noqa: BLE001 - 单条分析失败要落 unknown，不能丢
            logger.exception("结果分析失败 case=%s", wc.case_id)
            session.rollback()
            rows += 1
            errors += 1
            try:
                row = WorkflowResultAnalysis(
                    id=uuid.uuid4().hex,
                    run_id=run.id,
                    execution_id=wc.execution_id or "",
                    case_id=wc.case_id,
                    execution_status="unknown",
                    failure_category="unknown",
                    failure_reason=f"分析异常，未能形成可靠结论：{exc}"[:2000],
                    evidence=_dumps([f"分析异常：{type(exc).__name__}"]),
                    confidence=0.0,
                    recommended_action="人工查看执行记录和截图后判定",
                    needs_human_review=1,
                    source="rule",
                    model="",
                )
                session.add(row)
                session.flush()
                wc.analysis_id = row.id
                by_category["unknown"] = by_category.get("unknown", 0) + 1
                sources["rule"] = sources.get("rule", 0) + 1
                session.commit()
            except Exception:  # noqa: BLE001
                session.rollback()

    payload = {
        "analyzed": rows,
        "errors": errors,
        "byCategory": by_category,
        "bySource": sources,
        "llmAvailable": client.available(),
    }
    _end_stage(
        session,
        stage,
        "completed",
        f"分析 {rows} 条执行结果；分类：{', '.join(f'{k}:{v}' for k, v in by_category.items()) or '无'}",
        payload,
        model=client.model if client.available() else None,
    )
    return {"status": "completed", **payload}


# ---------- 汇总与报告 ----------

def _build_summary(session: Session, run: WorkflowRun) -> dict:
    stages = list(
        session.scalars(select(WorkflowStage).where(WorkflowStage.run_id == run.id))
    )
    wcs = list(session.scalars(select(WorkflowCase).where(WorkflowCase.run_id == run.id)))
    reviews = list(
        session.scalars(select(WorkflowCaseReview).where(WorkflowCaseReview.run_id == run.id))
    )
    analyses = list(
        session.scalars(select(WorkflowResultAnalysis).where(WorkflowResultAnalysis.run_id == run.id))
    )
    executions = [
        session.get(ExecutionRecord, wc.execution_id)
        for wc in wcs
        if wc.execution_id
    ]
    executions = [record for record in executions if record is not None]
    points = session.scalars(
        select(TestPoint).where(TestPoint.requirement_id == run.requirement_id)
    ).all()
    scores = [wc.quality_score for wc in wcs if wc.review_status == "pass"]
    code_stage = next((stage for stage in stages if stage.stage == "code_analysis"), None)
    return {
        "stageStatuses": {stage.stage: stage.status for stage in stages},
        "codeAnalysis": _loads(code_stage.payload, {}) if code_stage else {},
        "testPoints": len(points),
        "cases": len(wcs),
        "automation": _counts(wc.automation_status for wc in wcs),
        "review": _counts(wc.review_status for wc in wcs),
        "reviews": len(reviews),
        "revisions": sum(wc.revision_count for wc in wcs),
        "avgQuality": round(sum(scores) / len(scores), 1) if scores else 0,
        "executions": len(executions),
        "executionStatuses": _counts(record.status for record in executions),
        "analysis": _counts(item.failure_category for item in analyses),
        "analysisSources": _counts(item.source for item in analyses),
        "errors": {
            "stages": [stage.stage for stage in stages if stage.status == "failed"],
            "executionStart": sum(
                1
                for stage in stages
                if stage.stage == "execution"
                for item in (_loads(stage.payload, {}).get("errors") or [])
            ),
        },
    }


def _stage_report(session: Session, run: WorkflowRun, config: dict) -> dict:
    stage = _begin_stage(session, run, "report", "汇总各阶段结果并生成报告入口")
    _end_stage(session, stage, "completed", "工作流报告已生成", {})
    # 先结束 report 阶段，再汇总，保证 payload 里的 report 状态是 completed
    summary = _build_summary(session, run)
    stage.payload = _dumps({**summary, "reportUrl": f"/api/workflows/{run.id}/report"})
    session.commit()
    return {"status": "completed", **summary}


# ---------- 查询 ----------

def get_run(session: Session, run_id: str) -> WorkflowRun:
    run = session.get(WorkflowRun, run_id)
    if run is None:
        raise BizError(40407, f"工作流运行不存在: {run_id}", http_status=404)
    return run


def list_runs(session: Session, pid: str, limit: int = 20) -> list[dict]:
    project = session.get(Project, pid)
    if project is None:
        raise BizError(40401, f"项目不存在: {pid}", http_status=404)
    runs = session.scalars(
        select(WorkflowRun)
        .where(WorkflowRun.project_id == pid)
        .order_by(WorkflowRun.created_at.desc())
        .limit(max(1, min(100, limit)))
    )
    return [run_to_dict(run) for run in runs]


def get_run_detail(session: Session, run_id: str) -> dict:
    run = get_run(session, run_id)
    stages = list(
        session.scalars(
            select(WorkflowStage)
            .where(WorkflowStage.run_id == run.id)
            .order_by(WorkflowStage.created_at)
        )
    )
    stage_order = {name: index for index, name in enumerate(STAGES)}
    stages.sort(key=lambda item: stage_order.get(item.stage, 999))
    wcs = list(
        session.scalars(
            select(WorkflowCase).where(WorkflowCase.run_id == run.id).order_by(WorkflowCase.position)
        )
    )
    cases = []
    for wc in wcs:
        tc = session.get(TestCase, wc.case_id)
        reviews = list(
            session.scalars(
                select(WorkflowCaseReview)
                .where(WorkflowCaseReview.run_id == run.id, WorkflowCaseReview.case_id == wc.case_id)
                .order_by(WorkflowCaseReview.round)
            )
        )
        analysis = session.get(WorkflowResultAnalysis, wc.analysis_id) if wc.analysis_id else None
        record = session.get(ExecutionRecord, wc.execution_id) if wc.execution_id else None
        cases.append(
            {
                "id": wc.id,
                "caseId": wc.case_id,
                "title": tc.title if tc else (record.test_case_title if record else "用例已删除"),
                "type": tc.type if tc else "",
                "priority": tc.priority if tc else "",
                "source": tc.source if tc else "",
                "position": wc.position,
                "testPointId": wc.test_point_id,
                "automationStatus": wc.automation_status,
                "reviewStatus": wc.review_status,
                "revisionCount": wc.revision_count,
                "qualityScore": wc.quality_score,
                "firstReview": review_to_dict(reviews[0]) if reviews else None,
                "finalReview": review_to_dict(reviews[-1]) if reviews else None,
                "reviews": [review_to_dict(item) for item in reviews],
                "execution": {
                    "id": record.id,
                    "status": record.status,
                    "statusLabel": execution_status_meta(record.status)["label"],
                    "errorCategory": record.error_category,
                    "errorMessage": record.error_message,
                    "durationMs": record.duration_ms,
                    "startedAt": to_utc_iso(record.start_time),
                }
                if record
                else None,
                "analysis": analysis_to_dict(analysis) if analysis else None,
            }
        )
    return {
        **run_to_dict(run),
        "stages": [stage_to_dict(stage) for stage in stages],
        "cases": cases,
    }
