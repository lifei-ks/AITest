"""V3 静态分析测试：scanner / spring / vue 解析器（纯规则，零 LLM）。"""
from __future__ import annotations

from pathlib import Path

from app.analyzer.scanner import list_by_ext, scan
from app.analyzer.spring import analyze_backend
from app.analyzer.vue import analyze_frontend

SAMPLE = Path(__file__).resolve().parent.parent / "scripts" / "sample_src"


def test_scanner_detects_tech_stack():
    r = scan(str(SAMPLE))
    assert r.backend == "java-spring"
    assert r.frontend == "vue"


def test_spring_endpoints_no_duplicate_prefix():
    java = list_by_ext(scan(str(SAMPLE)), {".java"})
    backend = analyze_backend(java, str(SAMPLE))

    assert len(backend.endpoints) >= 5
    # 关键坑：类级 @RequestMapping 不能重复拼接
    for ep in backend.endpoints:
        assert "/api/orders/api/orders" not in ep.path
        assert ep.file and ep.line > 0  # 端点必须带 file 和 line

    # 前缀拼接正确
    paths = {ep.path for ep in backend.endpoints}
    assert "/api/orders/create" in paths
    assert "/api/orders/{id}/pay" in paths
    # 类级 @RequestMapping 不能成为独立端点
    assert all(ep.path != "/api/orders" for ep in backend.endpoints)


def test_spring_entities_and_constraints():
    backend = analyze_backend(list_by_ext(scan(str(SAMPLE)), {".java"}), str(SAMPLE))
    order = next((e for e in backend.entities if e.name == "Order"), None)
    assert order is not None
    field_names = {f.name for f in order.fields}
    assert {"receiverName", "amount", "remark"} <= field_names
    # 约束：receiverName notblank、remark size(max=200)
    constraints = {(c.field, c.rule) for c in order.fieldConstraints}
    assert ("receiverName", "notblank") in constraints
    assert ("remark", "size") in constraints
    # OrderStatus 枚举不应被误当实体
    assert all(e.name != "OrderStatus" for e in backend.entities)


def test_spring_enum_and_rules_and_ui_texts():
    backend = analyze_backend(list_by_ext(scan(str(SAMPLE)), {".java"}), str(SAMPLE))
    status = next((e for e in backend.enums if e.name == "OrderStatus"), None)
    assert status is not None
    labels = {v.name: v.label for v in status.values}
    assert labels["CREATED"] == "待支付"
    assert "PAID" in labels

    # 业务规则：4 处前置 throw
    assert len(backend.businessRules) >= 4
    guards = " ".join(b.guard for b in backend.businessRules)
    assert "CREATED" in guards and "SHIPPED" in guards

    # UI 文案：throw 里的中文消息
    assert "当前状态不可支付" in backend.uiTexts


def test_vue_routes_brace_balanced():
    frontend = analyze_frontend(
        list_by_ext(scan(str(SAMPLE)), {".vue", ".js"}), str(SAMPLE)
    )
    routes = {p.route for p in frontend.pages}
    # meta:{title} 里有 {}，括号配平必须能正确解析出两条路由
    assert "/orders/create" in routes
    assert "/orders/list" in routes


def test_vue_forms_selectors_texts():
    frontend = analyze_frontend(
        list_by_ext(scan(str(SAMPLE)), {".vue", ".js"}), str(SAMPLE)
    )
    create = next((p for p in frontend.pages if p.route == "/orders/create"), None)
    assert create is not None
    # 表单字段：收货人/金额/备注（不能把 <el-form-item> 当 <el-form>）
    all_fields = [f for form in create.forms for f in form.fields]
    props = {f.prop for f in all_fields}
    assert {"receiverName", "amount", "remark"} <= props

    # 选择器由 placeholder 反推
    placeholder_values = {s.value for s in frontend.selectors if s.type == "placeholder"}
    assert "请输入收货人" in placeholder_values

    # 文案
    assert "订单创建成功" in frontend.uiTexts
    assert "支付成功" in frontend.uiTexts
