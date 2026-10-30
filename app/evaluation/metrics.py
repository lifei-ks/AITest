"""评测指标（纯函数，不访问数据库/模型，便于单测与复算）。

实验 A：用例质量指标（必需字段完整率、测试点覆盖率、重复率、可验证预期率、
关键场景遗漏率、确定性质量分、人工质量分）。所有比率在无分母时返回 None，
调用方不得把 None 当作 0 或伪造提升。

实验 B：失败分类指标（accuracy、macro-F1、逐类 P/R/F1、unknown 检出率、
无证据结论率、人工一致率）。
"""
from __future__ import annotations

import re
from collections import Counter

from app.agent.quality_gate import is_near_duplicate, scenario_signature

QUOTE_ANCHOR_RE = re.compile(r"[「『“\"]([^」』”\"]+)[」』”\"]")
VAGUE_ASSERT_WORDS = ("显示正常", "符合预期", "页面正常", "正常显示", "没问题", "功能正常")


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _route_of(case: dict) -> str:
    for step in case.get("structuredSteps") or []:
        selector = step.get("uiSelector") or {}
        if selector.get("type") == "route" and selector.get("value"):
            return str(selector["value"])
        if step.get("type") == "ui_action" and selector.get("type") == "route":
            return str(selector.get("value") or "")
    return ""


def _input_corpus(case: dict) -> str:
    parts: list[str] = []
    for step in case.get("structuredSteps") or []:
        if step.get("type") != "input":
            continue
        selector = (step.get("uiSelector") or {}).get("value") or ""
        parts.append(f"{selector} {step.get('target') or ''} {step.get('action') or ''}")
    return " ".join(parts)


def required_field_completeness(cases: list[dict], required_by_route: dict[str, list[list[str]]]) -> float | None:
    """每条用例对“当前页面所有必填字段”的填写比例，再取平均。

    required_by_route: {route: [[label, prop, placeholder], ...]]
    字段任一同义词出现在 input 步骤的 selector/target/action 中即视为已填。
    没有可判定用例时返回 None；页面无必填字段的用例记为 1.0 并在结果中注明分母。
    """
    scores: list[float] = []
    for case in cases:
        required = required_by_route.get(_route_of(case)) or []
        if not required:
            scores.append(1.0)
            continue
        corpus = _input_corpus(case)
        filled = sum(1 for tokens in required if any(token and str(token) in corpus for token in tokens))
        scores.append(filled / len(required))
    return _mean(scores)


def required_field_completeness_count(cases: list[dict], required_by_route: dict[str, list[list[str]]]) -> dict:
    """返回分子/分母，便于审计完整率不是凭空算出的。"""
    filled_total = 0
    required_total = 0
    for case in cases:
        required = required_by_route.get(_route_of(case)) or []
        if not required:
            continue
        required_total += len(required)
        corpus = _input_corpus(case)
        filled_total += sum(1 for tokens in required if any(token and str(token) in corpus for token in tokens))
    return {"filled": filled_total, "required": required_total}


def point_coverage(cases: list[dict], point_ids: list[str]) -> float | None:
    if not point_ids:
        return None
    covered: set[str] = set()
    wanted = set(point_ids)
    for case in cases:
        tp = case.get("testPointId")
        if tp in wanted:
            covered.add(str(tp))
        refs = (case.get("coverageRefs") or {}).get("testPointIds") or []
        covered.update(str(item) for item in refs if item in wanted)
    return round(len(covered) / len(wanted), 4)


def duplicate_rate(cases: list[dict]) -> float | None:
    """重复用例率 = (用例数 - 去重后场景数) / 用例数。

    与质量闸使用同一结构化场景签名，避免指标与闸门口径不一致。
    """
    if not cases:
        return None
    seen: set[str] = set()
    duplicates = 0
    for case in cases:
        signature = scenario_signature(case)
        if signature in seen:
            duplicates += 1
        else:
            seen.add(signature)
    return round(duplicates / len(cases), 4)


