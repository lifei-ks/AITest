"""外置提示词加载器：UTF-8 文本 + {{VAR}} 占位符。

设计决策：
- 不用 ``str.format``：提示词正文包含大量 JSON 花括号，会被误当成占位符。
  只替换 ``{{UPPER_SNAKE_CASE}}``，JSON 示例保持单花括号原样输出。
- 缺失变量立即抛 ``PromptError``，避免把未替换的占位符发给模型。
- 默认按文件 mtime 热加载：调整 txt 不需要重启服务；编码/读取异常回退上次缓存并告警。
- 目录可用环境变量 ``AITEST_PROMPT_DIR`` 覆盖（测试/灰度），默认 ``app/agent/prompts``。
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Mapping

logger = logging.getLogger(__name__)

DEFAULT_PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Z][A-Z0-9_]*)\s*\}\}")

_cache: dict[Path, tuple[float, str]] = {}


class PromptError(RuntimeError):
    """提示词缺失、占位符未提供或模板读取失败。"""


def prompt_dir() -> Path:
    override = (os.getenv("AITEST_PROMPT_DIR") or "").strip()
    return Path(override) if override else DEFAULT_PROMPT_DIR


def prompt_path(name: str) -> Path:
    """只允许简单文件名，阻止 ../ 形式的路径穿越。"""
    if not re.fullmatch(r"[a-z0-9_]+", name or ""):
        raise PromptError(f"非法提示词名称: {name!r}")
    path = (prompt_dir() / f"{name}.txt").resolve()
    try:
        path.relative_to(prompt_dir().resolve())
    except ValueError:
        raise PromptError(f"提示词路径越界: {name}") from None
    return path


def load_prompt(name: str) -> str:
    path = prompt_path(name)
    if not path.exists():
        raise PromptError(f"提示词文件不存在: {path}")
    mtime = path.stat().st_mtime
    cached = _cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        if cached:
            logger.warning("提示词读取失败，继续使用缓存 %s: %s", path.name, exc)
            return cached[1]
        raise PromptError(f"提示词读取失败: {path.name}: {exc}") from exc
    _cache[path] = (mtime, text)
    return text


def placeholders(text: str) -> set[str]:
    return set(PLACEHOLDER_RE.findall(text or ""))


def render_prompt(name: str, values: Mapping[str, object]) -> str:
    """替换模板占位符；缺变量直接报错，而不是把 {{X}} 发给模型。"""
    text = load_prompt(name)
    required = placeholders(text)
    missing = sorted(required - set(values))
    if missing:
        raise PromptError(f"提示词 {name} 缺少变量: {', '.join(missing)}")

    def _replace(match: re.Match) -> str:
        key = match.group(1)
        value = values[key]
        return "" if value is None else str(value)

    return PLACEHOLDER_RE.sub(_replace, text)


def clear_cache() -> None:
    _cache.clear()
