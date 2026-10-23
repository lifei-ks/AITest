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

from pydantic import ValidationError

from app.agent.prompt_loader import render_prompt
from app.llm.client import LlmClient
from app.workflow.schemas import TestPointDraft

logger = logging.getLogger(__name__)

CATEGORIES = {"functional", "negative", "boundary", "state", "api", "permission"}
PRIORITIES = {"P0", "P1", "P2"}


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
    return validate_points(normalize_points(raw, "llm"))
