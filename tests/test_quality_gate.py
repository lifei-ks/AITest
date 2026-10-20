"""V4 质量闸测试：三道闸（Linter → 打分 → 去重）独立验证。"""
from __future__ import annotations

from app.agent.quality_gate import UiLanguageLinter, run_quality_gate
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
