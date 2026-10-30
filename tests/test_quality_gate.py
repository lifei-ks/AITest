"""质量闸测试：硬门槛 → 分项评分 → 结构化场景去重。"""
from __future__ import annotations

from app.agent.quality_gate import (
    UiLanguageLinter,
    is_compound_expected,
    run_quality_gate,
    scenario_signature,
)
from app.agent.test_generator_agent import GeneratedCase


def _assert_step(order: int = 2, expected: str = "预期结果") -> dict:
    anchor = expected if expected.startswith("「") else f"「{expected}」"
    return {
        "order": order,
        "type": "state_assert",
        "action": f"验证{expected}",
        "target": "",
        "uiSelector": {"type": "text", "value": anchor},
        "expected": anchor,
        "inputValue": "",
    }


def _route_step(order: int = 1, route: str = "/x") -> dict:
    return {
        "order": order,
        "type": "ui_action",
        "action": "打开页面",
        "target": "页面",
        "uiSelector": {"type": "route", "value": route},
        "expected": "",
        "inputValue": "",
    }


def _plain_assert_step(order: int, expected: str) -> dict:
    """无引号锚点的断言，用于构造低分用例。"""
    return {
        "order": order,
        "type": "state_assert",
        "action": "验证结果",
        "target": "",
        "uiSelector": {"type": "text", "value": ""},
        "expected": expected,
        "inputValue": "",
    }


def _input_step(
    order: int,
    value: str,
    target: str = "收货人",
    placeholder: str = "请输入收货人",
) -> dict:
    return {
        "order": order,
        "type": "input",
        "action": f"输入{target}",
        "target": target,
        "uiSelector": {"type": "placeholder", "value": placeholder},
        "expected": "",
        "inputValue": value,
    }


def _case(
    title: str = "用例",
    expected: tuple[str, ...] = ("预期结果",),
    structured_steps: list[dict] | None = None,
    coverage_refs: dict | None = None,
) -> GeneratedCase:
    steps = structured_steps
    if steps is None:
        steps = [_route_step(), _assert_step(2, expected[0] if expected else "预期结果")]
    return GeneratedCase(
        title=title,
        module="订单",
        type="positive",
        priority="P0",
        expected_results=list(expected),
        structured_steps=steps,
        coverage_refs=coverage_refs or {"endpointIds": [], "transitionIds": [], "ruleIds": []},
    )


def test_drop_no_expected_results():
    gate = run_quality_gate([_case(expected=())])
    assert gate.dropped == 1
    assert gate.dropReasons["no_expected"] == 1


def test_drop_no_steps():
    gate = run_quality_gate([_case(structured_steps=[])])
    assert gate.dropped == 1
    assert gate.dropReasons["no_steps"] == 1


def test_drop_no_assertion():
    gate = run_quality_gate([_case(structured_steps=[_route_step()])])
    assert gate.dropped == 1
    assert gate.dropReasons["no_assertion"] == 1


def test_linter_flags_api_words():
    case = _case(
        structured_steps=[
            {
                "order": 1,
                "type": "ui_action",
                "action": "调用接口创建订单",
                "target": "",
                "uiSelector": {"type": "text", "value": "x"},
                "expected": "",
                "inputValue": "",
            },
            _assert_step(2),
        ]
    )
    assert any("接口化描述" in issue for issue in UiLanguageLinter().lint(case))


def test_linter_flags_missing_selector():
    case = _case(
        structured_steps=[
            {
                "order": 1,
                "type": "ui_action",
                "action": "操作订单",
                "target": "",
                "uiSelector": {},
                "expected": "",
                "inputValue": "",
            },
            _assert_step(2),
        ]
    )
    assert any("缺少 uiSelector" in issue for issue in UiLanguageLinter().lint(case))


def test_missing_selector_penalized_once():
    """缺选择器只在选择器稳定性维度扣 8 分，lint 只做提示。"""
    case = _case(
        structured_steps=[
            {
                "order": 1,
                "type": "ui_action",
                "action": "操作订单",
                "target": "",
                "uiSelector": {},
                "expected": "",
                "inputValue": "",
            },
            _assert_step(2, "订单创建成功"),
        ],
        coverage_refs={"endpointIds": ["e1"]},
    )
    gate = run_quality_gate([case])
    assert len(gate.kept) == 1
    assert case.quality_score == 85
    assert any("缺少 uiSelector" in issue for issue in case.lint_issues)


def test_warning_does_not_affect_score():
    clean = _case(title="干净用例", expected=("订单创建成功",))
    warned = _case(
        title="接口化描述用例",
        expected=("订单创建成功",),
        structured_steps=[
            {
                "order": 1,
                "type": "ui_action",
                "action": "调用接口创建订单",
                "target": "",
                "uiSelector": {"type": "route", "value": "/x"},
                "expected": "",
                "inputValue": "",
            },
            _assert_step(2, "订单创建成功"),
        ],
        coverage_refs={"endpointIds": ["e1"]},
    )
    clean.coverage_refs = {"endpointIds": ["e1"]}
    gate = run_quality_gate([clean, warned])
    assert clean.quality_score == warned.quality_score
    assert any("接口化描述" in issue for issue in warned.lint_issues)


