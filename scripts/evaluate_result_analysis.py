"""实验 B：结果分析 Agent 与基线规则/人工标签的失败分类对比。

样本 JSONL 格式（每行一个对象）：
  {"id":"s1","evidence":{...},"gold_label":"product_defect","human_label":"product_defect","no_evidence":false}

要求：
- gold_label 只用于打分，脚本会从传给 Agent 的 evidence 中剔除所有标签字段，防止泄漏。
- 没有样本时输出 samples=0，不会伪造 accuracy/F1。
- rule 模式不调用模型；llm 模式使用当前 .env 配置，模型不可用时 Agent 会降级 rule，
  输出中的 sources 会如实显示实际来源。
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

LABEL_KEYS = ("gold_label", "gold", "human_label", "label")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="实验 B：失败分类评测")
    parser.add_argument("--samples", required=True, help="带 gold_label 的 JSONL")
    parser.add_argument("--output", default="evaluation_b.json")
    parser.add_argument("--mode", default="rule", choices=["rule", "llm"])
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--database-url", default="", help="可选；分析 Agent 不依赖数据库")
    return parser.parse_args()


def sanitize_evidence(evidence: dict) -> dict:
    """深拷贝并移除标签字段，避免评测标签泄漏给 Agent。"""
    clean = json.loads(json.dumps(evidence, ensure_ascii=False))
    for key in LABEL_KEYS:
        clean.pop(key, None)
    return clean


def is_no_evidence(evidence: dict) -> bool:
    return not (evidence.get("steps") or evidence.get("visionReview"))


def main() -> int:
    args = parse_args()
    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url

    from app.agent import result_analysis_agent
    from app.evaluation.metrics import (
        category_distribution,
        classification_metrics,
        human_agreement,
        no_evidence_conclusion_rate,
        unknown_detection_rate,
    )
    from app.llm.client import LlmClient
    from app.config import get_settings

    samples: list[dict] = []
    for line in Path(args.samples).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        item = json.loads(line)
        if not isinstance(item.get("evidence"), dict):
            raise SystemExit(f"样本 {item.get('id')} 缺少 evidence 对象")
        if not item.get("gold_label"):
            raise SystemExit(f"样本 {item.get('id')} 缺少 gold_label")
        samples.append(item)
    if args.limit > 0:
        samples = samples[: args.limit]

    client = None
    if args.mode == "llm":
        settings = get_settings()
        client = LlmClient(settings.llm_api_key, settings.llm_base_url, settings.llm_model)

    records: list[dict] = []
    latencies: list[int] = []
    for item in samples:
        evidence = sanitize_evidence(item["evidence"])
        started = time.time()
        result = result_analysis_agent.analyze(client, evidence)
        latency = int((time.time() - started) * 1000)
        latencies.append(latency)
        records.append(
            {
                "id": item.get("id"),
                "gold": item["gold_label"],
                "pred": result["failure_category"],
                "human": item.get("human_label"),
                "no_evidence": bool(item.get("no_evidence", is_no_evidence(evidence))),
                "source": result.get("source"),
                "confidence": result.get("confidence"),
                "needsHumanReview": result.get("needs_human_review"),
                "latencyMs": latency,
            }
        )

    pairs = [(record["gold"], record["pred"]) for record in records]
    labels = sorted({record["gold"] for record in records} | {record["pred"] for record in records})
    metrics = classification_metrics(pairs, labels)
    output = {
        "experiment": "B",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "mode": args.mode,
        "samples": len(records),
        "metrics": metrics,
        "unknownDetectionRate": unknown_detection_rate(pairs),
        "noEvidenceConclusionRate": no_evidence_conclusion_rate(records),
        "humanAgreement": human_agreement(records),
        "latencyMs": {
            "total": sum(latencies) if latencies else None,
            "avg": round(sum(latencies) / len(latencies), 1) if latencies else None,
        },
        "sources": category_distribution(record["source"] for record in records),
        "predictions": category_distribution(record["pred"] for record in records),
        "perSample": records,
        "notes": [
            "gold_label 未传入 Agent；evidence 已深拷贝并删除标签字段。",
            "unknownDetectionRate = 真值 unknown 中被预测为 unknown 的比例；无 unknown 样本时为 null。",
            "noEvidenceConclusionRate = 无步骤且无截图审核的样本中，被给出非 unknown/none 结论的比例，越低越保守。",
            "humanAgreement = 有 human_label 的样本中 pred==human 的比例；没有人工标签时为 null。",
            "macroF1 仅对有 gold 样本的类别取平均。",
            "llm 模式下如模型不可用，sources 会显示 rule，说明发生了降级。",
        ],
    }
    Path(args.output).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入 {args.output}（样本 {len(records)}）")
    if records:
        print(
            f"accuracy={metrics['accuracy']} macroF1={metrics['macroF1']} "
            f"unknown={output['unknownDetectionRate']} noEvidenceOverclaim={output['noEvidenceConclusionRate']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
