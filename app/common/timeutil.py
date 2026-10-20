"""时间序列化工具。

约定：数据库统一存 UTC（DateTime 列不带时区），对外输出必须带时区标记（Z），
否则前端 `new Date("2026-10-20T05:22:44")` 会按本地时间解析，导致东八区早 8 小时。
"""
from __future__ import annotations

from datetime import datetime, timezone


def to_utc_iso(dt: datetime | None) -> str | None:
    """naive datetime 视为 UTC，输出 ISO 8601（带 Z）；aware datetime 统一转 UTC。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
