"""分层断言回归：URL 与文案锚点必须同时满足。"""
from __future__ import annotations

from app.agent.execution_agent import _do_assert, _url_path_matches


class FakePage:
    def __init__(self, url: str, body: str):
        self.url = url
        self._body = body

    def inner_text(self, selector: str) -> str:
        assert selector == "body"
        return self._body


def test_url_and_anchor_both_pass():
    page = FakePage("http://localhost/orders", "订单创建成功")
    ok, status, detail = _do_assert(page, "跳转到 /orders 并提示「订单创建成功」")
    assert ok is True
    assert status == "passed"
    assert "URL 命中" in detail and "文本锚点命中" in detail


def test_url_match_but_anchor_missing_fails():
    page = FakePage("http://localhost/orders", "暂无数据")
    ok, status, detail = _do_assert(page, "跳转到 /orders 并提示「订单创建成功」")
    assert ok is False
    assert status == "failed"
    assert "文本锚点缺失" in detail


def test_url_mismatch_even_if_anchor_matches_fails():
    page = FakePage("http://localhost/create", "订单创建成功")
    ok, status, detail = _do_assert(page, "跳转到 /orders 并提示「订单创建成功」")
    assert ok is False
    assert status == "failed"
    assert "URL 未命中" in detail


def test_url_only_mismatch_is_failed_not_skipped():
    page = FakePage("http://localhost/create", "暂无数据")
    ok, status, detail = _do_assert(page, "跳转到 /orders")
    assert ok is False
    assert status == "failed"
    assert "URL 未命中" in detail


def test_anchor_only_passes():
    page = FakePage("http://localhost/create", "订单创建成功")
    ok, status, _ = _do_assert(page, "「订单创建成功」")
    assert ok is True
    assert status == "passed"


def test_no_anchor_is_skipped():
    page = FakePage("http://localhost/create", "页面正常")
    ok, status, detail = _do_assert(page, "显示正常")
    assert ok is False
    assert status == "skipped"
    assert "无可验证" in detail


def test_numeric_slash_is_not_treated_as_url():
    page = FakePage("http://localhost/orders", "共 20/21 字符")
    ok, status, _ = _do_assert(page, "「共 20/21 字符」")
    assert ok is True
    assert status == "passed"


def test_url_path_exact_match_not_substring():
    assert _url_path_matches("/orders", "http://localhost/orders/new") is False
    assert _url_path_matches("/orders", "http://localhost/orders?x=1") is True


def test_url_path_supports_placeholder_segment():
    assert _url_path_matches("/orders/{id}", "http://localhost/orders/123") is True
    assert _url_path_matches("/orders/{id}/pay", "http://localhost/orders/123/pay") is True
