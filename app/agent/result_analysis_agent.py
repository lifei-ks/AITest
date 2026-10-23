"""Agent 4：执行结果分析（证据驱动，不修改真实执行状态）。

输入是一份由工作流组装的证据包，包含：
- 执行记录快照（状态、错误码、错误文本、耗时、步骤数）
- 每一步的结果/错误/选择器/截图路径
- 已有截图审核结论（visionReview）
- 需求和测试点摘要

约束：
- ``execution_status`` 永远由工作流用数据库快照回填，模型不参与。
- 模型或规则都不允许把「断言失败」直接升级为产品缺陷；分类必须能在证据里找到依据。
- LLM 不可用/输出非法时降级为保守规则，结果标记 source=rule。
"""
from __future__ import annotations

import json
import logging

from pydantic import ValidationError

from app.agent.prompt_loader import render_prompt
from app.llm.client import LlmClient, LlmUnavailable
from app.workflow.schemas import FailureAnalysisLLM, FAILURE_CATEGORIES

logger = logging.getLogger(__name__)

_DEFECT_CATEGORIES = {"product_defect", "test_script_issue", "test_data_issue", "environment_issue"}

_ACTION_BY_CATEGORY = {
    "product_defect": "保留失败截图和步骤作为缺陷证据，提交缺陷并附上预期/实际差异。",
    "test_script_issue": "检查并修复用例步骤、选择器、断言或输入方式，修复后重新执行。",
    "test_data_issue": "调整测试数据使其满足页面/接口约束，并补充对应的反例测试点。",
    "environment_issue": "先确认被测服务、浏览器和网络可用，再重新执行；必要时重试一次。",
    "unknown": "人工查看失败步骤、截图和录屏，补充证据后再分类。",
    "none": "无需处理。",
}

_REASON_BY_CATEGORY = {
    "product_defect": "存在违反预期的页面行为证据",
    "test_script_issue": "问题指向用例自身（步骤/选择器/断言）",
    "test_data_issue": "问题指向测试数据不满足业务约束",
    "environment_issue": "问题指向执行环境而非业务行为",
    "unknown": "证据不足或多因并存，无法可靠归类",
    "none": "执行通过且未发现异常证据",
}


def _steps_text(evidence: dict) -> list[str]:
    lines: list[str] = []
    for step in (evidence.get("steps") or [])[:20]:
        idx = step.get("index")
        parts = [f"步骤{idx}", f"类型={step.get('type')}", f"结果={step.get('result')}"]
        if step.get("action"):
            parts.append(f"动作={step['action']}")
        if step.get("selectorUsed"):
            parts.append(f"选择器={step['selectorUsed']}")
        if step.get("error"):
            parts.append(f"错误={step['error']}")
        lines.append("；".join(parts))
    return lines


def _review_evidence(evidence: dict) -> list[str]:
    review = evidence.get("visionReview") or {}
    lines: list[str] = []
    if review:
        lines.append(f"截图审核结论={review.get('verdict') or '无'}；原因={review.get('reason') or '无'}")
        for issue in (review.get("issues") or [])[:5]:
            if issue.get("problem"):
                lines.append(f"截图审核问题：{issue['problem']}")
    return lines


def build_rule_evidence(evidence: dict) -> list[str]:
    items = _steps_text(evidence)
    items.extend(_review_evidence(evidence))
    if evidence.get("errorCategory"):
        items.append(f"错误码 {evidence['errorCategory']}：{evidence.get('errorMessage') or '无错误文本'}")
    return items[:12]


