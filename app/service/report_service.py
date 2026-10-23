"""HTML 报告：Jinja2 单文件自包含，截图相对路径引用。"""
from __future__ import annotations

import json
from collections import Counter

from jinja2 import Template
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.entity.models import (
    ExecutionRecord,
    ExecutionRequirement,
    ExecutionStep,
    Project,
    TestCase,
    WorkflowCase,
    WorkflowCaseReview,
    WorkflowResultAnalysis,
    WorkflowRun,
    WorkflowStage,
)
from app.service.coverage_service import coverage_matrix

WORKFLOW_STAGE_LABELS = {
    "code_analysis": "代码分析",
    "test_points": "测试点生成",
    "case_generation": "用例生成",
    "case_review": "用例审核",
    "execution": "自动执行",
    "result_analysis": "结果分析",
    "report": "报告汇总",
}

REPORT_TMPL = Template("""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><title>{{ project_name }} 执行报告</title>
<style>
body{font-family:-apple-system,'Microsoft YaHei',sans-serif;margin:24px;color:#303133}
h1{font-size:22px}.rate{font-size:48px;font-weight:700;color:#67c23a}
.statbar{display:flex;gap:16px;margin:16px 0}.stat{background:#f5f7fa;padding:12px 20px;border-radius:6px}
.stat b{display:block;font-size:20px}
details{border:1px solid #e4e7ed;border-radius:6px;margin:8px 0;padding:8px 12px}
summary{cursor:pointer;font-weight:600}
.pass{color:#67c23a}.fail{color:#f56c6c}.skip{color:#e6a23c}
table{width:100%;border-collapse:collapse;margin:8px 0}td,th{border:1px solid #e4e7ed;padding:6px;font-size:13px}
img{max-width:320px;border:1px solid #e4e7ed;margin:4px}
.bar{height:8px;background:#ebeef5;border-radius:4px;overflow:hidden}.bar i{display:block;height:100%;background:#409eff}
</style></head><body>
<h1>{{ project_name }} · 执行报告</h1>
<div>生成时间：{{ generated_at }}</div>
<div class="rate">{{ pass_rate }}</div>
<div class="statbar">
  <div class="stat">总数<b>{{ total }}</b></div>
  <div class="stat">通过<b class="pass">{{ passed }}</b></div>
  <div class="stat">失败<b class="fail">{{ failed }}</b></div>
  <div class="stat">跳过<b class="skip">{{ skipped }}</b></div>
  <div class="stat">平均耗时<b>{{ avg_ms }}ms</b></div>
</div>
{% if workflow %}
<h2>多 Agent 测试工作流</h2>
<div>需求版本：{{ workflow.requirementVersion or '-' }} {{ workflow.requirementName or '' }} · 状态：<b>{{ workflow.status }}</b> · 启动：{{ workflow.createdAt or '-' }}</div>
<table>
<thead><tr><th>阶段</th><th>状态</th><th>结果</th><th>耗时</th></tr></thead>
<tbody>
{% for s in workflow.stages %}
<tr><td>{{ s.label }}</td><td>{{ s.status }}</td><td>{{ s.outputSummary or '' }}{% if s.errorMessage %}<span class="fail">（{{ s.errorMessage }}）</span>{% endif %}</td><td>{{ s.durationMs }}ms</td></tr>
{% endfor %}
</tbody></table>
<h3>用例审核与结果分析</h3>
<table>
<thead><tr><th>用例</th><th>自动化</th><th>审核</th><th>质量分</th><th>修订</th><th>执行</th><th>根因分类</th><th>分析证据</th></tr></thead>
<tbody>
{% for c in workflow.cases %}
<tr>
<td>{{ c.title }}</td><td>{{ c.automationStatus }}</td><td>{{ c.reviewStatus }}</td><td>{{ c.qualityScore }}</td><td>{{ c.revisionCount }}</td>
<td>{{ c.executionStatus or '-' }}</td><td>{{ c.failureCategory or '-' }}</td>
<td>{{ c.failureReason or '' }}{% if c.evidence %}<br><small>{{ c.evidence|join('；') }}</small>{% endif %}</td>
</tr>
{% endfor %}
</tbody></table>
{% endif %}
<h2>失败分类</h2>
<ul>{% for cat, n in fail_categories %}<li>{{ cat }}：{{ n }}</li>{% else %}<li>无失败</li>{% endfor %}</ul>
<h2>覆盖矩阵</h2>
{% for k, v in matrix.items() %}{% if k in ('apiEndpoint','stateTransition','businessRule') %}
<div style="margin:8px 0">{{ k }}：规划 {{ (v.plannedRate*100)|round(1) }}% / 执行 {{ (v.executedRate*100)|round(1) }}%（{{ v.executedCovered }}/{{ v.total }}）</div>
<div class="bar"><i style="width:{{ (v.executedRate*100)|round(1) }}%"></i></div>
{% endif %}{% endfor %}
<h2>用例明细</h2>
{% for r in records %}
<details>
<summary>{{ r.test_case_title }} —— <span class="{{ r.status }}">{{ r.status }}</span>（{{ r.duration_ms }}ms）</summary>
{% if r.error_category %}<p>错误：{{ r.error_category }} {{ r.error_message or '' }}</p>{% endif %}
{% if r.video_path %}<p>录屏：<a href="{{ r.video_path }}">{{ r.video_path }}</a></p>{% endif %}
{% for s in r.steps %}<div style="margin:8px 0"><b>步骤 {{ s.step_index }} [{{ s.result }}]</b> {{ s.action }}</div>
{% if s.screenshot_before %}<img src="{{ s.screenshot_before }}" alt="before">{% endif %}
{% if s.screenshot_after %}<img src="{{ s.screenshot_after }}" alt="after">{% endif %}
{% if s.error %}<div class="fail">{{ s.error }}</div>{% endif %}{% endfor %}
</details>
{% endfor %}
</body></html>""")


