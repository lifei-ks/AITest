"""Agent 3：生成用例的独立审核与修复。

- 提示词外置：``case_review.txt`` / ``case_repair.txt`` / ``case_review_workflow.txt``。
- 保留原有 ok/suspect/fail 接口（页面上的「AI 质检」继续使用）；
- 工作流使用 pass/revise/reject 三态接口，并明确区分“可修复”和“不可执行”。
- 修复仍只产生 patch，必须经过 case_review_service 的源码事实校验后才能落库。
"""
from __future__ import annotations

import json
import re

from app.agent.prompt_loader import render_prompt
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


def review_case(
    client: LlmClient,
    case: dict,
    facts: dict,
    deterministic: dict | None = None,
) -> dict:
    """Review one generated case against requirement and source-derived facts."""
    payload = {
        "case": case,
        "requirementAndSourceFacts": _build_facts(facts),
        "deterministicChecks": deterministic or {},
    }
    prompt = render_prompt(
        "case_review",
        {"REVIEW_PAYLOAD": json.dumps(payload, ensure_ascii=False, indent=2)},
    )
    data = client.chat_json([{"role": "user", "content": prompt}], max_tokens=4096, json_mode=True)
    return parse_review(data)


def parse_review(data) -> dict:
    if not isinstance(data, dict):
        raise LlmUnavailable("AI 质检返回结构非法")
    verdict = str(data.get("verdict") or "").strip().lower()
    if verdict not in CASE_REVIEW_VERDICTS:
        verdict = CASE_REVIEW_SUSPECT
    issues = _parse_issues(data.get("issues"))
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
    step_results: list[dict] = []
    raw_steps = data.get("stepResults") or data.get("step_results") or []
    for item in raw_steps[:20] if isinstance(raw_steps, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("stepIndex") or item.get("step_index") or 0)
        except (TypeError, ValueError):
            continue
        if index <= 0:
            continue
        step_issues = _parse_issues(item.get("issues"))
        if any(i["severity"] == "blocker" for i in step_issues):
            step_verdict = "fail"
        elif any(i["severity"] == "warning" for i in step_issues):
            step_verdict = "warning"
        else:
            step_verdict = "pass"
        step_results.append({
            "stepIndex": index,
            "stepType": _compact(item.get("stepType") or item.get("step_type"), 40),
            "action": _compact(item.get("action"), 255),
            "verdict": _compact(item.get("verdict"), 20) or step_verdict,
            "checks": item.get("checks") if isinstance(item.get("checks"), list) else [],
            "issues": step_issues,
        })
    return {
        "verdict": verdict,
        "reason": _compact(data.get("reason"), 800) or ("用例通过 AI 质检" if verdict == CASE_REVIEW_OK else "AI 质检发现问题"),
        "issues": issues,
        "stepResults": step_results,
    }


def repair_case(client: LlmClient, case: dict, facts: dict, issues: list[dict] | None = None) -> dict:
    """Ask the model for a minimal patch; the service layer must validate it before persistence."""
    payload = {
        "case": case,
        "reviewIssues": issues or [],
        "requirementAndSourceFacts": _build_facts(facts),
    }
    prompt = render_prompt(
        "case_repair",
        {"REPAIR_PAYLOAD": json.dumps(payload, ensure_ascii=False, indent=2)},
    )
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


# ---------- 工作流三态审核（pass / revise / reject） ----------

WORKFLOW_STATUSES = ("pass", "revise", "reject")
_STATUS_ALIASES = {
    "ok": "pass",
    "suspect": "revise",
    "fail": "reject",
    "passed": "pass",
    "revised": "revise",
    "rejected": "reject",
}


def _parse_issues(raw_issues) -> list[dict]:
    issues: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in raw_issues if isinstance(raw_issues, list) else []:
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
        issues.append(
            {
                "severity": severity,
                "code": _compact(item.get("code"), 60),
                "field": _compact(item.get("field"), 60) or "case",
                "problem": problem,
                "suggestion": _compact(item.get("suggestion"), 1000),
                "evidence": _compact(item.get("evidence"), 500),
            }
        )
    return issues[:12]


def review_case_workflow(
    client: LlmClient,
    case: dict,
    facts: dict,
    *,
    deterministic_issues: list[str] | None = None,
    deterministic_blocking: list[str] | None = None,
    quality_score: int = 100,
) -> dict:
    """工作流审核：模型给三态意见，最终分值和是否可执行由工作流规则裁定。"""
    payload = {
        "case": case,
        "requirementAndSourceFacts": _build_facts(facts),
        "deterministicIssues": deterministic_issues or [],
        "deterministicBlockingIssues": deterministic_blocking or [],
        "deterministicQualityScore": quality_score,
    }
    prompt = render_prompt(
        "case_review_workflow",
        {"REVIEW_PAYLOAD": json.dumps(payload, ensure_ascii=False, indent=2)},
    )
    data = client.chat_json([{"role": "user", "content": prompt}], max_tokens=4096, json_mode=True)
    return parse_workflow_review(
        data,
        case_id=str(case.get("id") or ""),
        deterministic_issues=deterministic_issues or [],
        quality_score=quality_score,
    )


def parse_workflow_review(
    data,
    *,
    case_id: str,
    deterministic_issues: list[str] | None = None,
    quality_score: int = 100,
) -> dict:
    if not isinstance(data, dict):
        raise LlmUnavailable("AI 用例审核返回结构非法")
    raw_status = str(data.get("review_status") or data.get("verdict") or "").strip().lower()
    status = _STATUS_ALIASES.get(raw_status, raw_status)
    if status not in WORKFLOW_STATUSES:
        status = "revise"
    issues = _parse_issues(data.get("issues"))
    if status == "pass" and any(item["severity"] in ("blocker", "warning") for item in issues):
        status = "revise"
    if status in ("revise", "reject") and not issues:
        issues.append(
            {
                "severity": "warning" if status == "revise" else "blocker",
                "field": "case",
                "problem": str(data.get("summary") or data.get("reason") or "AI 认为用例需要调整"),
                "suggestion": "按审核意见修复后重新审核",
                "evidence": "",
            }
        )
    missing = data.get("missing_coverage")
    suggestions = data.get("suggestions")
    return {
        "case_id": str(data.get("case_id") or case_id),
        "review_status": status,
        "quality_score": max(0, min(100, int(quality_score))),
        "issues": issues,
        "missing_coverage": [
            _compact(item, 300) for item in (missing if isinstance(missing, list) else []) if _compact(item, 300)
        ][:10],
        "suggestions": [
            _compact(item, 500) for item in (suggestions if isinstance(suggestions, list) else []) if _compact(item, 500)
        ][:10],
        "deterministic_issues": [str(item)[:1000] for item in (deterministic_issues or [])][:20],
        "source": "llm",
        "model": "",
    }
