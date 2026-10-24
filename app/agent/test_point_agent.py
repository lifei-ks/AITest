"""Agent 1：需求测试点生成。

边界：
- 输入：需求文本 + 代码事实；输出：经过结构校验的测试点列表。
- 不负责落库、线程和降级决策，这些仍由 requirement_service 控制；
  这样现有「需求版本解析」接口的行为保持不变。
- 提示词外置在 ``app/agent/prompts/test_points.txt``。
"""
from __future__ import annotations

import json
import logging
import re

from pydantic import ValidationError

from app.agent.prompt_loader import render_prompt
from app.llm.client import LlmClient
from app.workflow.schemas import TestPointDraft

logger = logging.getLogger(__name__)

CATEGORIES = {"functional", "negative", "boundary", "state", "api", "permission"}
PRIORITIES = {"P0", "P1", "P2"}

# 静态页面结构/固定数量不是业务测试点：
# - “新建订单页展示5个表单字段”
# - “列表页默认展示4条种子订单”
# 这类数量会随页面/数据变化，应该由业务场景和运行时基线覆盖。
STATIC_STRUCTURE_RE = re.compile(
    r"(?:展示|显示|包含|含有|共|默认展示|默认显示)\s*\d+\s*(?:个|条|项|列|行)\s*"
    r"[^，。；,;]{0,8}(?:字段|输入项|输入框|按钮|选项|列|行|订单|记录|数据)"
)
BARE_FIELD_COUNT_RE = re.compile(
    r"\d+\s*(?:个|条|项)\s*(?:表单)?(?:字段|输入项|输入框|按钮|选项)"
)
BARE_STATUS_LABELS = {
    "待支付", "已支付", "已发货", "已完成", "已取消", "已退款",
    "支付", "发货", "完成", "取消", "退款", "查询", "重置", "提交",
    "新建订单", "返回列表",
}
STRUCTURAL_TITLE_WORDS = ("第一行", "排序", "顺序", "位置", "排在", "操作列", "按钮", "展示", "显示")


def is_static_structure_point(point: dict) -> bool:
    text = f"{point.get('title') or ''} {point.get('description') or ''}"
    if STATIC_STRUCTURE_RE.search(text) or BARE_FIELD_COUNT_RE.search(text):
        return True
    title = str(point.get("title") or "")
    expected = str(point.get("expected") or "").strip().strip("「」『』")
    if any(word in title for word in STRUCTURAL_TITLE_WORDS) and expected in BARE_STATUS_LABELS:
        return True
    return False


def filter_static_structure_points(points: list[dict]) -> list[dict]:
    kept: list[dict] = []
    for point in points:
        if is_static_structure_point(point):
            logger.info("丢弃静态结构/固定数量测试点: %s", point.get("title"))
            continue
        kept.append(point)
    return kept


def build_prompt(
    *,
    requirement_version: str,
    requirement_name: str,
    content: str,
    facts: dict,
) -> str:
    return render_prompt(
        "test_points",
        {
            "REQUIREMENT_VERSION": requirement_version,
            "REQUIREMENT_NAME": requirement_name,
            "REQUIREMENT_CONTENT": content,
            "FACTS_JSON": json.dumps(facts, ensure_ascii=False),
        },
    )


def normalize_points(raw, source: str) -> list[dict]:
    """兼容 dict/list 输出并做字段裁剪；与原 requirement_service 逻辑保持一致。"""
    if isinstance(raw, dict):
        raw = raw.get("testPoints") or raw.get("points") or raw.get("data") or []
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for rc in raw[:40]:
        if not isinstance(rc, dict):
            continue
        title = str(rc.get("title") or "").strip()
        if not title or title in seen:
            continue
        seen.add(title)
        cat = str(rc.get("category") or "functional").strip().lower()
        pri = str(rc.get("priority") or "P1").strip().upper()
        fields = rc.get("fields") or []
        if not isinstance(fields, list):
            fields = [str(fields)]
        try:
            confidence = float(rc.get("confidence"))
        except (TypeError, ValueError):
            confidence = 0.85 if source == "llm" else 0.7
        out.append(
            {
                "title": title[:255],
                "category": cat if cat in CATEGORIES else "functional",
                "priority": pri if pri in PRIORITIES else "P1",
                "description": str(rc.get("description") or "")[:2000],
                "pageRoute": str(rc.get("pageRoute") or rc.get("page_route") or "")[:255],
                "endpoint": str(rc.get("endpoint") or "")[:255],
                "fields": [str(f)[:80] for f in fields][:20],
                "expected": str(rc.get("expected") or "")[:1000],
                "source": source,
                "confidence": confidence,
            }
        )
    return out


def validate_points(points: list[dict]) -> list[dict]:
    """Pydantic 结构校验；非法条目丢弃并告警，不把坏数据写入数据库。"""
    valid: list[dict] = []
    for index, point in enumerate(points, start=1):
        try:
            TestPointDraft.model_validate(point)
        except ValidationError as exc:
            logger.warning("测试点 #%s 结构非法，已丢弃: %s", index, exc.errors()[:2])
            continue
        valid.append(point)
    return valid


def extract_points(
    client: LlmClient,
    *,
    requirement_version: str,
    requirement_name: str,
    content: str,
    facts: dict,
) -> list[dict]:
    """调用 LLM 抽取测试点；LLM 不可用时由调用方降级规则模式。"""
    prompt = build_prompt(
        requirement_version=requirement_version,
        requirement_name=requirement_name,
        content=content,
        facts=facts,
    )
    raw = client.chat_json([{"role": "user", "content": prompt}], max_tokens=8192, json_mode=True)
    return filter_static_structure_points(validate_points(normalize_points(raw, "llm")))
