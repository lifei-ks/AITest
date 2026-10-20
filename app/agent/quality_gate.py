"""三道质量闸（全规则，零 LLM）—— TASK_BOOK 6.1.4。

闸 1  UiLanguageLinter：拦截接口化描述 / 无 uiSelector 抽象动作 / 不可验证视觉断言
闸 2  ReviewQualityChecker 打分：起步 100，lintIssue 每条 −8（上限 −40）、无 uiSelector 步骤 −10、
      coverageRefs 全空 −10、步骤 >15 −10；expectedResults 空直接丢（硬门槛）、<40 丢
闸 3  去重：标题+首步骤归一化相似度 >0.85 视为重复，保留 qualityScore 高者
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


def _similarity(a, b) -> float:
    """标题+首步骤的归一化相似度。"""
    sa = _normalize(f"{a.title}{a.structured_steps[0]['action'] if a.structured_steps else ''}")
    sb = _normalize(f"{b.title}{b.structured_steps[0]['action'] if b.structured_steps else ''}")
    return SequenceMatcher(None, sa, sb).ratio()


def run_quality_gate(cases: list) -> GateResult:
    """三道闸：Linter → 打分 → 去重。给每个通过用例附加 lint_issues / quality_score。"""
    linter = UiLanguageLinter()
    dropReasons = {"no_expected": 0, "low_score": 0, "duplicate": 0}
    scored: list = []

    for case in cases:
        case.lint_issues = linter.lint(case)
        # 硬门槛：无 expectedResults 直接丢弃
        if not case.expected_results:
            dropReasons["no_expected"] += 1
            continue
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
        if any(_similarity(case, existing) > 0.95 for existing in kept):
            dropReasons["duplicate"] += 1
        else:
            kept.append(case)

    return GateResult(
        generated=len(cases),
        kept=kept,
        dropped=len(cases) - len(kept),
        dropReasons=dropReasons,
    )