def _workflow_context(session: Session, run_id: str) -> tuple[dict | None, set[str]]:
    run = session.get(WorkflowRun, run_id)
    if run is None:
        return None, set()
    stages = list(
        session.scalars(
            select(WorkflowStage)
            .where(WorkflowStage.run_id == run.id)
            .order_by(WorkflowStage.created_at)
        )
    )
    cases = list(
        session.scalars(
            select(WorkflowCase).where(WorkflowCase.run_id == run.id).order_by(WorkflowCase.position)
        )
    )
    execution_ids = {case.execution_id for case in cases if case.execution_id}
    case_rows = []
    for case in cases:
        analysis = session.get(WorkflowResultAnalysis, case.analysis_id) if case.analysis_id else None
        reviews = list(
            session.scalars(
                select(WorkflowCaseReview).where(
                    WorkflowCaseReview.run_id == run.id,
                    WorkflowCaseReview.case_id == case.case_id,
                )
            )
        )
        latest_review = max(reviews, key=lambda item: item.round) if reviews else None
        record = session.get(ExecutionRecord, case.execution_id) if case.execution_id else None
        test_case = session.get(TestCase, case.case_id)
        title = (
            (test_case.title if test_case else None)
            or (record.test_case_title if record else None)
            or case.case_id
        )
        latest_issues = []
        if latest_review and latest_review.issues:
            try:
                parsed_issues = json.loads(latest_review.issues)
                if isinstance(parsed_issues, list):
                    latest_issues = parsed_issues
            except (TypeError, ValueError):
                latest_issues = []
        case_rows.append(
            {
                "title": title,
                "automationStatus": case.automation_status,
                "reviewStatus": case.review_status,
                "qualityScore": case.quality_score,
                "revisionCount": case.revision_count,
                "latestIssue": (
                    latest_issues[0].get("problem", "")
                    if latest_issues and isinstance(latest_issues[0], dict)
                    else ""
                ),
                "executionStatus": record.status if record else None,
                "failureCategory": analysis.failure_category if analysis else None,
                "failureReason": analysis.failure_reason if analysis else "",
                "evidence": json.loads(analysis.evidence) if analysis and analysis.evidence else [],
            }
        )
    summary = {}
    if run.summary:
        try:
            summary = json.loads(run.summary)
        except (TypeError, ValueError):
            summary = {}
    context = {
        "id": run.id,
        "status": run.status,
        "requirementVersion": run.requirement_version,
        "requirementName": run.requirement_name,
        "createdAt": run.created_at.strftime("%Y-%m-%d %H:%M:%S") if run.created_at else None,
        "stages": [
            {
                "label": WORKFLOW_STAGE_LABELS.get(stage.stage, stage.stage),
                "status": stage.status,
                "outputSummary": stage.output_summary,
                "errorMessage": stage.error_message,
                "durationMs": stage.duration_ms,
            }
            for stage in stages
        ],
        "cases": case_rows,
        "summary": summary,
    }
    return context, execution_ids


def build_report(
    session: Session,
    pid: str,
    batch_id: str | None = None,
    requirement_id: str | None = None,
    workflow_run_id: str | None = None,
) -> str:
    proj = session.get(Project, pid)
    stmt = select(ExecutionRecord).where(ExecutionRecord.project_id == pid)
    workflow = None
    if workflow_run_id:
        workflow, workflow_execution_ids = _workflow_context(session, workflow_run_id)
        stmt = stmt.where(ExecutionRecord.id.in_(workflow_execution_ids or ["__none__"]))
    if batch_id:
        stmt = stmt.where(ExecutionRecord.batch_id == batch_id)
    if requirement_id:
        from app.service.execution_service import backfill_execution_requirements

        backfill_execution_requirements(session, pid)
        meta_stmt = select(ExecutionRequirement.execution_id).where(
            ExecutionRequirement.project_id == pid
        )
        if requirement_id == "__none__":
            meta_stmt = meta_stmt.where(ExecutionRequirement.requirement_id.is_(None))
        else:
            meta_stmt = meta_stmt.where(ExecutionRequirement.requirement_id == requirement_id)
        meta_ids = list(session.scalars(meta_stmt))
        stmt = stmt.where(ExecutionRecord.id.in_(meta_ids or ["__none__"]))
    records = list(session.scalars(stmt.order_by(ExecutionRecord.start_time)))

    passed = sum(1 for r in records if r.status == "passed")
    failed = sum(1 for r in records if r.status == "failed")
    skipped = sum(1 for r in records if r.status == "skipped")
    total = len(records)
    avg_ms = round(sum(r.duration_ms for r in records) / total) if total else 0
    pass_rate = f"{round(passed / total * 100)}%" if total else "-"

    fail_categories = Counter(r.error_category for r in records if r.status == "failed").most_common()

    # 步骤（截图相对路径已在执行时转为 outputs/...）
    for r in records:
        r.steps = list(session.scalars(select(ExecutionStep).where(ExecutionStep.execution_id == r.id).order_by(ExecutionStep.step_index)))

    matrix = coverage_matrix(session, pid, requirement_id)

    from datetime import datetime

    return REPORT_TMPL.render(
        project_name=proj.name if proj else "项目",
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        pass_rate=pass_rate, total=total, passed=passed, failed=failed, skipped=skipped,
        avg_ms=avg_ms, fail_categories=fail_categories, records=records, matrix=matrix,
        workflow=workflow,
    )
