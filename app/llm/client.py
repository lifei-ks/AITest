"""LLM 客户端（OpenAI 兼容协议），失败即降级。

兼容不同供应商/模型的响应形态：
- content 可能是字符串，也可能是 [{type:text,text:...}] 分片；
- 推理模型可能把最终答案放在 content，也可能只留在 reasoning_content；
- 推理模型可能因为 max_tokens 被思考过程占满而返回 finish_reason=length；
- GLM-5.3 / 5.3-flash 无法关闭思考，但支持 reasoning_effort=low，客户端自动降档；
- 供应商不支持的扩展参数会触发 400/422，客户端自动去掉后重试。

任何无法恢复的异常（无 Key / 超时 / 429 / 无最终答案 / JSON 非法）→ LlmUnavailable，
由调用方决定是否降级规则模式。
"""
from __future__ import annotations

import json
import logging
import re

import httpx

logger = logging.getLogger(__name__)

REASONING_KEYS = ("reasoning_content", "reasoning", "thinking", "analysis")
JSON_KEYS_HINT = {"testCases", "cases", "points", "testPoints", "patch", "verdict", "review_status"}


class LlmUnavailable(Exception):
    """LLM 不可用（无 Key / 超时 / 限流 / 返回非法），调用方应降级。"""


def _content_to_text(value) -> str:
    """把 OpenAI 兼容的 content 统一成字符串；兼容字符串、分片数组和对象。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                text = item
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content") or item.get("summary") or ""
            else:
                text = ""
            text = str(text).strip()
            if text:
                parts.append(text)
        return "\n".join(parts).strip()
    if isinstance(value, dict):
        text = value.get("text") or value.get("content") or ""
        return str(text).strip() if isinstance(text, (str, int, float)) else ""
    return str(value).strip()


def _balanced_json_candidates(text: str) -> list[str]:
    """从夹杂解释的文本中提取平衡的 JSON 片段，避免贪婪正则跨越多个对象。"""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] not in "{[":
            i += 1
            continue
        start = i
        stack: list[str] = []
        in_string = False
        escaped = False
        j = i
        closed = False
        while j < n:
            ch = text[j]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
            else:
                if ch == '"':
                    in_string = True
                elif ch in "{[":
                    stack.append(ch)
                elif ch in "}]":
                    if stack:
                        stack.pop()
                        if not stack:
                            out.append(text[start : j + 1])
                            i = j
                            closed = True
                            break
            j += 1
        if not closed:
            # 未闭合的片段交给截断修复逻辑，不在这里展开
            i = start + 1
            continue
        i += 1
        if len(out) >= 50:
            break
    return out


def _loads_with_repair(text: str):
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 常见修复 1：尾逗号
    fixed = re.sub(r",\s*([}\]])", r"\1", text)
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
    # 常见修复 3：对象末尾被截断 —— 截到最后一个完整字段后闭合对象
    if fixed.lstrip().startswith("{"):
        idx = fixed.rfind("}")
        if idx > 0:
            try:
                return json.loads(fixed[: idx + 1])
            except json.JSONDecodeError:
                pass
    return None


class LlmClient:
    def __init__(
        self,
        api_key: str = "",
        base_url: str = "",
        model: str = "",
        extra_body: dict | None = None,
    ):
        self.api_key = api_key
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.model = model or "gpt-4o-mini"
        self.extra_body = dict(extra_body or {})
        self.last_content = ""
        self.last_reasoning_content = ""
        self.last_finish_reason = ""
        self.last_usage: dict = {}
        self.last_text_source = ""

    def available(self) -> bool:
        return bool(self.api_key)

    def _request_extras(self) -> dict:
        """按模型能力补充请求参数；显式 extra_body 优先级最高。"""
        extras: dict = {}
        model = (self.model or "").lower()
        if ("glm-5.3" in model or "glm-5.2" in model) and "reasoning_effort" not in self.extra_body:
            # GLM-5.3/5.3-flash 强制思考且默认 max。长 JSON 任务会把 max_tokens
            # 全部花在 reasoning_content 上，导致 content 为空、finish_reason=length。
            extras["reasoning_effort"] = "low"
        extras.update(self.extra_body)
        return extras

    def chat_text(self, messages: list[dict], max_tokens: int = 256) -> str:
        """连通性/轻量调用；content 为空时允许把 reasoning 作为“有响应”的证据。"""
        text, _source = self._chat_raw(messages, max_tokens, False)
        if not text:
            raise LlmUnavailable(f"LLM 未返回内容（{self._detail()}）")
        return text

    def chat_json(self, messages: list[dict], max_tokens: int = 8192, json_mode: bool = False):
        """发起一次对话并解析为 JSON（返回 dict 或 list）。失败抛 LlmUnavailable。

        兼容策略：
        1. content 优先；content 为空时，若 reasoning_content 本身是合法 JSON 也接受；
        2. finish_reason=length 时提升 token 预算重试一次；
        3. json_mode 失败时退回普通模式（部分供应商不支持 response_format）；
        4. 供应商不支持扩展参数（400/422）时自动去掉重试。
        """
        text, _source = self._chat_raw(messages, max_tokens, json_mode)
        parsed = self._try_parse(text)
        if parsed is not None:
            return parsed

        # 思考过程吃满 token：提升预算后重试一次，避免直接降级规则。
        if self.last_finish_reason == "length" and max_tokens < 32768:
            bigger = min(max(max_tokens * 2, 8192), 32768)
            logger.warning(
                "LLM 输出被截断（%s），提升 max_tokens %s -> %s 重试",
                self._detail(),
                max_tokens,
                bigger,
            )
            text, _source = self._chat_raw(messages, bigger, json_mode)
            parsed = self._try_parse(text)
            if parsed is not None:
                return parsed

        # JSON 模式不合法时，普通模式再试一次（部分兼容服务只在普通模式返回正文）
        if json_mode:
            text, _source = self._chat_raw(messages, max_tokens, False)
            parsed = self._try_parse(text)
            if parsed is not None:
                return parsed

        if not text:
            raise LlmUnavailable(f"LLM 未返回最终答案（{self._detail()}）")
        raise LlmUnavailable(f"LLM 返回内容无法解析为 JSON（{self._detail()}）")

    def _chat_raw(self, messages: list[dict], max_tokens: int, json_mode: bool) -> tuple[str, str]:
        """返回 (文本, 来源)。来源为 content / reasoning_content / choice_text。"""
        if not self.available():
            raise LlmUnavailable("未配置 LLM_API_KEY")
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": max_tokens,
        }
        resp = self._request(payload, json_mode)
        if resp.status_code == 429:
            raise LlmUnavailable("LLM 限流 429")
        if resp.status_code != 200:
            raise LlmUnavailable(f"LLM HTTP {resp.status_code}: {resp.text[:200]}")

        try:
            body = resp.json()
            choice = body["choices"][0]
            message = choice.get("message") or {}
            if not isinstance(message, dict):
                raise TypeError("message 不是对象")
        except (KeyError, IndexError, ValueError, TypeError) as e:
            raise LlmUnavailable(f"LLM 响应结构非法: {e}") from e

        self.last_finish_reason = str(choice.get("finish_reason") or "")
        usage = body.get("usage")
        self.last_usage = usage if isinstance(usage, dict) else {}
        content = _content_to_text(message.get("content"))
        self.last_content = content
        self.last_reasoning_content = ""
        for key in REASONING_KEYS:
            reasoning = _content_to_text(message.get(key))
            if reasoning:
                self.last_reasoning_content = reasoning
                break
        if content:
            self.last_text_source = "content"
            return content, "content"
        if self.last_reasoning_content:
            self.last_text_source = "reasoning_content"
            return self.last_reasoning_content, "reasoning_content"
        # 少数供应商使用 choices[0].text / output_text
        alt = _content_to_text(choice.get("text")) or _content_to_text(body.get("output_text"))
        if alt:
            self.last_text_source = "choice_text"
            return alt, "choice_text"
        self.last_text_source = ""
        return "", ""

    def _request(self, payload: dict, json_mode: bool):
        """发送请求；400/422 时依次回退 json_mode 和扩展参数。"""
        extras = self._request_extras()
        attempts: list[tuple[bool, dict]] = []
        if json_mode:
            attempts.append((True, extras))
        attempts.append((False, extras))
        if extras:
            attempts.append((False, {}))
        last_resp = None
        for index, (use_json, body_extras) in enumerate(attempts):
            body = dict(payload)
            if use_json:
                body["response_format"] = {"type": "json_object"}
            body.update(body_extras)
            try:
                resp = httpx.post(
                    f"{self.base_url}/chat/completions",
                    json=body,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    timeout=240,
                )
            except Exception as e:  # noqa: BLE001
                raise LlmUnavailable(f"LLM 请求失败: {e}") from e
            last_resp = resp
            if resp.status_code in (400, 422) and index < len(attempts) - 1:
                logger.info(
                    "LLM 不支持当前请求参数，回退重试: status=%s json_mode=%s extras=%s",
                    resp.status_code,
                    use_json,
                    sorted(body_extras),
                )
                continue
            return resp
        return last_resp

    def _detail(self) -> str:
        usage = self.last_usage if isinstance(self.last_usage, dict) else {}
        details = usage.get("completion_tokens_details")
        if not isinstance(details, dict):
            details = {}
        bits = [f"finish={self.last_finish_reason or 'unknown'}"]
        if details.get("reasoning_tokens") is not None:
            bits.append(f"reasoning_tokens={details.get('reasoning_tokens')}")
        if usage.get("completion_tokens") is not None:
            bits.append(f"completion_tokens={usage.get('completion_tokens')}")
        if self.last_reasoning_content:
            bits.append(f"reasoning_chars={len(self.last_reasoning_content)}")
        bits.append(f"source={self.last_text_source or 'empty'}")
        return "，".join(bits)

    @staticmethod
    def _parse_json(content: str):
        parsed = LlmClient._try_parse(content)
        if parsed is None:
            raise LlmUnavailable("LLM 返回非 JSON（可能被截断）")
        return parsed

    @staticmethod
    def _try_parse(content: str):
        """从 LLM 输出里解析 JSON，容忍 markdown、解释文字、尾逗号与截断。"""
        if not content:
            return None
        text = content.strip()
        candidates: list[str] = []
        fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
        if fenced:
            candidates.append(fenced.group(1))
        candidates.append(text)
        # 贪婪提取保留旧行为，平衡提取用于 reasoning 中夹带多个 JSON 的场景
        greedy = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
        if greedy:
            candidates.append(greedy.group(1))
        candidates.extend(_balanced_json_candidates(text))

        parsed: list = []
        for candidate in candidates:
            obj = _loads_with_repair(candidate)
            if obj is not None:
                parsed.append(obj)
        if not parsed:
            return None

        def score(item) -> int:
            size = len(json.dumps(item, ensure_ascii=False, default=str))
            keys = set(item) if isinstance(item, dict) else set()
            # 优先选择业务包裹对象，避免 reasoning 里的示例 JSON 抢先命中
            return size + (10000 if keys & JSON_KEYS_HINT else 0)

        # 同分时取靠后的候选：最终答案通常出现在思考过程之后
        return max(enumerate(parsed), key=lambda pair: (score(pair[1]), pair[0]))[1]
