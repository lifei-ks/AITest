"""V4 规则模式生成器测试：用 order-system 真实分析结果，不配 LLM Key。"""
from __future__ import annotations

from pathlib import Path

from app.agent.quality_gate import run_quality_gate
from app.agent.state_machine_agent import extract_state_machines
from app.agent.test_generator_agent import generate_rule_cases
from app.analyzer.scanner import list_by_ext, scan
from app.analyzer.spring import analyze_backend
from app.analyzer.vue import analyze_frontend

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