def near_duplicate_rate(cases: list[dict]) -> float | None:
    """近似重复用例率：同路由、同步骤类型序列且相似度 > 0.98 的用例对占比。"""
    if len(cases) < 2:
        return None
    pairs = 0
    total = 0
    for i in range(len(cases)):
        signature_i = scenario_signature(cases[i])
        for j in range(i + 1, len(cases)):
            if signature_i == scenario_signature(cases[j]):
                continue
            total += 1
            if is_near_duplicate(cases[i], cases[j]):
                pairs += 1
    return round(pairs / total, 4) if total else None


def _anchors(case: dict) -> list[str]:
    texts = list(case.get("expectedResults") or [])
    for step in case.get("structuredSteps") or []:
        if step.get("type") == "state_assert":
            texts.append(str(step.get("expected") or ""))
    anchors: list[str] = []
    for text in texts:
        anchors.extend(QUOTE_ANCHOR_RE.findall(str(text)))
    # expectedResults 与 state_assert 常写同一个锚点，按去重后的可见文案计数
    return list(dict.fromkeys(item.strip() for item in anchors if item.strip()))


def verifiable_expectation_rate(cases: list[dict]) -> float | None:
    if not cases:
        return None
    good = 0
    for case in cases:
        texts = " ".join([*(case.get("expectedResults") or []), *(
            str(step.get("expected") or "") for step in (case.get("structuredSteps") or []) if step.get("type") == "state_assert"
        )])
        anchors = _anchors(case)
        if len(anchors) == 1 and not any(word in texts for word in VAGUE_ASSERT_WORDS):
            good += 1
    return round(good / len(cases), 4)


def key_scenario_omission(cases: list[dict], points: list[dict]) -> dict:
    """关键场景（negative/boundary/state）中没有任何用例覆盖的比例。"""
    key_points = [point for point in points if point.get("category") in ("negative", "boundary", "state")]
    if not key_points:
        return {"rate": None, "missing": [], "total": 0}
    covered: set[str] = set()
    for case in cases:
        tp = case.get("testPointId")
        if tp:
            covered.add(str(tp))
        covered.update(str(item) for item in (case.get("coverageRefs") or {}).get("testPointIds") or [])
    missing = [point.get("title") or point.get("id") for point in key_points if str(point.get("id")) not in covered]
    return {
        "rate": round(len(missing) / len(key_points), 4),
        "missing": missing,
        "total": len(key_points),
    }


def quality_score_summary(cases: list[dict]) -> dict:
    scores = [case.get("qualityScore") for case in cases if isinstance(case.get("qualityScore"), (int, float))]
    return {
        "mean": _mean([float(score) for score in scores]),
        "min": min(scores) if scores else None,
        "max": max(scores) if scores else None,
        "count": len(scores),
    }


def human_quality_score(cases: list[dict], scores_by_case: dict[str, float] | None) -> float | None:
    if not scores_by_case:
        return None
    values = [
        float(scores_by_case[case["id"]])
        for case in cases
        if case.get("id") in scores_by_case
    ]
    return _mean(values)


def evaluate_case_set(
    cases: list[dict],
    points: list[dict],
    required_by_route: dict[str, list[list[str]]],
    human_scores: dict[str, float] | None = None,
) -> dict:
    omission = key_scenario_omission(cases, points)
    # 反例/边界用例会故意留空被测字段，必填完整率只对正向用例有意义。
    positive_cases = [case for case in cases if str(case.get("type") or "").lower() == "positive"]
    required_cases = positive_cases or cases
    return {
        "caseCount": len(cases),
        "testPointCount": len(points),
        "requiredFieldCompleteness": required_field_completeness(required_cases, required_by_route),
        "requiredFieldDetail": required_field_completeness_count(required_cases, required_by_route),
        "requiredFieldCases": len(required_cases),
        "requiredFieldScope": "positive_cases" if positive_cases else "all_cases",
        "pointCoverage": point_coverage(cases, [str(point["id"]) for point in points]),
        "duplicateRate": duplicate_rate(cases),
        "nearDuplicateRate": near_duplicate_rate(cases),
        "verifiableExpectationRate": verifiable_expectation_rate(cases),
        "keyScenarioOmissionRate": omission["rate"],
        "keyScenarioMissing": omission["missing"],
        "qualityScore": quality_score_summary(cases),
        "humanQualityScore": human_quality_score(cases, human_scores),
    }


