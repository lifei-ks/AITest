"""V4 规则模式生成器测试：用 order-system 真实分析结果，不配 LLM Key。"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.agent.quality_gate import run_quality_gate
from app.agent.state_machine_agent import extract_state_machines
from app.agent.test_generator_agent import generate_rule_cases, generate_rule_cases_for_points
from app.analyzer.scanner import list_by_ext, scan
from app.analyzer.spring import analyze_backend
from app.analyzer.vue import analyze_frontend
from app.service.generation_service import _gen_llm_for_points

ORDER_SYSTEM = Path(__file__).resolve().parent.parent / "scripts" / "order-system"


def _context():
    s = scan(str(ORDER_SYSTEM))
    java = list_by_ext(s, {".java"})
    backend = analyze_backend(java, str(ORDER_SYSTEM))
    vue_js = list_by_ext(s, {".vue", ".js"})
    frontend = analyze_frontend(vue_js, str(ORDER_SYSTEM))
    machines = [m.to_dict() for m in extract_state_machines(backend, java, str(ORDER_SYSTEM))]
    return backend, frontend, machines


def test_generate_at_least_12_and_four_types():
    backend, frontend, machines = _context()
    cases = generate_rule_cases(backend, frontend, machines)
    gate = run_quality_gate(cases)
    assert len(gate.kept) >= 12
    types = {c.type for c in gate.kept}
    assert {"positive", "negative", "boundary", "state"} <= types


def test_structured_steps_contract():
    backend, frontend, machines = _context()
    gate = run_quality_gate(generate_rule_cases(backend, frontend, machines))
    for c in gate.kept:
        assert c.structured_steps, f"{c.title} structuredSteps 为空"
        assert c.expected_results, f"{c.title} expectedResults 为空"
        assert c.test_data is not None
        for step in c.structured_steps:
            assert step["type"] in {"ui_action", "input", "state_assert"}


def test_state_case_has_pre_advance_steps():
    backend, frontend, machines = _context()
    gate = run_quality_gate(generate_rule_cases(backend, frontend, machines))
    state_cases = [c for c in gate.kept if c.type == "state"]
    assert state_cases
    # 完成（SHIPPED→COMPLETED）的前置应含"推进"步骤（支付、发货）
    complete = next((c for c in state_cases if "完成" in c.title), None)
    assert complete is not None
    actions = " ".join(s["action"] for s in complete.structured_steps)
    # 完成（SHIPPED→COMPLETED）的前置应是"支付""发货"等真实操作按钮
    assert "支付" in actions
    assert "发货" in actions


def _receiver_boundary_point(expected: str) -> dict:
    return {
        "id": "tp-receiver-boundary",
        "title": "收货人长度20与21字符的边界校验",
        "category": "boundary",
        "priority": "P1",
        "description": "分别提交20字符与21字符收货人，校验合法通过上限与超长提示",
        "pageRoute": "/orders/create",
        "endpoint": "",
        "fields": ["receiverName"],
        "expected": expected,
    }


def test_compound_boundary_point_splits_into_two_cases():
    """一个测试点同时写了合法/越界两个结果时，必须拆成两条单场景用例。"""
    backend, frontend, machines = _context()
    point = _receiver_boundary_point(
        "20字符可创建并提示「订单创建成功」；21字符提示「收货人不能超过20个字符」"
    )
    cases = generate_rule_cases_for_points([point], backend, frontend, machines)

    assert len(cases) == 2
    by_expected = {tuple(c.expected_results): c for c in cases}
    assert ("订单创建成功",) in by_expected
    assert ("收货人不能超过20个字符",) in by_expected

    required = {"receiverName", "phone", "address", "amount"}
    for case in cases:
        # 合法路径必须填齐全部必填字段，否则提交不了
        assert required <= set(case.test_data)
        receiver = case.test_data["receiverName"]
        asserting = [s for s in case.structured_steps if s["type"] == "state_assert"]
        assert len(asserting) == 1
        # 一个断言只允许一个锚点，不能把两句预期塞进一个「」
        assert asserting[0]["expected"].count("「") == 1
        assert asserting[0]["expected"].count("」") == 1

    assert len(by_expected[("订单创建成功",)].test_data["receiverName"]) == 20
    assert len(by_expected[("收货人不能超过20个字符",)].test_data["receiverName"]) == 21

    gate = run_quality_gate(cases)
    assert len(gate.kept) == 2, "同一测试点拆出的合法/越界用例不能被去重"
    assert gate.dropReasons["duplicate"] == 0


def test_atomic_boundary_point_generates_single_case():
    """新提示词产出的单场景边界点：合法值只出一条成功用例，越界只出一条报错用例。"""
    backend, frontend, machines = _context()

    valid_cases = generate_rule_cases_for_points(
        [_receiver_boundary_point("订单创建成功")], backend, frontend, machines
    )
    assert len(valid_cases) == 1
    assert valid_cases[0].expected_results == ["订单创建成功"]
    assert len(valid_cases[0].test_data["receiverName"]) == 20

    invalid_cases = generate_rule_cases_for_points(
        [_receiver_boundary_point("收货人不能超过20个字符")], backend, frontend, machines
    )
    assert len(invalid_cases) == 1
    assert invalid_cases[0].expected_results == ["收货人不能超过20个字符"]
    assert len(invalid_cases[0].test_data["receiverName"]) == 21


class _CompoundCaseClient:
    """模拟 LLM 仍把两个场景塞进一条用例的情况。"""

    def __init__(self, point_id: str):
        self.point_id = point_id

    def chat_json(self, _messages, **_kwargs):
        return {
            "testCases": [
                {
                    "testPointId": self.point_id,
                    "title": "收货人长度20与21字符的边界校验",
                    "expectedResults": [
                        "20字符可创建并提示「订单创建成功」；21字符提示「收货人不能超过20个字符」"
                    ],
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
                            "inputValue": "超" * 21,
                        },
                        {
                            "order": 3,
                            "type": "state_assert",
                            "action": "验证「收货人不能超过20个字符」",
                            "target": "",
                            "uiSelector": {"type": "text", "value": "「收货人不能超过20个字符」"},
                            "expected": "「收货人不能超过20个字符」",
                            "inputValue": "",
                        },
                    ],
                }
            ]
        }


def test_llm_compound_test_point_falls_back_to_rule_split():
    """复合测试点的 LLM 输出不可信，必须丢弃并交给规则拆成单场景用例。"""
    backend, frontend, machines = _context()
    point = _receiver_boundary_point(
        "20字符可创建并提示「订单创建成功」；21字符提示「收货人不能超过20个字符」"
    )
    requirement = SimpleNamespace(version="V1.0", name="订单管理系统测试1")

    llm_cases = _gen_llm_for_points(
        _CompoundCaseClient(point["id"]), requirement, [point], backend, frontend, machines
    )
    assert llm_cases == []

    rule_cases = generate_rule_cases_for_points([point], backend, frontend, machines)
    assert len(rule_cases) == 2
    assert {tuple(c.expected_results) for c in rule_cases} == {
        ("订单创建成功",),
        ("收货人不能超过20个字符",),
    }
