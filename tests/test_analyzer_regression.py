"""V3 解析器回归测试：针对真实项目 order-system 暴露的 5 个问题。

这些问题是人造样本 sample_src 覆盖不到的，用真实代码回归才有价值。
"""
from __future__ import annotations

from pathlib import Path

from app.agent.state_machine_agent import extract_state_machines
from app.analyzer.scanner import list_by_ext, scan
from app.analyzer.spring import analyze_backend
from app.analyzer.vue import analyze_frontend

ORDER_SYSTEM = Path(__file__).resolve().parent.parent / "scripts" / "order-system"


def _backend():
    return analyze_backend(list_by_ext(scan(str(ORDER_SYSTEM)), {".java"}), str(ORDER_SYSTEM))


def _machines():
    java = list_by_ext(scan(str(ORDER_SYSTEM)), {".java"})
    return extract_state_machines(_backend(), java, str(ORDER_SYSTEM))


def _frontend():
    return analyze_frontend(
        list_by_ext(scan(str(ORDER_SYSTEM)), {".vue", ".js"}), str(ORDER_SYSTEM)
    )


# 问题 1：@PostMapping / @GetMapping 省略括号时漏解析
def test_bare_mapping_endpoints():
    backend = _backend()
    methods = {(ep.method, ep.path) for ep in backend.endpoints}
    assert ("POST", "/api/orders") in methods  # create（无括号 @PostMapping）
    assert ("GET", "/api/orders") in methods  # list（无括号 @GetMapping）


# 问题 2：实体过度识别（Controller/Service/通用类不该是实体）
def test_entities_exclude_controllers_and_services():
    backend = _backend()
    names = {e.name for e in backend.entities}
    assert "Order" in names
    assert "CreateOrderRequest" in names
    assert "OrderController" not in names
    assert "OrderServiceImpl" not in names
    assert "ApiResponse" not in names
    assert "BizException" not in names
    assert "DemoController" not in names


# 问题 3：同字段多个校验注解都要保留（@NotBlank 不能被 @Size 覆盖）
def test_multiple_constraints_kept():
    backend = _backend()
    req = next(e for e in backend.entities if e.name == "CreateOrderRequest")
    receiver_rules = {c.rule for c in req.fieldConstraints if c.field == "receiverName"}
    assert "notblank" in receiver_rules
    assert "size" in receiver_rules
    amount_rules = {c.rule for c in req.fieldConstraints if c.field == "amount"}
    assert "notnull" in amount_rules  # @NotNull 不能被 @DecimalMin 清掉


# 问题 4：写初始态（create 里 setStatus(CREATED)）不应产生状态转换
def test_no_transition_to_initial_state():
    machines = _machines()
    sm = machines[0]
    initial = sm.states[0]["name"]  # CREATED
    assert not any(t.to == initial for t in sm.transitions)


# 问题 5：无 prop/label 的空 form-item（按钮）不应产生字段
def test_no_empty_form_fields():
    frontend = _frontend()
    create = next(p for p in frontend.pages if p.name == "OrderCreate")
    fields = [f for form in create.forms for f in form.fields]
    assert all(f.prop or f.label for f in fields)
    props = {f.prop for f in fields}
    assert {"receiverName", "phone", "address", "amount", "remark"} <= props
