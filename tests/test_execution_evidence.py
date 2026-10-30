"""输入值 DOM 证据：长文本不靠截图判断完整性。"""
from __future__ import annotations

from app.agent.execution_agent import _input_evidence
from app.agent.review_agent import _build_prompt, _steps_text


def test_input_evidence_marks_long_value_as_match():
    value = "a" * 100
    evidence = _input_evidence(value, value)
    assert evidence["inputValueLength"] == 100
    assert evidence["actualValueLength"] == 100
    assert evidence["valueMatch"] is True
    assert evidence["actualValuePreview"].startswith("a" * 20)
    assert "..." in evidence["actualValuePreview"]


def test_input_evidence_detects_truncated_value():
    evidence = _input_evidence("a" * 100, "a" * 99)
    assert evidence["inputValueLength"] == 100
    assert evidence["actualValueLength"] == 99
    assert evidence["valueMatch"] is False


def test_short_value_preview_is_not_truncated():
    evidence = _input_evidence("张三", "张三")
    assert evidence["actualValuePreview"] == "张三"


def test_sensitive_input_does_not_store_value_or_hash():
    evidence = _input_evidence("secret", "secret", sensitive=True)
    assert evidence["sensitive"] is True
    assert evidence["valueMatch"] is True
    assert "actualValuePreview" not in evidence
    assert "actualValueHash" not in evidence


def test_steps_text_includes_dom_evidence():
    steps = [{
        "stepType": "input",
        "action": "输入收货人",
        "result": "passed",
        "target": "收货人",
        "selectorUsed": "请输入收货人",
        "evidence": _input_evidence("a" * 100, "a" * 100),
    }]
    text = _steps_text(steps)
    assert "输入长度：100" in text
    assert "DOM实际长度：100" in text
    assert "值一致：True" in text


def test_prompt_has_long_input_rule():
    prompt = _build_prompt(
        {"title": "长文本输入", "expectedResults": ["「保存成功」"]},
        [{
            "stepType": "input",
            "action": "输入收货人",
            "result": "passed",
            "evidence": _input_evidence("a" * 100, "a" * 100),
        }],
        "http://localhost",
        [],
    )
    assert "以 DOM 证据为准" in prompt
    assert "DOM实际长度：100" in prompt
    assert "没有提供截图的步骤" in prompt