def rule_analysis(evidence: dict) -> dict:
    """确定性兜底：宁可 unknown，也不把证据不足说成产品缺陷。"""
    status = str(evidence.get("executionStatus") or "unknown")
    category_code = str(evidence.get("errorCategory") or "")
    review = evidence.get("visionReview") or {}
    review_verdict = str(review.get("verdict") or "")
    evidence_items = build_rule_evidence(evidence)
    failed_steps = [step for step in (evidence.get("steps") or []) if step.get("result") == "failed"]
    failed_text = " ".join(
        f"{step.get('error') or ''} {step.get('selectorUsed') or ''} {step.get('action') or ''}"
        for step in failed_steps
    ).lower()
    script_markers = (
        "locator",
        "not an <input",
        "元素未找到",
        "输入框未找到",
        "strict mode violation",
        "selector",
        "placeholder",
        "等待元素",
    )
    env_markers = ("net::", "err_connection", "err_name", "连接被拒绝", "browser", "浏览器", "dns")
    looks_like_script_error = any(marker in failed_text for marker in script_markers) and not any(
        marker in failed_text for marker in env_markers
    )

    if status in ("passed", "cancelled"):
        if status == "cancelled":
            return {
                "failure_category": "none",
                "failure_reason": "执行被取消，没有产生可分析的失败证据。",
                "evidence": ["执行状态=cancelled"],
                "confidence": 1.0,
                "recommended_action": _ACTION_BY_CATEGORY["none"],
                "needs_human_review": False,
                "source": "rule",
            }
        if review_verdict == "suspect":
            return {
                "failure_category": "unknown",
                "failure_reason": "确定性步骤全部通过，但截图审核存在疑点，需人工确认。",
                "evidence": evidence_items or ["执行状态=passed", "截图审核=suspect"],
                "confidence": 0.4,
                "recommended_action": _ACTION_BY_CATEGORY["unknown"],
                "needs_human_review": True,
                "source": "rule",
            }
        return {
            "failure_category": "none",
            "failure_reason": "执行状态为 passed，且没有异常证据。",
            "evidence": ["执行状态=passed"],
            "confidence": 0.9,
            "recommended_action": _ACTION_BY_CATEGORY["none"],
            "needs_human_review": False,
            "source": "rule",
        }

    if looks_like_script_error and category_code in ("ENV_ERROR", "SELECTOR_NOT_FOUND", "TIMEOUT"):
        return {
            "failure_category": "test_script_issue",
            "failure_reason": "失败发生在元素定位/输入阶段（执行器给出的错误文本指向 locator/元素类型不匹配），更可能是用例选择器或测试数据问题，而不是环境不可用。",
            "evidence": evidence_items or [f"错误码 {category_code}"],
            "confidence": 0.7,
            "recommended_action": _ACTION_BY_CATEGORY["test_script_issue"],
            "needs_human_review": True,
            "source": "rule",
        }
    if category_code == "CASE_INVALID":
        return {
            "failure_category": "test_script_issue",
            "failure_reason": "执行器判定用例无效（缺少结构化步骤、输入值或选择器），属于用例自身问题。",
            "evidence": evidence_items or ["错误码 CASE_INVALID"],
            "confidence": 0.95,
            "recommended_action": _ACTION_BY_CATEGORY["test_script_issue"],
            "needs_human_review": False,
            "source": "rule",
        }
    if category_code == "ENV_ERROR":
        return {
            "failure_category": "environment_issue",
            "failure_reason": "执行器报告环境错误（浏览器/驱动/网络等），不是业务断言结果。",
            "evidence": evidence_items or ["错误码 ENV_ERROR"],
            "confidence": 0.85,
            "recommended_action": _ACTION_BY_CATEGORY["environment_issue"],
            "needs_human_review": False,
            "source": "rule",
        }
    if category_code == "NAVIGATION_FAILED":
        return {
            "failure_category": "environment_issue",
            "failure_reason": "页面无法打开或目标地址返回错误，优先排查环境/服务，而非直接判产品缺陷。",
            "evidence": evidence_items or ["错误码 NAVIGATION_FAILED"],
            "confidence": 0.7,
            "recommended_action": _ACTION_BY_CATEGORY["environment_issue"],
            "needs_human_review": True,
            "source": "rule",
        }
    if category_code == "TIMEOUT":
        return {
            "failure_category": "environment_issue",
            "failure_reason": "步骤超时，可能是环境慢、服务无响应或页面行为异常，证据不足以归为产品缺陷。",
            "evidence": evidence_items or ["错误码 TIMEOUT"],
            "confidence": 0.45,
            "recommended_action": _ACTION_BY_CATEGORY["environment_issue"],
            "needs_human_review": True,
            "source": "rule",
        }
    if category_code == "SELECTOR_NOT_FOUND":
        return {
            "failure_category": "test_script_issue",
            "failure_reason": "执行器找不到用例指定的元素，优先检查选择器/页面结构是否变化。",
            "evidence": evidence_items or ["错误码 SELECTOR_NOT_FOUND"],
            "confidence": 0.55,
            "recommended_action": _ACTION_BY_CATEGORY["test_script_issue"],
            "needs_human_review": True,
            "source": "rule",
        }
    if category_code == "ASSERT_FAILED":
        return {
            "failure_category": "unknown",
            "failure_reason": "断言未命中预期文案，可能是产品缺陷，也可能是选择器/断言写错；证据不足以直接定性。",
            "evidence": evidence_items or ["错误码 ASSERT_FAILED"],
            "confidence": 0.35,
            "recommended_action": _ACTION_BY_CATEGORY["unknown"],
            "needs_human_review": True,
            "source": "rule",
        }
    return {
        "failure_category": "unknown",
        "failure_reason": "没有足够证据判断失败原因。",
        "evidence": evidence_items[:6],
        "confidence": 0.2 if evidence_items else 0.0,
        "recommended_action": _ACTION_BY_CATEGORY["unknown"],
        "needs_human_review": True,
        "source": "rule",
    }


