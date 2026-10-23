"""Agent 2：可执行测试用例生成（提示词外置）。

职责边界：
- 只负责组提示词、调用 LLM、把响应收敛为 dict 列表。
- 语义校验（测试点 id 必须匹配、单输入单断言、必填字段、选择器事实校验）
  仍由 generation_service / quality_gate 完成，避免出现两套判定标准。
- 用例是否可自动化由工作流层根据 structuredSteps 判定。
"""
from __future__ import annotations

import json

from app.agent.prompt_loader import render_prompt
from app.llm.client import LlmClient


def build_points_prompt(
    *,
    requirement_version: str,
    requirement_name: str,
    points: list[dict],
    facts: dict,
) -> str:
    payload = [
        {
            "testPointId": p["id"],
            "title": p["title"],
            "category": p["category"],
            "priority": p["priority"],
            "description": p["description"],
            "pageRoute": p["pageRoute"],
            "endpoint": p["endpoint"],
            "fields": p["fields"],
            "expected": p["expected"],
        }
        for p in points
    ]
    return render_prompt(
        "test_cases_by_point",
        {
            "REQUIREMENT_VERSION": requirement_version,
            "REQUIREMENT_NAME": requirement_name,
            "POINTS_JSON": json.dumps(payload, ensure_ascii=False),
            "FACTS_JSON": json.dumps(facts, ensure_ascii=False),
        },
    )


def _as_case_list(raw) -> list:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        value = raw.get("testCases") or raw.get("cases") or raw.get("data") or []
        return value if isinstance(value, list) else []
    return []


def generate_for_points(
    client: LlmClient,
    *,
    requirement_version: str,
    requirement_name: str,
    points: list[dict],
    facts: dict,
    max_tokens: int = 8192,
) -> list[dict]:
    prompt = build_points_prompt(
        requirement_version=requirement_version,
        requirement_name=requirement_name,
        points=points,
        facts=facts,
    )
    raw = client.chat_json([{"role": "user", "content": prompt}], max_tokens=max_tokens, json_mode=True)
    return [item for item in _as_case_list(raw) if isinstance(item, dict)]


def generate_project_cases(client: LlmClient, facts: dict, max_tokens: int = 8192) -> list[dict]:
    prompt = render_prompt(
        "project_cases",
        {"FACTS_JSON": json.dumps(facts, ensure_ascii=False)},
    )
    raw = client.chat_json([{"role": "user", "content": prompt}], max_tokens=max_tokens, json_mode=True)
    return [item for item in _as_case_list(raw) if isinstance(item, dict)]
