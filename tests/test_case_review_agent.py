"""AI 用例质检/修复的解析与源码事实校验测试。"""
from __future__ import annotations

import pytest

from app.agent import case_review_agent
from app.service.case_review_service import _normalize_patch, _validate_case


def test_parse_review_downgrades_contradictory_ok():
    review = case_review_agent.parse_review({
        "verdict": "ok",
        "reason": "看起来没问题",
        "issues": [{
            "severity": "blocker",
            "field": "structuredSteps",
            "problem": "placeholder 不存在",
            "suggestion": "改用真实 placeholder",
            "evidence": "源码字段 phone",
        }],
    })
    assert review["verdict"] == "suspect"
    assert review["issues"][0]["severity"] == "blocker"


def test_parse_review_adds_issue_when_verdict_is_fail_without_detail():
    review = case_review_agent.parse_review({"verdict": "fail", "reason": "无法执行"})
    assert review["verdict"] == "fail"
    assert review["issues"]


def test_normalize_patch_keeps_only_supported_fields():
    patch = _normalize_patch({
        "title": " 修复后的标题 ",
        "type": "negative",
        "expectedResults": ["「创建成功」"],
        "testData": {"phone": "123"},
        "structuredSteps": [{
            "type": "state_assert",
            "action": "验证",
            "expected": "「创建成功」",
        }],
    })
    assert patch["title"] == "修复后的标题"
    assert patch["type"] == "negative"
    assert patch["expected_results"] == ["「创建成功」"]
    assert patch["test_data"] == {"phone": "123"}
    assert patch["structured_steps"][0]["order"] == 1


def test_validate_case_accepts_source_backed_case():
    frontend = {
        "pages": [{
            "route": "/orders/create",
            "forms": [{"fields": [
                {"label": "手机号", "prop": "phone", "placeholder": "请输入手机号"},
            ]}],
        }],
        "selectors": [{"type": "text", "value": "提交订单"}],
        "uiTexts": ["创建成功"],
    }
    case = {
        "expectedResults": ["「创建成功」"],
        "structuredSteps": [
            {"type": "ui_action", "uiSelector": {"type": "route", "value": "/orders/create"}},
            {"type": "input", "uiSelector": {"type": "placeholder", "value": "请输入手机号"}, "inputValue": "123"},
            {"type": "ui_action", "uiSelector": {"type": "text", "value": "提交订单"}},
            {"type": "state_assert", "expected": "「创建成功」"},
        ],
    }
    assert _validate_case(case, frontend, "创建订单主流程", None) == []


def test_validate_case_rejects_invented_selector_and_anchor():
    frontend = {
        "pages": [{"route": "/orders/create", "forms": [{"fields": []}]}],
        "selectors": [],
        "uiTexts": [],
    }
    case = {
        "expectedResults": ["「订单已创建成功」"],
        "structuredSteps": [
            {"type": "ui_action", "uiSelector": {"type": "route", "value": "/orders/new"}},
            {"type": "input", "uiSelector": {"type": "placeholder", "value": "请输入手机"}, "inputValue": "123"},
            {"type": "state_assert", "expected": "「订单已创建成功」"},
        ],
    }
    errors = _validate_case(case, frontend, "", None)
    assert any("路由不存在" in item for item in errors)
    assert any("placeholder 不在源码事实中" in item for item in errors)
    assert any("断言锚点不在源码/需求事实中" in item for item in errors)


def test_validate_case_requires_anchor_on_every_assert_step():
    frontend = {
        "pages": [{"route": "/orders/create", "forms": [{"fields": []}]}],
        "selectors": [{"type": "text", "value": "提交订单"}],
        "uiTexts": ["创建成功"],
    }
    case = {
        "expectedResults": ["「创建成功」"],
        "structuredSteps": [
            {"type": "ui_action", "uiSelector": {"type": "route", "value": "/orders/create"}},
            {"type": "ui_action", "uiSelector": {"type": "text", "value": "提交订单"}},
            {"type": "state_assert", "expected": "「创建成功」"},
            {"type": "state_assert", "expected": "页面正常"},
        ],
    }
    errors = _validate_case(case, frontend, "", None)
    assert any("缺少「」文案锚点" in item for item in errors)


def test_validate_case_rejects_invented_test_data_field():
    frontend = {
        "pages": [{
            "route": "/orders/create",
            "forms": [{"fields": [{"label": "手机号", "prop": "phone"}]}],
        }],
        "uiTexts": ["创建成功"],
    }
    case = {
        "expectedResults": ["「创建成功」"],
        "testData": {"unknownField": "value"},
        "structuredSteps": [
            {"type": "ui_action", "uiSelector": {"type": "route", "value": "/orders/create"}},
            {"type": "state_assert", "expected": "「创建成功」"},
        ],
    }
    errors = _validate_case(case, frontend, "", None)
    assert any("testData 字段不在源码/需求事实中" in item for item in errors)


def test_validate_case_ignores_state_assert_selector_value():
    """state_assert 由 expected 锚点断言，uiSelector.value 不参与执行，不应误报。"""
    frontend = {
        "pages": [{"route": "/orders/create", "forms": [{"fields": []}]}],
        "selectors": [{"type": "text", "value": "提交订单"}],
        "uiTexts": ["创建成功"],
    }
    case = {
        "expectedResults": ["「创建成功」"],
        "structuredSteps": [
            {"type": "ui_action", "uiSelector": {"type": "route", "value": "/orders/create"}},
            {"type": "ui_action", "uiSelector": {"type": "text", "value": "提交订单"}},
            {
                "type": "state_assert",
                "uiSelector": {"type": "text", "value": "「创建成功」"},
                "expected": "「创建成功」",
            },
        ],
    }
    assert _validate_case(case, frontend, "", None) == []


def test_normalize_patch_rejects_invalid_type():
    with pytest.raises(Exception):
        _normalize_patch({"type": "api_call"})