def _has_grounded_evidence(items: list[str], evidence: dict) -> bool:
    """声称为真实缺陷/脚本问题时，至少引用一个输入里真实存在的证据。"""
    if not items:
        return False
    step_indexes = {str(step.get("index")) for step in (evidence.get("steps") or [])}
    error_code = str(evidence.get("errorCategory") or "")
    texts = [str(x) for x in (evidence.get("expectedResults") or [])]
    review = evidence.get("visionReview") or {}
    for item in items:
        text = str(item)
        if any(f"步骤{idx}" in text or f"步骤 {idx}" in text for idx in step_indexes):
            return True
        if error_code and error_code in text:
            return True
        if review.get("verdict") and review["verdict"] in text:
            return True
        if any(expect and expect in text for expect in texts):
            return True
    return False


def guard_analysis(result: dict, evidence: dict) -> dict:
    """对 LLM/规则统一做保守性校正。"""
    status = str(evidence.get("executionStatus") or "unknown")
    category = str(result.get("failure_category") or "unknown")
    if category not in FAILURE_CATEGORIES:
        category = "unknown"
    items = [str(item) for item in (result.get("evidence") or []) if str(item).strip()][:20]
    review = evidence.get("visionReview") or {}

    if status == "cancelled":
        category = "none"
    elif status == "passed":
        category = "unknown" if str(review.get("verdict") or "") == "suspect" else "none"
    elif category == "none":
        # 非通过状态不能声称 none
        category = "unknown"

    if category in _DEFECT_CATEGORIES and not _has_grounded_evidence(items, evidence):
        category = "unknown"
        items = items + ["证据未引用具体步骤/错误码/截图结论，已降级为 unknown"]
        result["failure_reason"] = "原分类缺少可核对的执行证据，已降级为 unknown。"

    try:
        confidence = max(0.0, min(1.0, float(result.get("confidence") or 0.0)))
    except (TypeError, ValueError):
        confidence = 0.0
    if category == "unknown":
        confidence = min(confidence, 0.5)
    if not items:
        confidence = min(confidence, 0.3)

    needs_review = bool(result.get("needs_human_review", True))
    if category in ("product_defect", "unknown"):
        needs_review = True
    if category == "none":
        needs_review = False if status in ("passed", "cancelled") else needs_review

    return {
        "failure_category": category,
        "failure_reason": str(result.get("failure_reason") or _REASON_BY_CATEGORY.get(category, ""))[:2000],
        "evidence": items[:10],
        "confidence": round(confidence, 3),
        "recommended_action": str(
            result.get("recommended_action") or _ACTION_BY_CATEGORY.get(category, "")
        )[:1000],
        "needs_human_review": needs_review,
        "execution_status": status,
        "source": str(result.get("source") or "rule"),
        "model": str(result.get("model") or ""),
    }


def analyze(client: LlmClient | None, evidence: dict) -> dict:
    """优先 LLM；不可用/结构非法时降级规则，并显式记录 source。"""
    if client is not None and client.available():
        try:
            prompt = render_prompt(
                "result_analysis",
                {"EVIDENCE_JSON": json.dumps(evidence, ensure_ascii=False, indent=2)},
            )
            raw = client.chat_json([{"role": "user", "content": prompt}], max_tokens=2048, json_mode=True)
            parsed = FailureAnalysisLLM.model_validate(raw).model_dump()
            parsed["source"] = "llm"
            parsed["model"] = client.model
            # execution_status 始终由工作流回填，模型输出中的同名字段会被忽略
            parsed.pop("execution_status", None)
            return guard_analysis(parsed, evidence)
        except (LlmUnavailable, ValidationError, ValueError) as exc:
            logger.warning("结果分析 LLM 不可用，降级规则: %s", exc)
    return guard_analysis(rule_analysis(evidence), evidence)
