"""LLM 客户端（OpenAI 兼容协议），失败即降级。

任何异常（无 Key / 超时 / 429 / JSON 非法）→ LlmUnavailable，调用方降级规则模式。
"""
from __future__ import annotations

import json
import logging
import re

import httpx

logger = logging.getLogger(__name__)


class LlmUnavailable(Exception):
    """LLM 不可用（无 Key / 超时 / 限流 / 返回非法），调用方应降级。"""


class LlmClient:
    def __init__(self, api_key: str = "", base_url: str = "", model: str = ""):
        self.api_key = api_key
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.model = model or "gpt-4o-mini"

    def available(self) -> bool:
        return bool(self.api_key)

    def chat_json(self, messages: list[dict], max_tokens: int = 8192, json_mode: bool = False):
        """发起一次对话并解析为 JSON（返回 dict 或 list）。失败抛 LlmUnavailable。

        json_mode=True 时使用 OpenAI 兼容的 response_format={"type":"json_object"}，
        可显著降低长 JSON 输出被截断/夹带解释的概率；若服务端不支持则自动重试普通模式。
        """
        content = self._chat(messages, max_tokens, json_mode)
        try:
            return self._parse_json(content)
        except LlmUnavailable:
            # JSON 模式仍不合法时，普通模式再试一次
            if json_mode:
                content = self._chat(messages, max_tokens, False)
                return self._parse_json(content)
            raise

    def _chat(self, messages: list[dict], max_tokens: int, json_mode: bool) -> str:
        if not self.available():
            raise LlmUnavailable("未配置 LLM_API_KEY")
        url = f"{self.base_url}/chat/completions"
        payload = {"model": self.model, "messages": messages, "temperature": 0.2, "max_tokens": max_tokens}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        try:
            resp = httpx.post(url, json=payload, headers=headers, timeout=180)
        except Exception as e:  # noqa: BLE001
            raise LlmUnavailable(f"LLM 请求失败: {e}") from e

        if resp.status_code == 400 and json_mode:
            # 部分兼容服务不支持 response_format，退回普通模式
            logger.info("LLM 不支持 json_object，回退普通模式")
            return self._chat(messages, max_tokens, False)
        if resp.status_code == 429:
            raise LlmUnavailable("LLM 限流 429")
        if resp.status_code != 200:
            raise LlmUnavailable(f"LLM HTTP {resp.status_code}: {resp.text[:200]}")

        try:
            body = resp.json()
            message = body["choices"][0]["message"]
            content = (message.get("content") or "").strip()
            # reasoning 模型：content 可能为空，但 reasoning_content 有内容；此时视为未产出答案
            if not content:
                content = message.get("reasoning_content") or ""
        except (KeyError, IndexError, ValueError) as e:
            raise LlmUnavailable(f"LLM 响应结构非法: {e}") from e

        if not content:
            raise LlmUnavailable("LLM 返回空内容")
        return content

    @staticmethod
    def _parse_json(content: str):
        """从 LLM 输出里解析 JSON（容忍 markdown、多余文字、尾逗号与截断数组）。"""
        content = content.strip()
        # 去掉 ```json ... ``` 包裹
        m = re.search(r"```(?:json)?\s*(.*?)\s*```", content, re.DOTALL)
        if m:
            content = m.group(1)
        # 提取第一个 JSON 值（对象 { } 或数组 [ ]）
        m = re.search(r"(\{.*\}|\[.*\])", content, re.DOTALL)
        if m:
            content = m.group(1)
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass

        # 常见修复 1：尾逗号
        fixed = re.sub(r",\s*([}\]])", r"\1", content)
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass

        # 常见修复 2：输出被 max_tokens 截断 —— 截到最后一个完整对象再闭合数组
        if fixed.lstrip().startswith("["):
            idx = fixed.rfind("}")
            if idx > 0:
                try:
                    return json.loads(fixed[: idx + 1] + "]")
                except json.JSONDecodeError:
                    pass
        raise LlmUnavailable("LLM 返回非 JSON（可能被截断）")
