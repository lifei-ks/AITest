"""固定总数断言 → 运行时基线断言的单元测试。"""
from __future__ import annotations

import pytest

from app.agent.count_assertion import normalize_case_counts
from app.agent.execution_agent import _StepError, _resolve_variables


def _new_order_case():
    return {
        "id": "TC-test",
        "title": "新建订单成功后列表刷新",
        "preconditions": [],
        "expectedResults": [
            "提交合法订单后提示「订单创建成功」，跳转至 /orders/list，"
            "新订单出现在首行、状态为待支付，总数变为 5 条"
        ],
        "steps": [],
        "structuredSteps": [
            {
                "order": 1,
                "type": "ui_action",
                "action": "打开新建订单页面",
                "target": "新建订单",
                "uiSelector": {"type": "route", "value": "/orders/create"},
                "expected": "",
                "inputValue": "",
            },
            {
                "order": 2,
                "type": "input",
                "action": "输入收货人",
                "target": "收货人",
                "uiSelector": {"type": "placeholder", "value": "请输入收货人"},
                "expected": "",
                "inputValue": "张三",
            },
            {
                "order": 3,
                "type": "ui_action",
                "action": "点击提交按钮",
                "target": "提交订单",
                "uiSelector": {"type": "text", "value": "提交订单"},
                "expected": "",
                "inputValue": "",
            },
            {
                "order": 4,
                "type": "state_assert",
                "action": "校验创建成功提示",
                "target": "成功提示",
                "uiSelector": {"type": "text", "value": "「订单创建成功」"},
                "expected": "「订单创建成功」",
                "inputValue": "",
            },
            {
                "order": 5,
                "type": "state_assert",
                "action": "校验列表总数",
                "target": "总数",
                "uiSelector": {"type": "text", "value": "「共 5 条订单」"},
                "expected": "「共 5 条订单」",
                "inputValue": "",
            },
        ],
    }


def test_normalize_fixed_count_to_runtime_baseline():
    case = _new_order_case()
    result = normalize_case_counts(case, ["/orders/list", "/orders/create"])
    assert result["changed"] is True
    steps = case["structuredSteps"]
    assert steps[0]["type"] == "ui_action"
    assert steps[0]["uiSelector"]["value"] == "/orders/list"
    assert steps[1]["type"] == "capture"
    assert steps[1]["variable"] == "base_count"
    assert steps[1]["pattern"] == r"共\s*(\d+)\s*条订单"
    count_assert = next(s for s in steps if s["action"] == "校验列表总数")
    assert count_assert["expected"] == "「共 ${base_count+1} 条订单」"
    assert "固定" not in " ".join(case["expectedResults"])
    assert any("运行时基线" in item for item in case["preconditions"])


def test_normalize_drops_list_only_case_without_create_action():
    case = _new_order_case()
    case["structuredSteps"] = [
        step for step in case["structuredSteps"] if step["action"] not in ("点击提交按钮", "输入收货人", "打开新建订单页面")
    ]
    result = normalize_case_counts(case, ["/orders/list"])
    assert result["changed"] is False
    assert result["drop"] is True


def test_resolve_variables_and_missing_variable():
    assert _resolve_variables("共 ${base_count+1} 条订单", {"base_count": "7"}) == "共 8 条订单"
    assert _resolve_variables("「${base_count-1}」", {"base_count": "7"}) == "「6」"
    with pytest.raises(_StepError) as exc:
        _resolve_variables("共 ${missing+1} 条", {})
    assert "未捕获" in exc.value.message


def test_normalize_drops_ambiguous_list_routes():
    case = _new_order_case()
    case["expectedResults"] = ["提交订单后总数变为 5 条"]
    result = normalize_case_counts(case, ["/orders/list", "/admin/order-list"])
    assert result["changed"] is False
    assert result["drop"] is True
