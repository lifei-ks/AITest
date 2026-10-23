"""实验 A：同一需求下「仅生成」vs「生成 + 独立审核/修订」的用例质量对比。

用法（会改写生成用例，必须显式确认）：
  .venv/Scripts/python.exe scripts/evaluate_workflow.py \
      --project-id <pid> --requirement-id <rid> --mode rule \
      --output evaluation_a.json --allow-write

说明：
- 基线：直接调用现有 generation_service.generate（无工作流审核）。
- 方案：复用同一批用例，运行工作流审核/修订（不执行），只统计审核通过的用例。
- 指标由 app/evaluation/metrics.py 计算；没有真实数据时输出 null，不会补 0 或编造提升。
- 未捕获 token usage，因此 cost 固定为 null；llmCalls / durationMs 为真实计数。
- 同一需求顺序执行意味着基线和方案不是独立样本；严格实验应对照两份需求/数据库副本。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="实验 A：用例质量对比")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--requirement-id", required=True)
    parser.add_argument("--mode", default="rule", choices=["auto", "llm", "rule"])
    parser.add_argument("--max-revisions", type=int, default=2, choices=[0, 1, 2])
    parser.add_argument("--max-cases", type=int, default=20)
    parser.add_argument("--human-scores", default="", help="可选 JSON 文件：{caseId: 0~100}")
    parser.add_argument("--output", default="evaluation_a.json")
    parser.add_argument("--database-url", default="", help="实验专用数据库，建议指向副本")
    parser.add_argument("--offline", action="store_true", help="强制离线：不调用 LLM，审核走确定性规则")
    parser.add_argument("--allow-write", action="store_true", help="确认允许改写该需求下的生成用例")
    return parser.parse_args()


def _flatten(prefix: str, value: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, item in (value or {}).items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            out[name] = float(item)
        elif isinstance(item, dict):
            out.update(_flatten(name, item))
    return out


def _case_rows(session, project_id: str, requirement_id: str) -> list[dict]:
    from sqlalchemy import select

    from app.entity.models import RequirementCase, TestCase
    from app.service.generation_service import case_to_dict

    rows = session.execute(
        select(TestCase, RequirementCase)
        .join(RequirementCase, RequirementCase.test_case_id == TestCase.id)
        .where(RequirementCase.requirement_id == requirement_id, TestCase.project_id == project_id)
        .order_by(TestCase.priority.asc(), TestCase.project_seq.asc())
    ).all()
    out: list[dict] = []
    seen: set[str] = set()
    for tc, mapping in rows:
        if tc.id in seen:
            continue
        if tc.source in ("manual", "import"):
            continue
        seen.add(tc.id)
        case = case_to_dict(tc)
        case["testPointId"] = mapping.test_point_id
        out.append(case)
    return out


def _required_by_route(session, project_id: str) -> dict[str, list[list[str]]]:
    from app.service.generation_service import get_generation_context

    backend, frontend, _machines = get_generation_context(session, project_id)
    required_names = {
        constraint.field
        for entity in backend.entities
        for constraint in entity.fieldConstraints
        if constraint.rule in ("notblank", "notnull", "notempty")
    }
    out: dict[str, list[list[str]]] = {}
    for page in frontend.pages:
        tokens: list[list[str]] = []
        for form in page.forms:
            for field in form.fields:
                required = (
                    field.required
                    or field.prop in required_names
                    or field.label in required_names
                )
                if not required:
                    continue
                tokens.append(
                    [str(value) for value in (field.label, field.prop, field.placeholder) if value]
                )
        out[page.route] = tokens
    return out


def _point_dicts(session, requirement_id: str) -> list[dict]:
    from sqlalchemy import select

    from app.entity.models import TestPoint
    from app.service.requirement_service import test_point_to_dict

    return [
        test_point_to_dict(point)
        for point in session.scalars(
            select(TestPoint).where(TestPoint.requirement_id == requirement_id).order_by(TestPoint.seq)
        )
    ]


def main() -> int:
    args = parse_args()
    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url
    if not args.allow_write:
        print("拒绝执行：该脚本会改写需求下的生成用例。请先备份数据库，并加 --allow-write。", file=sys.stderr)
        return 2

    from app.database import SessionLocal
    from app.entity.models import Requirement
    from app.evaluation.metrics import compare_metrics, evaluate_case_set, quality_score_summary
    from app.llm.client import LlmClient
    from app.service import generation_service, requirement_service, workflow_service
    from app.workflow.schemas import WorkflowCreate

    run_mode = "rule" if args.offline else args.mode
    if args.offline:
        workflow_service._text_client = lambda: LlmClient("", "", "offline")

    with SessionLocal() as session:
        req = session.get(Requirement, args.requirement_id)
        if req is None or req.project_id != args.project_id:
            print("需求版本不存在或不属于该项目", file=sys.stderr)
            return 2
        points = _point_dicts(session, req.id)
        if not points:
            extracted = requirement_service.parse_points(session, req, run_mode)
            requirement_service.save_points(session, req, extracted)
            session.commit()
            points = _point_dicts(session, req.id)
        required_by_route = _required_by_route(session, args.project_id)

    human_scores = None
    if args.human_scores:
        human_scores = json.loads(Path(args.human_scores).read_text(encoding="utf-8"))

    calls = {"count": 0}
    original = LlmClient.chat_json

    def counted(self, *a, **kwargs):
        calls["count"] += 1
        return original(self, *a, **kwargs)

    LlmClient.chat_json = counted
    try:
        started = time.time()
        baseline_stats = generation_service.generate(
            args.project_id, run_mode, requirement_id=args.requirement_id
        )
        baseline_ms = int((time.time() - started) * 1000)
        baseline_calls = calls["count"]
        with SessionLocal() as session:
            baseline_cases = _case_rows(session, args.project_id, args.requirement_id)
        baseline_metrics = evaluate_case_set(
            baseline_cases, points, required_by_route, human_scores
        )
        baseline_metrics.update(
            {
                "llmCalls": baseline_calls,
                "durationMs": baseline_ms,
                "estimatedCost": None,
                "generation": baseline_stats,
            }
        )

        calls["count"] = 0
        started = time.time()
        with SessionLocal() as session:
            run = workflow_service.create_run(
                session,
                args.project_id,
                WorkflowCreate(
                    requirementId=args.requirement_id,
                    mode=run_mode,
                    autoExecute=False,
                    analyzeEnabled=False,
                    maxRevisions=args.max_revisions,
                    maxCases=args.max_cases,
                    reuseExistingCases=True,
                    reviewEnabled=True,
                ),
                run_async=False,
            )
        proposed_ms = int((time.time() - started) * 1000)
        proposed_calls = calls["count"]
        with SessionLocal() as session:
            detail = workflow_service.get_run_detail(session, run["id"])
            proposed_all: list[dict] = []
            for item in detail["cases"]:
                from app.entity.models import TestCase
                from app.service.generation_service import case_to_dict

                tc = session.get(TestCase, item["caseId"])
                if tc is None or tc.source in ("manual", "import"):
                    continue
                case = case_to_dict(tc)
                case["testPointId"] = item["testPointId"]
                case["reviewStatus"] = item["reviewStatus"]
                # qualityScore 保留质量闸分，保证与 baseline 同一把尺；
                # reviewQualityScore 才是工作流审核分，单独输出，不参与 baseline 对比。
                case["reviewQualityScore"] = item["qualityScore"]
                proposed_all.append(case)
        proposed_approved = [case for case in proposed_all if case.get("reviewStatus") == "pass"]
        proposed_metrics = evaluate_case_set(
            proposed_approved, points, required_by_route, human_scores
        )
        proposed_all_metrics = evaluate_case_set(
            proposed_all, points, required_by_route, human_scores
        )
        proposed_metrics.update(
            {
                "llmCalls": proposed_calls,
                "durationMs": proposed_ms,
                "estimatedCost": None,
                "runId": run["id"],
                "runStatus": run["status"],
                "approvedCases": len(proposed_approved),
                "allCases": len(proposed_all),
                "reviewQualityScore": quality_score_summary(
                    [{"qualityScore": case["reviewQualityScore"]} for case in proposed_approved]
                ),
                "allCasesMetrics": proposed_all_metrics,
            }
        )
    finally:
        LlmClient.chat_json = original

    baseline_flat = _flatten("", baseline_metrics)
    proposed_flat = _flatten("", proposed_metrics)
    result = {
        "experiment": "A",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "projectId": args.project_id,
        "requirementId": args.requirement_id,
        "mode": run_mode,
        "offline": args.offline,
        "definitions": {
            "requiredFieldCompleteness": "仅统计 positive 用例（反例/边界会故意留空被测字段）：已填写必填字段数 / 页面必填字段数，再取平均；无必填字段页面记 1.0。",
            "pointCoverage": "至少被一条用例引用的测试点数 / 测试点总数。",
            "duplicateRate": "(用例数 - 去重场景数) / 用例数；指纹=标题+全部步骤+预期。",
            "verifiableExpectationRate": "预期和断言合并后恰好 1 个「」锚点、且不含模糊词的比例。",
            "keyScenarioOmissionRate": "negative/boundary/state 测试点中无任何用例覆盖的比例。",
            "qualityScore": "现有质量闸分；baseline 与 proposed 使用同一把尺，可直接比较。",
            "reviewQualityScore": "工作流审核分（确定性校验 + AI 建议扣分），只出现在 proposed，不参与对比。",
            "humanQualityScore": "仅在提供 --human-scores 时计算；否则 null。",
            "llmCalls": "本次阶段真实调用 chat_json 的次数。",
            "estimatedCost": "未采集 token usage，固定 null，不估算。",
        },
        "baseline": baseline_metrics,
        "proposed": proposed_metrics,
        "diff": compare_metrics(baseline_flat, proposed_flat),
        "notes": [
            "基线与方案在同一需求上顺序执行，属于配对观察，不是独立随机样本。",
            "方案指标只统计 reviewStatus=pass 的用例；全部用例指标见 proposed.allCasesMetrics。",
            "cost 为 null：当前 LlmClient 不返回 token usage。",
        ],
    }
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入 {args.output}")
    print(
        "baseline 用例数={} 覆盖={} 完整率={}；proposed 通过用例数={} 覆盖={} 完整率={}".format(
            baseline_metrics["caseCount"],
            baseline_metrics["pointCoverage"],
            baseline_metrics["requiredFieldCompleteness"],
            proposed_metrics["caseCount"],
            proposed_metrics["pointCoverage"],
            proposed_metrics["requiredFieldCompleteness"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
