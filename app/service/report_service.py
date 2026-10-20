"""HTML 报告：Jinja2 单文件自包含，截图相对路径引用。"""
from __future__ import annotations

import json
from collections import Counter

from jinja2 import Template
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.entity.models import ExecutionRecord, ExecutionRequirement, ExecutionStep, Project
from app.service.coverage_service import coverage_matrix

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


def build_report(
    session: Session,
    pid: str,
    batch_id: str | None = None,
    requirement_id: str | None = None,
) -> str:
    proj = session.get(Project, pid)
    stmt = select(ExecutionRecord).where(ExecutionRecord.project_id == pid)
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

    matrix = coverage_matrix(session, pid)

    from datetime import datetime

    return REPORT_TMPL.render(
        project_name=proj.name if proj else "项目",
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        pass_rate=pass_rate, total=total, passed=passed, failed=failed, skipped=skipped,
        avg_ms=avg_ms, fail_categories=fail_categories, records=records, matrix=matrix,
    )
