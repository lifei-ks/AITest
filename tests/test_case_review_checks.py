"""独立审核 Agent 的逐步确定性检查测试。"""
from __future__ import annotations

from app.agent.case_review_checks import (
    dedup_issues,
    find_title_duplicates,
    merge_review,
    review_case_steps,
)


FACTS = {
    "requirement": {
        "version": "V1",
        "name": "订单主流程",
        "excerpt": "收货人必填，长度不超过20个字符，创建成功提示「订单创建成功」。",
    },
    "testPoint": {
        "id": "tp-1",
        "title": "新增订单",
        "description": "填写收货人提交",
        "expected": "「订单创建成功」",
        "fields": ["receiverName"],
        "pageRoute": "/orders/new",
    },
    "pages": [
        {"route": "/orders", "name": "订单列表", "forms": []},
        {
            "route": "/orders/new",
            "name": "新增订单",
            "forms": [
                {
                    "fields": [
                        {"label": "收货人", "prop": "receiverName", "placeholder": "请输入收货人", "required": True},
                        {"label": "手机号", "prop": "phone", "placeholder": "请输入手机号", "required": True},
                    ]
                }
            ],
        },
    ],
    "selectors": [{"type": "text", "value": "提交"}, {"type": "text", "value": "新增"}],
    "uiTexts": ["订单创建成功", "收货人不能为空", "收货人不能超过20个字符"],
    "endpoints": [{"method": "POST", "path": "/api/orders"}],
    "entities": [
        {
            "name": "Order",
            "fieldConstraints": [
                {"field": "receiverName", "rule": "size", "value": 20},
            ],
        }
    ],
    "businessRules": [],
    "enums": [],
    "stateMachines": [],
}


def _route(route: str = "/orders/new") -> dict:
    return {"type": "ui_action", "action": "打开页面", "uiSelector": {"type": "route", "value": route}}


def _input(value: str = "张三", target: str = "收货人", placeholder: str = "请输入收货人") -> dict:
    return {
        "type": "input",
        "action": f"输入{target}",
        "target": target,
        "uiSelector": {"type": "placeholder", "value": placeholder},
        "inputValue": value,
    }


def _click(text: str = "提交") -> dict:
    return {"type": "ui_action", "action": f"点击{text}", "target": text, "uiSelector": {"type": "text", "value": text}}


def _assert(expected: str = "「订单创建成功」") -> dict:
    return {"type": "state_assert", "action": "验证结果", "expected": expected}


def _case(steps: list[dict], expected: list[str] | None = None, case_type: str = "positive") -> dict:
    return {
        "id": "TC-1",
        "title": "新增订单",
        "type": case_type,
        "expectedResults": expected or ["订单创建成功"],
        "structuredSteps": steps,
        "testData": {},
        "coverageRefs": {"testPointIds": ["tp-1"]},
    }


def _codes(result: dict) -> set[str]:
    return {issue.get("code") for issue in result["issues"]}


def test_valid_positive_case_passes():
    case = _case([
        _route(),
        _input("张三"),
        _input("13800138000", "手机号", "请输入手机号"),
        _click(),
        _assert(),
    ])
    result = review_case_steps(case, FACTS)
    assert result["verdict"] == "pass"
    assert not result["blocking"]


def test_first_step_must_be_route():
    case = _case([_input("张三"), _assert()])
    result = review_case_steps(case, FACTS)
    assert result["verdict"] == "reject"
    assert "FIRST_STEP_ROUTE" in _codes(result)


def test_input_requires_value():
    case = _case([_route(), _input(""), _assert()])
    result = review_case_steps(case, FACTS)
    assert result["verdict"] == "reject"
    assert "INPUT_VALUE" in _codes(result)


def test_undefined_variable_is_blocking():
    case = _case([
        _route(),
        _assert("「共 ${missing+1} 条订单」"),
    ])
    result = review_case_steps(case, FACTS)
    assert result["verdict"] == "reject"
    assert "VARIABLE_UNDEFINED" in _codes(result)


def test_fixed_count_without_capture_is_warning():
    case = _case([
        _route("/orders"),
        _assert("「共 5 条订单」"),
    ], expected=["「共 5 条订单」"])
    result = review_case_steps(case, FACTS)
    assert result["verdict"] == "revise"
    assert "FIXED_COUNT_BASELINE" in _codes(result)