def test_low_score_kept_for_review():
    """低分不再静默丢弃，保留为待复核。"""
    steps = [
        {
            "order": i,
            "type": "ui_action",
            "action": f"操作{i}",
            "target": "",
            "uiSelector": {},
            "expected": "",
            "inputValue": "",
        }
        for i in range(1, 8)
    ]
    steps.append(_plain_assert_step(8, "共 5 条"))
    case = _case(expected=("共 5 条",), structured_steps=steps)
    gate = run_quality_gate([case])
    assert len(gate.kept) == 1
    assert gate.needs_review == 1
    assert gate.dropReasons["low_score"] == 0
    assert any("质量分低于 40" in issue for issue in case.lint_issues)


def test_low_score_can_still_be_dropped_in_legacy_mode():
    steps = [
        {
            "order": i,
            "type": "ui_action",
            "action": f"操作{i}",
            "target": "",
            "uiSelector": {},
            "expected": "",
            "inputValue": "",
        }
        for i in range(1, 8)
    ]
    steps.append(_plain_assert_step(8, "共 5 条"))
    gate = run_quality_gate(
        [_case(expected=("共 5 条",), structured_steps=steps)],
        drop_low_score=True,
    )
    assert gate.dropped == 1
    assert gate.dropReasons["low_score"] == 1


def test_dedup_exact_same_case():
    c1 = _case(title="新建订单：收货人为空校验失败")
    c2 = _case(title="新建订单：收货人为空校验失败")
    gate = run_quality_gate([c1, c2])
    assert len(gate.kept) == 1
    assert gate.dropReasons["duplicate"] == 1


def test_different_test_points_not_deduped():
    c1 = _case(title="同一场景")
    c2 = _case(title="同一场景")
    c1.test_point_id = "tp-a"
    c2.test_point_id = "tp-b"
    gate = run_quality_gate([c1, c2])
    assert len(gate.kept) == 2


def test_compound_expected_detection():
    assert is_compound_expected(
        "20字符可创建并提示「订单创建成功」；21字符提示「收货人不能超过20个字符」"
    )
    assert is_compound_expected("「20字符可创建并提示「订单创建成功」」")
    assert is_compound_expected("提交成功；返回订单列表")
    assert not is_compound_expected("「订单创建成功」")
    assert not is_compound_expected("收货人不能超过20个字符")


def test_drop_compound_expected():
    case = _case(
        expected=("20字符可创建并提示「订单创建成功」；21字符提示「收货人不能超过20个字符」",)
    )
    gate = run_quality_gate([case])
    assert gate.dropped == 1
    assert gate.dropReasons["compound_expected"] == 1


def test_split_boundary_cases_not_deduped():
    """同一测试点拆出的合法/越界用例，预期不同不能去重。"""
    c1 = _case(title="收货人长度边界（20字符合法）", expected=("订单创建成功",))
    c2 = _case(title="收货人长度边界（21字符超长）", expected=("收货人不能超过20个字符",))
    c1.test_point_id = c2.test_point_id = "tp-boundary"
    gate = run_quality_gate([c1, c2])
    assert len(gate.kept) == 2
    assert gate.dropReasons["duplicate"] == 0


def test_boundary_20_vs_21_not_deduped_without_test_point():
    """项目级生成（无 test_point_id）也不能把 20/21 字符边界用例合并。"""
    c1 = _case(
        title="收货人边界校验",
        expected=("订单创建成功",),
        structured_steps=[_route_step(1, "/orders/new"), _input_step(2, "a" * 20), _assert_step(3, "订单创建成功")],
    )
    c2 = _case(
        title="收货人边界校验",
        expected=("收货人不能超过20个字符",),
        structured_steps=[_route_step(1, "/orders/new"), _input_step(2, "a" * 21), _assert_step(3, "收货人不能超过20个字符")],
    )
    gate = run_quality_gate([c1, c2])
    assert len(gate.kept) == 2
    assert gate.dropReasons["duplicate"] == 0


def test_near_duplicate_marked_warning_not_dropped():
    c1 = _case(
        title="收货人边界校验",
        expected=("订单创建成功",),
        structured_steps=[_route_step(1, "/orders/new"), _input_step(2, "a" * 20), _assert_step(3, "订单创建成功")],
    )
    c2 = _case(
        title="收货人边界校验",
        expected=("订单创建成功",),
        structured_steps=[_route_step(1, "/orders/new"), _input_step(2, "a" * 21), _assert_step(3, "订单创建成功")],
    )
    gate = run_quality_gate([c1, c2])
    assert len(gate.kept) == 2
    assert gate.near_duplicate == 1
    assert any("近似重复" in issue for issue in c2.lint_issues + c1.lint_issues)


def test_existing_signature_skips_case():
    case = _case(title="跨批次已有用例")
    gate = run_quality_gate([case], existing_signatures={scenario_signature(case)})
    assert gate.dropped == 1
    assert gate.dropReasons["duplicate"] == 1


def test_drop_multiple_inputs_for_same_field():
    steps = [
        _route_step(1, "/orders/create"),
        _input_step(2, "合" * 20),
        _input_step(3, "超" * 21),
        _assert_step(4, "收货人不能超过20个字符"),
    ]
    case = _case(
        expected=("收货人不能超过20个字符",),
        structured_steps=steps,
    )
    gate = run_quality_gate([case])
    assert gate.dropped == 1
    assert gate.dropReasons["multiple_inputs"] == 1


def test_multiple_inputs_different_fields_allowed():
    steps = [
        _input_step(1, "13800138000", "手机号", "请输入手机号"),
        _input_step(2, "北京市朝阳区测试路1号", "收货地址", "请输入收货地址"),
        _assert_step(3, "订单创建成功"),
    ]
    gate = run_quality_gate([_case(structured_steps=steps, expected=("订单创建成功",))])
    assert len(gate.kept) == 1
