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


def test_llm_failure_stops_remaining_batches():
    from app.llm.client import LlmUnavailable

    backend, frontend, machines = _context()
    points = [
        {
            "id": f"p{i}",
            "title": f"测试点{i}",
            "category": "functional",
            "priority": "P1",
            "description": "",
            "pageRoute": "/orders/create",
            "endpoint": "",
            "fields": [],
            "expected": "「订单创建成功」",
        }
        for i in range(6)
    ]

    class FailingClient:
        calls = 0
        model = "reasoning-model"

        def chat_json(self, *args, **kwargs):
            self.calls += 1
            raise LlmUnavailable("只有 reasoning_content，没有最终答案")

    client = FailingClient()
    requirement = SimpleNamespace(version="V1.0", name="订单")
    cases = _gen_llm_for_points(client, requirement, points, backend, frontend, machines)
    assert cases == []
    assert client.calls == 1, "第一批失败后应停止后续批次，交给规则兜底"


def test_expected_result_string_is_not_split_into_characters():
    """GLM 可能把 expectedResults 返成字符串，不能逐字符拆成数组。"""
    from app.service.generation_service import _as_text_list

    assert _as_text_list("对一个已支付订单点击发货后提示「发货成功」") == [
        "对一个已支付订单点击发货后提示「发货成功」"
    ]
    # 历史脏数据：一条字符串被拆成了单字符数组，读取时合并
    assert _as_text_list(["对", "一", "个", "已", "发"]) == ["对一个已发"]
    # 正常的多条预期不能被合并
    assert _as_text_list(["第一条预期", "第二条预期"]) == ["第一条预期", "第二条预期"]


def test_llm_string_expected_results_keeps_full_sentence():
    backend, frontend, machines = _context()
    point = {
        "id": "p1",
        "title": "合法数据创建订单",
        "category": "functional",
        "priority": "P0",
        "description": "",
        "pageRoute": "/orders/create",
        "endpoint": "",
        "fields": [],
        "expected": "「订单创建成功」",
    }

    class StringExpectedClient:
        model = "glm"

        def chat_json(self, *args, **kwargs):
            return {
                "testCases": [
                    {
                        "testPointId": "p1",
                        "title": "合法数据创建订单成功",
                        "type": "positive",
                        "priority": "P0",
                        "expectedResults": "填写合法数据提交后页面提示「订单创建成功」",
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
                                "type": "state_assert",
                                "action": "验证创建成功",
                                "target": "",
                                "uiSelector": {"type": "text", "value": "「订单创建成功」"},
                                "expected": "「订单创建成功」",
                                "inputValue": "",
                            },
                        ],
                    }
                ]
            }

    requirement = SimpleNamespace(version="V1.0", name="订单")
    cases = _gen_llm_for_points(
        StringExpectedClient(), requirement, [point], backend, frontend, machines
    )
    assert len(cases) == 1
    assert cases[0].expected_results == ["填写合法数据提交后页面提示「订单创建成功」"]


def _filter_point(title: str, description: str, expected: str, route: str = "/orders/list") -> dict:
    return {
        "id": "tp-filter",
        "title": title,
        "category": "functional",
        "priority": "P1",
        "description": description,
        "pageRoute": route,
        "endpoint": "GET /api/orders",
        "fields": [],
        "expected": expected,
    }


def test_filter_point_clicks_query_and_only_fills_min():
    """列表页筛选应点“查询”，且“只填最低金额”不能顺手填入最高金额。"""
    backend, frontend, machines = _context()
    point = _filter_point(
        "只填最低金额100筛选订单",
        "列表页只填最低金额100查询，返回金额≥100的订单，闭区间含边界",
        "「查询」后列表仅剩金额≥100的订单",
    )
    cases = generate_rule_cases_for_points([point], backend, frontend, machines)
    assert len(cases) == 1
    case = cases[0]
    actions = [s["action"] for s in case.structured_steps]
    assert "点击查询" in actions
    assert "点击提交" not in actions
    inputs = [s for s in case.structured_steps if s["type"] == "input"]
    assert [(s["target"], s["inputValue"]) for s in inputs] == [("最低金额", "100")]


def test_filter_point_range_uses_both_amounts_and_order_no_anchor():
    backend, frontend, machines = _context()
    point = _filter_point(
        "金额区间100-200筛选订单",
        "填最低100、最高200查询，种子数据只剩199.00订单，total为1",
        "「查询」后列表只剩订单号ORD20260101001",
    )
    cases = generate_rule_cases_for_points([point], backend, frontend, machines)
    case = cases[0]
    inputs = [s for s in case.structured_steps if s["type"] == "input"]
    assert [(s["target"], s["inputValue"]) for s in inputs] == [
        ("最低金额", "100"),
        ("最高金额", "200"),
    ]
    assert case.expected_results == ["ORD20260101001"]


def test_filter_point_status_select_uses_element_plus_dropdown_selectors():
    backend, frontend, machines = _context()
    point = _filter_point(
        "金额区间与状态组合筛选",
        "金额区间与订单状态组合查询，只返回同时满足金额和状态条件的订单",
        "「查询」后列表仅剩同时满足两条件的订单",
    )
    cases = generate_rule_cases_for_points([point], backend, frontend, machines)
    steps = cases[0].structured_steps
    status_steps = [s for s in steps if "订单状态" in s["action"] or "已支付" in s["action"]]
    assert len(status_steps) == 2
    # 不能再用 input[placeholder] 点击，必须点 Element Plus 的 wrapper 和下拉项
    assert status_steps[0]["uiSelector"]["type"] == "css"
    assert "el-select__wrapper" in status_steps[0]["uiSelector"]["value"]
    assert status_steps[1]["uiSelector"]["type"] == "css"
    assert "el-select-dropdown__item" in status_steps[1]["uiSelector"]["value"]


def test_stats_six_statuses_assert_each_label():
    backend, frontend, machines = _context()
    point = {
        "id": "tp-stats-status",
        "title": "状态分布六项齐全",
        "category": "functional",
        "priority": "P1",
        "description": "统计接口固定返回6个状态数量，未出现的状态为0",
        "pageRoute": "/orders/statistics",
        "endpoint": "GET /api/orders/statistics",
        "fields": [],
        "expected": "「状态分布」",
    }
    cases = generate_rule_cases_for_points([point], backend, frontend, machines)
    asserts = [s for s in cases[0].structured_steps if s["type"] == "state_assert"]
    assert [s["expected"] for s in asserts] == [
        "「待支付」", "「已支付」", "「已发货」", "「已完成」", "「已取消」", "「已退款」"
    ]


def test_stats_two_decimal_point_uses_capture_pattern():
    backend, frontend, machines = _context()
    point = {
        "id": "tp-stats-decimal",
        "title": "统计页面金额两位小数展示",
        "category": "functional",
        "priority": "P1",
        "description": "统计页面总额、平均金额、有效金额统一保留2位小数",
        "pageRoute": "/orders/statistics",
        "endpoint": "GET /api/orders/statistics",
        "fields": [],
        "expected": "「刷新统计」后金额保留两位小数",
    }
    cases = generate_rule_cases_for_points([point], backend, frontend, machines)
    capture = [s for s in cases[0].structured_steps if s["type"] == "capture"]
    assert len(capture) == 1
    assert capture[0]["pattern"] == r"订单总额\s*[¥￥]?\s*(\d+\.\d{2})"
