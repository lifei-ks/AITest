"""V3 状态机提取测试：setStatus 证据 + 守卫推导 + BFS 最短路径。"""
from __future__ import annotations

from pathlib import Path

from app.agent.state_machine_agent import bfs_paths, extract_state_machines
from app.analyzer.scanner import list_by_ext, scan
from app.analyzer.spring import analyze_backend

SAMPLE = Path(__file__).resolve().parent.parent / "scripts" / "sample_src"


def _machines():
    java = list_by_ext(scan(str(SAMPLE)), {".java"})
    backend = analyze_backend(java, str(SAMPLE))
    return extract_state_machines(backend, java, str(SAMPLE))


def test_state_machine_basic():
    machines = _machines()
    assert len(machines) >= 1
    sm = machines[0]
    assert sm.name == "OrderStatus"
    assert len(sm.states) == 5


def test_transitions_verified_and_count():
    sm = _machines()[0]
    assert len(sm.transitions) >= 4
    # verified 唯一判据：找到 setStatus 写入点
    assert all(t.verified for t in sm.transitions)


def test_pay_from_created_to_paid():
    sm = _machines()[0]
    pay = [t for t in sm.transitions if t.trigger == "支付"]
    assert pay, "应有支付转换"
    assert any(t.from_ == "CREATED" and t.to == "PAID" for t in pay)


def test_cancel_exclusion_semantics():
    """排除语义：SHIPPED/COMPLETED 不可取消 → from 不含它们。"""
    sm = _machines()[0]
    cancels = [t for t in sm.transitions if t.trigger == "取消"]
    assert cancels
    cancel_froms = {t.from_ for t in cancels}
    assert "SHIPPED" not in cancel_froms
    assert "COMPLETED" not in cancel_froms
    assert "CREATED" in cancel_froms  # 待支付可取消


def test_evidence_has_file_line():
    sm = _machines()[0]
    for t in sm.transitions:
        assert t.evidence, "每条转换必须有证据"
        assert any("#" in e for e in t.evidence)


def test_bfs_shortest_path():
    sm = _machines()[0]
    paths = bfs_paths(sm.states, sm.transitions, initial_state="CREATED")
    # 支付→发货→完成 的最短推进路径
    assert paths.get("COMPLETED") == ["CREATED", "PAID", "SHIPPED", "COMPLETED"]
    assert paths.get("PAID") == ["CREATED", "PAID"]


def test_not_equal_guard_uses_single_source_state(tmp_path):
    """批量动作里的 status != CREATED 是否定守卫，不能推导成其余全部状态。"""
    enum_file = tmp_path / "OrderStatus.java"
    nl = chr(10)
    enum_file.write_text(
        nl.join([
            "public enum OrderStatus {",
            "    CREATED,",
            "    PAID,",
            "    SHIPPED,",
            "    COMPLETED,",
            "    CANCELLED,",
            "    REFUNDED",
            "}",
        ]),
        encoding="utf-8",
    )
    service_file = tmp_path / "OrderService.java"
    service_file.write_text(
        """
public class OrderService {
    public void batchCancel(List<Long> ids) {
        for (Long id : ids) {
            Order order = new Order();
            if (order.getStatus() != OrderStatus.CREATED) {
                continue;
            }
            order.setStatus(OrderStatus.CANCELLED);
        }
    }
}
""",
        encoding="utf-8",
    )
    backend = analyze_backend([str(enum_file), str(service_file)], str(tmp_path))
    machines = extract_state_machines(backend, [str(service_file)], str(tmp_path))
    assert machines
    transitions = machines[0].transitions
    assert len(transitions) == 1
    assert transitions[0].from_ == "CREATED"
    assert transitions[0].to == "CANCELLED"