def test_negative_case_does_not_require_all_required_fields():
    case = _case([
        _route(),
        _input("张三"),
        _click(),
        _assert("「手机号不能为空」"),
    ], expected=["「手机号不能为空」"], case_type="negative")
    result = review_case_steps(case, FACTS)
    assert "REQUIRED_FIELD_MISSING" not in _codes(result)


def test_unknown_field_is_blocking():
    case = _case([_route(), _input("张三", "不存在字段", "不存在的placeholder"), _assert()])
    result = review_case_steps(case, FACTS)
    assert result["verdict"] == "reject"
    assert "FIELD_EXISTS" in _codes(result)


def test_merge_keeps_deterministic_rejection():
    case = _case([_input("张三"), _assert()])
    deterministic = review_case_steps(case, FACTS)
    merged = merge_review(deterministic, {"verdict": "ok", "issues": [], "reason": "AI 认为通过"})
    assert deterministic["verdict"] == "reject"
    assert merged["verdict"] == "reject"


def test_merge_adds_only_new_llm_issues_and_reduces_score():
    case = _case([_route(), _assert()])
    deterministic = review_case_steps(case, FACTS)
    merged = merge_review(
        deterministic,
        {
            "verdict": "suspect",
            "reason": "断言与测试点不完全一致",
            "issues": [
                {
                    "code": "SEMANTIC_COVERAGE",
                    "severity": "warning",
                    "problem": "没有验证列表中的新增订单",
                    "reason": "测试点要求列表出现新增记录",
                    "evidence": "testPoint.expected",
                    "suggestion": "补充列表行断言",
                }
            ],
        },
    )
    assert merged["verdict"] == "revise"
    assert merged["score"] < deterministic["score"]
    assert any(issue["code"] == "SEMANTIC_COVERAGE" for issue in merged["issues"])


def test_merge_does_not_duplicate_same_code():
    case = _case([_input("张三"), _assert()])
    deterministic = review_case_steps(case, FACTS)
    duplicate = {
        "code": "FIRST_STEP_ROUTE",
        "severity": "blocker",
        "problem": "第一步不是导航",
    }
    merged = merge_review(deterministic, {"verdict": "fail", "issues": [duplicate]})
    assert len([i for i in merged["issues"] if i.get("code") == "FIRST_STEP_ROUTE"]) == 1


def test_title_duplicate_marked_as_candidate():
    current = _case([_route(), _input("张三"), _assert()])
    peer = _case([_route(), _input("张三"), _assert()])
    current["id"] = "TC-1"
    peer["id"] = "TC-2"
    current["title"] = "新增订单-填写合法信息提交成功"
    peer["title"] = "新增订单-填写合法信息提交成功"

    issues = dedup_issues(current, [peer])
    assert any(issue["code"] == "NEAR_DUPLICATE" for issue in issues)
    candidates = find_title_duplicates(current, [peer])
    assert candidates[0]["verdict"] == "duplicate_candidate"


def test_20_vs_21_not_marked_as_duplicate():
    current = _case(
        [_route(), _input("a" * 20), _assert("「订单创建成功」")],
        expected=["订单创建成功"],
    )
    peer = _case(
        [_route(), _input("a" * 21), _assert("「收货人不能超过20个字符」")],
        expected=["收货人不能超过20个字符"],
    )
    current["id"] = "TC-1"
    peer["id"] = "TC-2"
    current["title"] = "新增订单-收货人20字符边界"
    peer["title"] = "新增订单-收货人21字符边界"

    issues = dedup_issues(current, [peer])
    assert not any(issue["code"] == "NEAR_DUPLICATE" for issue in issues)
    candidates = find_title_duplicates(current, [peer])
    assert candidates[0]["verdict"] == "different_scenario"


def test_similar_title_different_expected_marked_info():
    current = _case(
        [_route(), _input("张三"), _assert("「订单创建成功」")],
        expected=["订单创建成功"],
    )
    peer = _case(
        [_route(), _input("张三"), _assert("「收货人不能为空」")],
        expected=["收货人不能为空"],
    )
    current["id"] = "TC-1"
    peer["id"] = "TC-2"
    current["title"] = "新增订单-提交校验"
    peer["title"] = "新增订单-提交校验2"

    issues = dedup_issues(current, [peer])
    assert any(
        issue["code"] == "TITLE_SIMILAR" and issue["severity"] == "info"
        for issue in issues
    )
