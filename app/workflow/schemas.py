"""工作流与 Agent 的结构化输入/输出契约。

这里只做“边界校验”，不重复实现生成器/执行器逻辑：
- Agent 输出进入业务前必须过 Pydantic；
- 用例审核结果同时包含 LLM 建议和确定性校验，质量分不由模型单独决定；
- 结果分析类别是有限枚举，未知证据一律 unknown，不冒充产品缺陷。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

TEST_POINT_TYPES = ("functional", "negative", "boundary", "state", "api", "permission")
PRIORITIES = ("P0", "P1", "P2")
REVIEW_STATUSES = ("pass", "revise", "reject")
FAILURE_CATEGORIES = (
    "product_defect",
    "test_script_issue",
    "test_data_issue",
    "environment_issue",
    "unknown",
    "none",
)
AUTOMATION_STATUSES = ("automated", "manual", "blocked")


class WorkflowCreate(BaseModel):
    requirementId: str
    mode: Literal["auto", "rule", "llm"] = "auto"
    autoExecute: bool = True
    maxRevisions: int = Field(default=2, ge=0, le=2)
    maxCases: int = Field(default=20, ge=1, le=100)
    reuseExistingCases: bool = True
    reanalyzePoints: bool = False
    targetUrl: str | None = None
    # Pipeline：缺分析时自动跑静态分析；已有成功分析默认复用
    analyzeCode: bool = True
    reanalyzeCode: bool = False
    # 实验/调试开关：不改变默认闭环行为
    reviewEnabled: bool = True
    analyzeEnabled: bool = True


class TestPointDraft(BaseModel):
    """测试点落库前的结构校验。

    数据库沿用 category / expected 字段；对外 API 会映射为 test_type / expected_behavior。
    """

    title: str = Field(min_length=1, max_length=255)
    description: str = ""
    category: Literal["functional", "negative", "boundary", "state", "api", "permission"] = "functional"
    priority: Literal["P0", "P1", "P2"] = "P1"
    expected: str = ""
    pageRoute: str = ""
    endpoint: str = ""
    fields: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)

    @field_validator("fields", mode="before")
    @classmethod
    def _coerce_fields(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return value

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.7


class ReviewIssue(BaseModel):
    severity: Literal["blocker", "warning", "info"] = "warning"
    field: str = "case"
    problem: str
    suggestion: str = ""
    evidence: str = ""


class CaseReviewResult(BaseModel):
    """用例审核 Agent 的最终结构化结果（3 个状态，不静默丢弃）。"""

    case_id: str
    review_status: Literal["pass", "revise", "reject"]
    quality_score: int = Field(ge=0, le=100)
    issues: list[ReviewIssue] = Field(default_factory=list)
    missing_coverage: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    deterministic_issues: list[str] = Field(default_factory=list)
    source: Literal["llm", "deterministic", "mixed"] = "deterministic"
    model: str = ""


class ResultAnalysisResult(BaseModel):
    """结果分析 Agent 的输出版本；execution_status 是客观快照，不允许模型改写。"""

    case_id: str
    execution_status: str
    failure_category: Literal[
        "product_defect",
        "test_script_issue",
        "test_data_issue",
        "environment_issue",
        "unknown",
        "none",
    ] = "unknown"
    failure_reason: str = ""
    evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    recommended_action: str = ""
    needs_human_review: bool = True
    source: Literal["llm", "rule"] = "rule"
    model: str = ""

    @field_validator("evidence", mode="before")
    @classmethod
    def _coerce_evidence(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            return [f"{k}: {v}" for k, v in value.items()]
        out: list[str] = []
        for item in value if isinstance(value, list) else []:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                out.append("；".join(f"{k}={v}" for k, v in item.items()))
            else:
                out.append(str(item))
        return out[:20]

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, value):
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return 0.0


class FailureAnalysisLLM(BaseModel):
    """模型原始输出的最小校验；执行状态由代码回填，模型不得提供/覆盖。"""

    failure_category: str = "unknown"
    failure_reason: str = ""
    evidence: list = Field(default_factory=list)
    confidence: float = 0.0
    recommended_action: str = ""
    needs_human_review: bool = True

    @field_validator("failure_category", mode="before")
    @classmethod
    def _category(cls, value):
        text = str(value or "unknown").strip().lower()
        aliases = {
            "product": "product_defect",
            "bug": "product_defect",
            "功能缺陷": "product_defect",
            "script": "test_script_issue",
            "test_script": "test_script_issue",
            "脚本问题": "test_script_issue",
            "data": "test_data_issue",
            "测试数据": "test_data_issue",
            "environment": "environment_issue",
            "env": "environment_issue",
            "环境问题": "environment_issue",
            "none": "none",
            "通过": "none",
        }
        text = aliases.get(text, text)
        return text if text in FAILURE_CATEGORIES else "unknown"
