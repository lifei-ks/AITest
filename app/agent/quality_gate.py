"""三道质量闸（全规则，零 LLM）—— TASK_BOOK 6.1.4。

闸 0  一例多用例：复合预期（多个「」锚点、嵌套锚点、分号并列多个结果）或同一字段填入
      两个不同值，直接丢弃。一条用例只能验证一个输入场景，否则证据只保留最后一次输入（假失败/假成功）。
闸 1  UiLanguageLinter：拦截接口化描述 / 无 uiSelector 抽象动作 / 不可验证视觉断言
闸 2  ReviewQualityChecker 打分：起步 100，lintIssue 每条 −8（上限 −40）、无 uiSelector 步骤 −10、
      coverageRefs 全空 −10、步骤 >15 −10；expectedResults 空直接丢（硬门槛）、<40 丢
闸 3  去重：标题+全部步骤+预期归一化相似度 >0.95 视为重复，保留 qualityScore 高者；
      同一测试点拆出的合法/越界用例预期不同，不判重
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

# 接口化描述关键词（命中即 lint issue）
FORBIDDEN_API_WORDS = [
    "调用接口", "调用api", "发送请求", "请求接口", "http请求",
    "验证数据库", "检查数据库", "查询数据库", "检查返回值", "api_call",
]

# 不可验证的视觉断言关键词
VAGUE_ASSERT_WORDS = ["显示正常", "符合预期", "页面正常", "正常显示", "没问题"]

# 页面可见文案锚点（与执行器保持一致：中文角括号/弯引号/英文引号）
QUOTE_ANCHOR_RE = re.compile(r"[\u300c\u300e\u201c\u2018\"']([^\u300d\u300f\u201d\u2019\"']+)[\u300d\u300f\u201d\u2019\"']")
COMPOUND_SEPARATOR_RE = re.compile(r"[；;]")


def quoted_anchors(text: str) -> list[str]:
    """提取预期中的可见文案锚点，如「订单创建成功」→ ['订单创建成功']。"""
    return [m.strip() for m in QUOTE_ANCHOR_RE.findall(text or "") if m.strip()]


def is_compound_expected(text: str) -> bool:
    """预期是否描述了多个场景/多个可观察结果。

    命中任一即视为复合：
    - 出现两个及以上引号锚点（如：20字符提示「创建成功」；21字符提示「超长」）
    - 锚点内部又嵌套「」（执行器只能切出半句，必然找不到文案）
    - 分号两侧各是一句完整中文结果
    """
    raw = (text or "").strip()
    if not raw:
        return False
    if raw.count("「") >= 2 or raw.count("『") >= 2:
        return True
    anchors = quoted_anchors(raw)
    if len(anchors) >= 2:
        return True
    if any("「" in a or "『" in a for a in anchors):
        return True
    parts = [p.strip() for p in COMPOUND_SEPARATOR_RE.split(raw) if p.strip()]
    return len(parts) >= 2 and all(len(p) >= 4 for p in parts)


def case_has_compound_expected(case) -> bool:
    """用例的 expectedResults 或 state_assert 步骤是否含复合预期。"""
    if any(is_compound_expected(exp) for exp in case.expected_results):
        return True
    for step in case.structured_steps:
        if step.get("type") != "state_assert":
            continue
        selector = step.get("uiSelector") or {}
        for raw in (step.get("expected"), selector.get("value")):
            if is_compound_expected(str(raw or "")):
                return True
    return False


def steps_have_multiple_inputs(steps) -> bool:
    """同一字段被填入两个不同的值 → 一条用例在验证两个输入场景。

    如：先填20字符提交验证成功、再改成21字符提交验证超长。两个断言可能分别通过，
    但失败时无法判断是哪个场景的问题，且截图/证据只保留最后一次输入。
    """
    seen: dict[str, str] = {}
    for step in steps or []:
        if step.get("type") != "input":
            continue
        selector = step.get("uiSelector") or {}
        key = str(selector.get("value") or step.get("target") or "").strip()
        value = str(step.get("inputValue") or "").strip()
        if not key or not value:
            continue
        if key in seen and seen[key] != value:
            return True
        seen[key] = value
    return False


def case_has_multiple_inputs(case) -> bool:
    return steps_have_multiple_inputs(case.structured_steps)


@dataclass
class GateResult:
    generated: int
    kept: list = field(default_factory=list)
    dropped: int = 0
    dropReasons: dict = field(default_factory=dict)


class UiLanguageLinter:
    """闸 1：拦截接口化描述、无 uiSelector 的抽象动作、不可验证断言。"""

    def lint(self, case) -> list[str]:
        issues: list[str] = []
        for step in case.structured_steps:
            action = step.get("action", "")
            target = step.get("target", "")
            text = f"{action} {target}".lower()
            for w in FORBIDDEN_API_WORDS:
                if w in text:
                    issues.append(f"接口化描述：{action}")
                    break
            # 无 uiSelector 的抽象动作（ui_action / input 必须有定位）
            sel = step.get("uiSelector") or {}
            if step.get("type") in ("ui_action", "input") and not sel.get("value"):
                issues.append(f"缺少 uiSelector：{action}")
            # capture 步骤必须声明变量和提取规则，否则执行器无法消费
            if step.get("type") == "capture":
                if not str(step.get("variable") or "").strip():
                    issues.append(f"capture 缺少 variable：{action}")
                if not str(step.get("pattern") or "").strip():
                    issues.append(f"capture 缺少 pattern：{action}")
        for exp in case.expected_results:
            for w in VAGUE_ASSERT_WORDS:
                if w in exp:
                    issues.append(f"不可验证断言：{exp}")
                    break
        return issues


def score_case(case, lint_issues: list[str]) -> int:
    """闸 2：打分 0~100。"""
    s = 100
    s -= min(8 * len(lint_issues), 40)
    for step in case.structured_steps:
        sel = step.get("uiSelector") or {}
        if step.get("type") in ("ui_action", "input") and not sel.get("value"):
            s -= 10
    refs = case.coverage_refs or {}
    if not any(refs.values()):
        s -= 10
    if len(case.structured_steps) > 15:
        s -= 10
    return max(s, 0)


def _normalize(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


def _scenario_text(case) -> str:
    """去重指纹：标题 + 全部步骤（含输入值/断言）+ 预期结果。

    只用标题+首步骤会把「20字符合法」和「21字符超长」这类同点拆出的用例误判为重复。
    """
    steps = "".join(
        f"{s.get('action', '')}{s.get('target', '')}{s.get('inputValue', '')}{s.get('expected', '')}"
        f"{s.get('variable', '')}{s.get('pattern', '')}"
        for s in case.structured_steps
    )
    return _normalize(f"{case.title}{steps}{'|'.join(case.expected_results)}")


def _similarity(a, b) -> float:
    return SequenceMatcher(None, _scenario_text(a), _scenario_text(b)).ratio()


def _same_expected(a, b) -> bool:
    return _normalize("|".join(a.expected_results)) == _normalize("|".join(b.expected_results))


def _is_duplicate(case, existing) -> bool:
    # 同一测试点拆出的合法/越界用例：标题高度相似但预期不同，属于两条合法用例
    if (
        case.test_point_id
        and case.test_point_id == existing.test_point_id
        and not _same_expected(case, existing)
    ):
        return False
    return _similarity(case, existing) > 0.95


def run_quality_gate(cases: list) -> GateResult:
    """三道闸：Linter → 打分 → 去重。给每个通过用例附加 lint_issues / quality_score。"""
    linter = UiLanguageLinter()
    dropReasons = {
        "no_expected": 0,
        "compound_expected": 0,
        "multiple_inputs": 0,
        "low_score": 0,
        "duplicate": 0,
    }
    scored: list = []

    for case in cases:
        # 硬门槛：无 expectedResults 直接丢弃
        if not case.expected_results:
            dropReasons["no_expected"] += 1
            continue
        # 硬门槛：复合预期（一条用例判断两个输入/两个结果）无法被单次执行同时满足
        if case_has_compound_expected(case):
            dropReasons["compound_expected"] += 1
            continue
        # 硬门槛：同一字段填两个值，本质仍是一条用例验证两个输入场景
        if case_has_multiple_inputs(case):
            dropReasons["multiple_inputs"] += 1
            continue
        case.lint_issues = linter.lint(case)
        case.quality_score = score_case(case, case.lint_issues)
        if case.quality_score < 40:
            dropReasons["low_score"] += 1
            continue
        scored.append(case)

    # 去重：保留 qualityScore 高的。
    # 决策：阈值 0.95（原 TASK_BOOK 建议 0.85）。理由：规则生成的同类型用例标题仅字段名不同，
    # 0.85 会误杀"收货人为空/手机号为空"这类合法不同用例；0.95 只去掉几乎完全一致的重复。
    kept: list = []
    for case in sorted(scored, key=lambda c: c.quality_score, reverse=True):
        if any(_is_duplicate(case, existing) for existing in kept):
            dropReasons["duplicate"] += 1
        else:
            kept.append(case)

    return GateResult(
        generated=len(cases),
        kept=kept,
        dropped=len(cases) - len(kept),
        dropReasons=dropReasons,
    )
