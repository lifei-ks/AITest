"""评测指标与标签隔离测试（不依赖真实模型/真实实验数据）。"""
from __future__ import annotations

from app.evaluation.metrics import (
    classification_metrics,
    compare_metrics,
    duplicate_rate,
    evaluate_case_set,
    human_agreement,
    key_scenario_omission,
    no_evidence_conclusion_rate,
    point_coverage,
    required_field_completeness,
    unknown_detection_rate,
    verifiable_expectation_rate,
)
from scripts.evaluate_result_analysis import is_no_evidence, sanitize_evidence


def _case(route: str, inputs: list[tuple[str, str]], expected: list[str], title: str = "用例"):
    steps = [
        {"type": "ui_action", "uiSelector": {"type": "route", "value": route}, "action": "打开页面"},
    ]
    steps.extend(
        {
            "type": "input",
            "uiSelector": {"type": "placeholder", "value": selector},
            "inputValue": value,
            "action": f"输入{selector}",
        }
        for selector, value in inputs
    )
    if expected:
        steps.append(
            {
                "type": "state_assert",
                "expected": expected[0],
                "uiSelector": {"type": "text", "value": expected[0]},
                "action": "验证结果",
            }
        )
    return {
        "id": title,
        "title": title,
        "structuredSteps": steps,
        "expectedResults": expected,
        "coverageRefs": {},
    }


def test_required_field_completeness_and_count():
    required = {
        "/orders/create": [["收货人", "receiverName"], ["手机号", "phone"], ["地址", "address"]],
        "/orders/list": [],
    }
    cases = [
        _case("/orders/create", [("请输入收货人", "张三"), ("请输入手机号", "13800138000")], ["「创建成功」"], "c1"),
        _case("/orders/list", [], ["「订单列表」"], "c2"),
    ]
    assert required_field_completeness(cases, required) == 0.8333
    assert evaluate_case_set(cases, [], required)["requiredFieldDetail"] == {"filled": 2, "required": 3}


def test_coverage_duplicate_and_verifiable_rate():
    point_a = "p1"
    point_b = "p2"
    cases = [
        {**_case("/x", [], ["「成功」"], "same"), "testPointId": point_a},
        {**_case("/x", [], ["「成功」"], "same"), "testPointId": point_a},
        {**_case("/x", [], ["「成功」", "「另一个结果」"], "multi"), "testPointId": point_b},
    ]
    assert point_coverage(cases, [point_a, point_b]) == 1.0
    assert point_coverage(cases, [point_a, "p3"]) == 0.5
    assert duplicate_rate(cases) == 0.3333
    assert verifiable_expectation_rate(cases) == 0.6667


def test_key_scenario_omission():
    points = [
        {"id": "p1", "title": "正常流程", "category": "functional"},
        {"id": "p2", "title": "必填校验", "category": "negative"},
        {"id": "p3", "title": "边界值", "category": "boundary"},
    ]
    cases = [{"testPointId": "p2"}]
    result = key_scenario_omission(cases, points)
    assert result["rate"] == 0.5
    assert result["missing"] == ["边界值"]
    assert result["total"] == 2


def test_compare_metrics_marks_direction_and_zero_baseline():
    baseline = {"duplicateRate": 0, "pointCoverage": 0.5}
    proposed = {"duplicateRate": 0.2, "pointCoverage": 0.75}
    diff = compare_metrics(baseline, proposed)
    assert diff["duplicateRate"]["percent"] is None
    assert diff["duplicateRate"]["direction"] == "lower_is_better"
    assert diff["pointCoverage"]["percent"] == 50.0


def test_classification_metrics_per_class_and_macro():
    pairs = [
        ("product_defect", "product_defect"),
        ("product_defect", "test_script_issue"),
        ("environment_issue", "environment_issue"),
        ("unknown", "unknown"),
    ]
    result = classification_metrics(pairs)
    assert result["accuracy"] == 0.75
    assert result["perClass"]["product_defect"]["recall"] == 0.5
    assert result["perClass"]["product_defect"]["precision"] == 1.0
    assert round(result["macroF1"], 4) == 0.8889
    assert unknown_detection_rate(pairs) == 1.0
    assert unknown_detection_rate([("product_defect", "product_defect")]) is None


def test_no_evidence_overclaim_and_human_agreement():
    records = [
        {"pred": "product_defect", "no_evidence": True, "human": "product_defect"},
        {"pred": "unknown", "no_evidence": True, "human": "unknown"},
        {"pred": "none", "no_evidence": False, "human": None},
    ]
    assert no_evidence_conclusion_rate(records) == 0.5
    assert human_agreement(records) == 1.0


def test_label_sanitization_and_no_evidence_detection():
    evidence = {
        "gold_label": "product_defect",
        "human_label": "product_defect",
        "steps": [],
        "visionReview": {},
    }
    clean = sanitize_evidence(evidence)
    assert "gold_label" not in clean and "human_label" not in clean
    assert is_no_evidence(clean) is True
    assert is_no_evidence({"steps": [{"index": 1}], "visionReview": {}}) is False
