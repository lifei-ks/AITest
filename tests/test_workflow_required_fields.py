"""工作流必填字段校验：只检查用例所在页面的正向表单提交。"""
from __future__ import annotations

from app.service.workflow_service import _missing_required_fields


FACTS = {
    "pages": [
        {"route": "/orders", "forms": []},
        {
            "route": "/orders/new",
            "forms": [
                {
                    "fields": [
                        {"label": "收货人", "prop": "receiverName", "placeholder": "请输入收货人", "required": True},
                        {"label": "手机号", "prop": "phone", "placeholder": "请输入手机号", "required": True},
                    ]
                }
            ],
        },
    ]
}


def _steps(route: str, *extra: dict) -> list[dict]:
    return [
        {
            "type": "ui_action",
            "action": "打开页面",
            "target": "",
            "uiSelector": {"type": "route", "value": route},
        },
        *extra,
    ]


def test_list_page_case_does_not_require_form_fields():
    case = {
        "type": "positive",
        "structuredSteps": _steps(
            "/orders",
            {"type": "state_assert", "action": "断言列表", "expected": "「暂无数据」"},
        ),
    }
    assert _missing_required_fields(case, FACTS) == []


def test_positive_form_case_reports_missing_field():
    case = {
        "type": "positive",
        "structuredSteps": _steps(
            "/orders/new",
            {
                "type": "input",
                "action": "输入收货人",
                "target": "收货人",
                "uiSelector": {"type": "placeholder", "value": "请输入收货人"},
                "inputValue": "张三",
            },
            {
                "type": "ui_action",
                "action": "点击提交",
                "target": "提交",
                "uiSelector": {"type": "text", "value": "提交"},
            },
            {"type": "state_assert", "action": "断言", "expected": "「订单创建成功」"},
        ),
    }
    assert _missing_required_fields(case, FACTS) == ["手机号"]


def test_negative_case_is_not_checked():
    case = {
        "type": "negative",
        "structuredSteps": _steps(
            "/orders/new",
            {"type": "state_assert", "action": "断言", "expected": "「收货人不能为空」"},
        ),
    }
    assert _missing_required_fields(case, FACTS) == []
