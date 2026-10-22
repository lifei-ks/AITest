"""执行领域枚举及中文说明。

数据库仍存储稳定英文值，避免中文展示文案变化影响历史数据；接口会同时返回
code、label、description 和 tag，前端与报告使用同一份定义。
"""
from __future__ import annotations


CASE_EXECUTION_STATUS_META: dict[str, dict] = {
    "not_run": {
        "label": "未执行",
        "tag": "info",
        "description": "用例尚未产生任何执行记录。",
    },
    "running": {
        "label": "执行中",
        "tag": "primary",
        "description": "用例正在真实浏览器中执行，尚无最终结论。",
    },
    "passed": {
        "label": "通过",
        "tag": "success",
        "description": "确定性检查全部通过，或证据经 AI/人工复核确认符合预期。",
    },
    "failed": {
        "label": "失败",
        "tag": "danger",
        "description": "出现确定错误，或人工复核确认结果不符合预期。",
    },
    "skipped": {
        "label": "跳过",
        "tag": "warning",
        "description": "存在无法验证的步骤，没有足够证据判为通过或失败。",
    },
    "needs_review": {
        "label": "待复核",
        "tag": "warning",
        "description": "证据不足、AI 判断有疑点或审核不可用，需要人工确认。",
    },
    "cancelled": {
        "label": "已取消",
        "tag": "info",
        "description": "用户中止执行，不能视为通过。",
    },
}

# 执行记录创建后即处于 running，因此没有 not_run。
EXECUTION_RECORD_STATUS_META: dict[str, dict] = {
    key: value for key, value in CASE_EXECUTION_STATUS_META.items() if key != "not_run"
}

AI_REVIEW_VERDICT_META: dict[str, dict] = {
    "ok": {
        "label": "证据支持预期",
        "tag": "success",
        "description": "关键截图和步骤记录明确支持用例预期；不会覆盖确定性执行失败。",
    },
    "suspect": {
        "label": "疑似异常",
        "tag": "danger",
        "description": "截图存在异常、与预期不符，或证据不足以证明预期。",
    },
    "unavailable": {
        "label": "审核不可用",
        "tag": "info",
        "description": "模型未配置、调用失败、返回结构非法或没有可用截图。",
    },
}

ERROR_CATEGORY_META: dict[str, dict] = {
    "SELECTOR_NOT_FOUND": {
        "label": "元素未找到",
        "tag": "warning",
        "description": "执行器已尝试选择器和文本兜底链，仍无法定位可操作元素。",
    },
    "ASSERT_FAILED": {
        "label": "断言失败",
        "tag": "danger",
        "description": "页面地址或引号锚定文案没有满足用例预期。",
    },
    "NAVIGATION_FAILED": {
        "label": "页面导航失败",
        "tag": "danger",
        "description": "页面无法打开，或目标地址返回 4xx/5xx。",
    },
    "TIMEOUT": {
        "label": "步骤超时",
        "tag": "warning",
        "description": "页面操作或等待超过执行器默认超时时间。",
    },
    "CASE_INVALID": {
        "label": "用例无效",
        "tag": "warning",
        "description": "用例缺少结构化步骤、输入值、选择器等执行必需字段。",
    },
    "ENV_ERROR": {
        "label": "环境错误",
        "tag": "info",
        "description": "浏览器启动失败、驱动异常或其他非业务执行环境问题。",
    },
}

CASE_EXECUTION_STATUSES = tuple(CASE_EXECUTION_STATUS_META)
EXECUTION_RECORD_STATUSES = tuple(EXECUTION_RECORD_STATUS_META)
AI_REVIEW_VERDICTS = tuple(AI_REVIEW_VERDICT_META)
ERROR_CATEGORIES = tuple(ERROR_CATEGORY_META)


def _meta(mapping: dict[str, dict], code: str | None) -> dict:
    item = mapping.get(code or "", {})
    return {
        "code": code or None,
        "label": item.get("label") or (code or "未分类"),
        "description": item.get("description") or "",
        "tag": item.get("tag") or "info",
    }


def case_status_meta(code: str | None) -> dict:
    return _meta(CASE_EXECUTION_STATUS_META, code)


def execution_status_meta(code: str | None) -> dict:
    return _meta(EXECUTION_RECORD_STATUS_META, code)


def review_verdict_meta(code: str | None) -> dict:
    return _meta(AI_REVIEW_VERDICT_META, code)


def error_category_meta(code: str | None) -> dict:
    return _meta(ERROR_CATEGORY_META, code)


def execution_enum_payload() -> dict:
    """供前端和外部调用方读取完整的枚举值与中文含义。"""
    return {
        "caseExecutionStatuses": CASE_EXECUTION_STATUS_META,
        "executionRecordStatuses": EXECUTION_RECORD_STATUS_META,
        "aiReviewVerdicts": AI_REVIEW_VERDICT_META,
        "errorCategories": ERROR_CATEGORY_META,
    }
