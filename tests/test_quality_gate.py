"""V4 质量闸测试：三道闸（Linter → 打分 → 去重）独立验证。"""
from __future__ import annotations

from app.agent.quality_gate import UiLanguageLinter, is_compound_expected, run_quality_gate
from app.agent.test_generator_agent import GeneratedCase


def _case(title="用例", expected=("预期结果",), structured_steps=None, coverage_refs=None):
    steps = structured_steps or [
        {
            "order": 1,
            "type": "ui_action",
            "action": "打开页面",
            "target": "页面",
            "uiSelector": {"type": "route", "value": "/x"},
            "expected": "",
            "inputValue": "",
        }
    ]
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
    """硬门槛：无 expectedResults 直接丢弃。"""
    case = _case(expected=())
    gate = run_quality_gate([case])
    assert gate.dropped == 1
    assert gate.dropReasons["no_expected"] == 1


def test_linter_flags_api_words():
    """闸1：拦截接口化描述。"""
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
            }
        ]
    )
    issues = UiLanguageLinter().lint(case)
    assert any("接口化描述" in i for i in issues)


def test_linter_flags_missing_selector():
    """闸1：无 uiSelector 的抽象动作。"""
    case = _case(
        structured_steps=[
            {"order": 1, "type": "ui_action", "action": "操作订单", "target": "", "uiSelector": {}, "expected": "", "inputValue": ""}
        ]
    )
    issues = UiLanguageLinter().lint(case)
    assert any("缺少 uiSelector" in i for i in issues)


def test_score_low_dropped():
    """闸2：得分 <40 丢弃。"""
    # 无 uiSelector 的步骤多 → 扣分严重 → 低分丢弃
    steps = [
        {"order": i, "type": "ui_action", "action": f"操作{i}", "target": "", "uiSelector": {}, "expected": "", "inputValue": ""}
        for i in range(1, 8)
    ]
    case = _case(structured_steps=steps)
    gate = run_quality_gate([case])
    assert gate.dropReasons.get("low_score", 0) >= 1


def test_dedup_similar_cases():
    """闸3：标题+首步骤相似 >0.85 视为重复。"""
    c1 = _case(title="新建订单：收货人为空校验失败")
    c2 = _case(title="新建订单：收货人为空校验失败")
    gate = run_quality_gate([c1, c2])
    assert len(gate.kept) == 1
    assert gate.dropReasons["duplicate"] == 1


def test_compound_expected_detection():
    """一个预期里出现两个「」锚点 / 嵌套引号 / 分号并列结果 → 复合预期。"""
    assert is_compound_expected(
        "20字符可创建并提示「订单创建成功」；21字符提示「收货人不能超过20个字符」"
    )
    assert is_compound_expected("「20字符可创建并提示「订单创建成功」」")
    assert is_compound_expected("提交成功；返回订单列表")
    assert not is_compound_expected("「订单创建成功」")
    assert not is_compound_expected("收货人不能超过20个字符")


def test_drop_compound_expected():
    """闸0：复合预期无法被单次执行同时满足，直接丢弃并记录原因。"""
    case = _case(
        expected=("20字符可创建并提示「订单创建成功」；21字符提示「收货人不能超过20个字符」",)
    )
    gate = run_quality_gate([case])
    assert gate.dropped == 1
    assert gate.dropReasons["compound_expected"] == 1


def test_split_boundary_cases_not_deduped():
    """同一测试点拆出的合法/越界用例，预期不同不能按标题相似去重。"""
    c1 = _case(title="收货人长度20与21字符的边界校验（20字符合法）", expected=("订单创建成功",))
    c2 = _case(title="收货人长度20与21字符的边界校验（21字符超长）", expected=("收货人不能超过20个字符",))
    c1.test_point_id = c2.test_point_id = "tp-boundary"
    gate = run_quality_gate([c1, c2])
    assert len(gate.kept) == 2
    assert gate.dropReasons["duplicate"] == 0


def test_drop_multiple_inputs_for_same_field():
    """同一字段先填20字符、再填21字符 → 一例两输入，丢弃。"""
    steps = [
        {
            "order": 1,
            "type": "ui_action",
            "action": "打开页面",
            "target": "页面",
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
            "inputValue": "合" * 20,
        },
        {
            "order": 3,
            "type": "input",
            "action": "输入收货人",
            "target": "收货人",
            "uiSelector": {"type": "placeholder", "value": "请输入收货人"},
            "expected": "",
            "inputValue": "超" * 21,
        },
        {
            "order": 4,
            "type": "state_assert",
            "action": "验证「收货人不能超过20个字符」",
            "target": "",
            "uiSelector": {"type": "text", "value": "「收货人不能超过20个字符」"},
            "expected": "「收货人不能超过20个字符」",
            "inputValue": "",
        },
    ]
    case = _case(structured_steps=steps, expected=("收货人不能超过20个字符",))
    gate = run_quality_gate([case])
    assert gate.dropped == 1
    assert gate.dropReasons["multiple_inputs"] == 1


def test_multiple_inputs_different_fields_allowed():
    """不同字段各填一次是正常单场景用例，不能误杀。"""
    steps = [
        {
            "order": 1,
            "type": "input",
            "action": "输入手机号",
            "target": "手机号",
            "uiSelector": {"type": "placeholder", "value": "请输入手机号"},
            "expected": "",
            "inputValue": "13800138000",
        },
        {
            "order": 2,
            "type": "input",
            "action": "输入收货地址",
            "target": "收货地址",
            "uiSelector": {"type": "placeholder", "value": "请输入收货地址"},
            "expected": "",
            "inputValue": "北京市朝阳区测试路1号",
        },
        {
            "order": 3,
            "type": "state_assert",
            "action": "验证「订单创建成功」",
            "target": "",
            "uiSelector": {"type": "text", "value": "「订单创建成功」"},
            "expected": "「订单创建成功」",
            "inputValue": "",
        },
    ]
    gate = run_quality_gate([_case(structured_steps=steps, expected=("订单创建成功",))])
    assert len(gate.kept) == 1
