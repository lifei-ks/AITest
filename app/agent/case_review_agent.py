"""AI generated-case review and repair prompts."""
from __future__ import annotations

import json
import re

from app.llm.client import LlmUnavailable

CASE_REVIEW_OK = "ok"
CASE_REVIEW_SUSPECT = "suspect"
CASE_REVIEW_FAIL = "fail"
CASE_REVIEW_VERDICTS = (CASE_REVIEW_OK, CASE_REVIEW_SUSPECT, CASE_REVIEW_FAIL)


def _compact(value, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _build_facts(facts: dict) -> dict:
    return {
        "requirement": facts.get("requirement") or {},
        "testPoint": facts.get("testPoint") or {},
        "pages": facts.get("pages") or [],
        "selectors": (facts.get("selectors") or [])[:100],
        "uiTexts": (facts.get("uiTexts") or [])[:80],
        "endpoints": (facts.get("endpoints") or [])[:80],
        "entities": (facts.get("entities") or [])[:30],
        "businessRules": (facts.get("businessRules") or [])[:60],
        "enums": (facts.get("enums") or [])[:20],
        "stateMachines": facts.get("stateMachines") or [],
    }


def review_case(client: LlmClient, case: dict, facts: dict) -> dict:
    """Review one generated case against requirement and source-derived facts."""
    payload = {
        "case": case,
        "requirementAndSourceFacts": _build_facts(facts),
    }
    prompt = f"""你是资深 UI 自动化测试评审员。请根据本期需求文档与被测系统源码事实，严格评审下面这条已生成用例。

评审输入：
{json.dumps(payload, ensure_ascii=False, indent=2)}

必须逐项检查：
1. 用例是否完整覆盖测试点意图；不得偏离需求或漏掉关键前置条件。
2. 只能使用事实中的页面路由、字段 placeholder/label、按钮/文案、接口、状态和业务规则；编造定位器是 blocker。
3. 每个可执行步骤必须有真实 uiSelector；input 必须有 inputValue；禁止 api_call/数据库操作。
4. state_assert 必须有可观察锚点（「」引号文案或精确 URL）；模糊的“显示正常”是 blocker。
5. 正例/反例/边界/状态流转的测试数据与预期结果必须匹配需求与源码约束。
6. 步骤顺序必须符合真实页面跳转和状态前置条件。
7. 问题必须给出证据，例如页面路由、字段、placeholder、业务规则、状态转换或需求摘录。

verdict 规则：
- ok：没有 blocker/warning。
- suspect：存在 warning，或某项无法基于提供事实确认。
- fail：存在 blocker，或用例无法执行/会错误执行。

只输出 JSON：
{{
  "verdict": "ok|suspect|fail",
  "reason": "不超过 160 字的中文总结",
  "issues": [
    {{
      "severity": "blocker|warning|info",
      "field": "title|expectedResults|testData|structuredSteps|coverageRefs|preconditions",
      "problem": "具体问题",
      "suggestion": "可执行的修复建议",
      "evidence": "需求/源码事实依据"
    }}
  ]
}}
"""
    data = client.chat_json([{"role": "user", "content": prompt}], max_tokens=4096, json_mode=True)
    return parse_review(data)


def parse_review(data) -> dict:
    if not isinstance(data, dict):
        raise LlmUnavailable("AI 质检返回结构非法")
    verdict = str(data.get("verdict") or "").strip().lower()
    if verdict not in CASE_REVIEW_VERDICTS:
        verdict = CASE_REVIEW_SUSPECT
    raw_issues = data.get("issues") if isinstance(data.get("issues"), list) else []
    issues: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in raw_issues[:12]:
        if not isinstance(item, dict):
            continue
        problem = _compact(item.get("problem"), 1000)
        if not problem:
            continue
        severity = str(item.get("severity") or "warning").lower()
        if severity not in ("blocker", "warning", "info"):
            severity = "warning"
        key = (severity, problem)
        if key in seen:
            continue
        seen.add(key)
        issues.append({
            "severity": severity,
            "field": _compact(item.get("field"), 60) or "case",
            "problem": problem,
            "suggestion": _compact(item.get("suggestion"), 1000),
            "evidence": _compact(item.get("evidence"), 500),
        })
    if verdict == CASE_REVIEW_OK and any(i["severity"] in ("blocker", "warning") for i in issues):
        verdict = CASE_REVIEW_SUSPECT
    if verdict != CASE_REVIEW_OK and not issues:
        issues.append({
            "severity": "warning" if verdict == CASE_REVIEW_SUSPECT else "blocker",
            "field": "case",
            "problem": "AI 认为用例有问题但未给出具体问题",
            "suggestion": "请人工检查后修复",
            "evidence": "",
        })
    return {
        "verdict": verdict,
        "reason": _compact(data.get("reason"), 800) or ("用例通过 AI 质检" if verdict == CASE_REVIEW_OK else "AI 质检发现问题"),
        "issues": issues,
    }


def repair_case(client: LlmClient, case: dict, facts: dict, issues: list[dict] | None = None) -> dict:
    """Ask the model for a minimal patch; the service layer must validate it before persistence."""
    payload = {
        "case": case,
        "reviewIssues": issues or [],
        "requirementAndSourceFacts": _build_facts(facts),
    }
    prompt = f"""你是资深 UI 自动化测试用例修复专家。请根据需求文档、源码事实和 AI 质检问题，修复这条用例。

修复输入：
{json.dumps(payload, ensure_ascii=False, indent=2)}

硬性要求：
1. 只输出需要修改的字段 patch；未修改字段不要输出。
2. structuredSteps 一旦修改，必须输出修复后的完整数组，不能只输出部分步骤。
3. 第一步必须是 ui_action + route，且 route 必须来自事实中的页面路由。
4. placeholder/label 必须与事实中的字段完全一致；text 只能使用事实中的按钮/文案。
5. input 必须有具体 inputValue；state_assert 的 expected 必须使用「真实页面文案」或需求文档中的明确文案锚定。
6. 禁止 api_call、数据库校验和模糊断言。
7. 正例/反例/边界/状态类型的测试数据、步骤和预期必须保持一致。
8. 保留原用例中正确的内容，不要无意义重写。

只输出 JSON：
{{
  "summary": "不超过 120 字的修复说明",
  "patch": {{
    "title": "如需修改才输出",
    "module": "如需修改才输出",
    "type": "positive|negative|boundary|state",
    "priority": "P0|P1|P2",
    "preconditions": ["如需修改才输出"],
    "expectedResults": ["如需修改才输出"],
    "testData": {{"如需修改才输出": "字段值"}},
    "structuredSteps": [
      {{"order":1,"type":"ui_action","action":"打开页面","target":"页面","uiSelector":{{"type":"route","value":"/orders/create"}},"expected":"","inputValue":""}}
    ]
  }}
}}
"""
    data = client.chat_json([{"role": "user", "content": prompt}], max_tokens=6144, json_mode=True)
    if not isinstance(data, dict) or not isinstance(data.get("patch"), dict):
        raise LlmUnavailable("AI 修复返回结构非法")
    patch = data.get("patch") or {}
    if not patch:
        raise LlmUnavailable("AI 认为无需修改，未返回补丁")
    return {
        "summary": _compact(data.get("summary"), 500) or "AI 修复了用例",
        "patch": patch,
    }