def compare_metrics(baseline: dict, proposed: dict, lower_is_better: set[str] | None = None) -> dict:
    """逐指标给出绝对值差和百分比；baseline 为 0/None 时百分比为 None。"""
    lower_is_better = lower_is_better or {
        "duplicateRate",
        "nearDuplicateRate",
        "keyScenarioOmissionRate",
    }
    out: dict[str, dict] = {}
    for key, base in baseline.items():
        if not isinstance(base, (int, float)) or isinstance(base, bool):
            continue
        current = proposed.get(key)
        if not isinstance(current, (int, float)) or isinstance(current, bool):
            continue
        diff = round(current - base, 4)
        percent = round(diff / base * 100, 2) if base else None
        out[key] = {
            "baseline": base,
            "proposed": current,
            "diff": diff,
            "percent": percent,
            "direction": "lower_is_better" if key in lower_is_better else "higher_is_better",
        }
    return out


# ---------- 实验 B：失败分类 ----------

def classification_metrics(pairs: list[tuple[str, str]], labels: list[str] | None = None) -> dict:
    """pairs = [(gold, pred)]；逐类 P/R/F1 + accuracy + macro-F1（仅统计有 gold 样本的类）。"""
    if not pairs:
        return {
            "samples": 0,
            "accuracy": None,
            "macroF1": None,
            "perClass": {},
            "labels": labels or [],
        }
    labels = labels or sorted({gold for gold, _ in pairs} | {pred for _, pred in pairs})
    per_class: dict[str, dict] = {}
    for label in labels:
        tp = sum(1 for gold, pred in pairs if gold == label and pred == label)
        fp = sum(1 for gold, pred in pairs if gold != label and pred == label)
        fn = sum(1 for gold, pred in pairs if gold == label and pred != label)
        support = sum(1 for gold, _ in pairs if gold == label)
        precision = tp / (tp + fp) if tp + fp else None
        recall = tp / (tp + fn) if tp + fn else None
        f1 = (2 * precision * recall / (precision + recall)) if precision is not None and recall is not None and (precision + recall) else None
        per_class[label] = {
            "precision": round(precision, 4) if precision is not None else None,
            "recall": round(recall, 4) if recall is not None else None,
            "f1": round(f1, 4) if f1 is not None else None,
            "support": support,
            "predicted": tp + fp,
        }
    accuracy = sum(1 for gold, pred in pairs if gold == pred) / len(pairs)
    macro_classes = [item["f1"] for item in per_class.values() if item["support"] > 0 and item["f1"] is not None]
    return {
        "samples": len(pairs),
        "accuracy": round(accuracy, 4),
        "macroF1": round(sum(macro_classes) / len(macro_classes), 4) if macro_classes else None,
        "perClass": per_class,
        "labels": labels,
    }


def unknown_detection_rate(pairs: list[tuple[str, str]], unknown_label: str = "unknown") -> float | None:
    support = sum(1 for gold, _ in pairs if gold == unknown_label)
    if not support:
        return None
    hit = sum(1 for gold, pred in pairs if gold == unknown_label and pred == unknown_label)
    return round(hit / support, 4)


def no_evidence_conclusion_rate(records: list[dict]) -> float | None:
    """无证据样本中，给出确定分类（非 unknown/none）的比例；越低越保守。"""
    no_evidence = [item for item in records if item.get("no_evidence")]
    if not no_evidence:
        return None
    overclaim = sum(1 for item in no_evidence if item["pred"] not in ("unknown", "none"))
    return round(overclaim / len(no_evidence), 4)


def human_agreement(records: list[dict]) -> float | None:
    judged = [item for item in records if item.get("human")]
    if not judged:
        return None
    agree = sum(1 for item in judged if item["pred"] == item["human"])
    return round(agree / len(judged), 4)


def category_distribution(values) -> dict[str, int]:
    return dict(Counter(str(value) for value in values))
