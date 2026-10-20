"""统一响应信封：所有接口（含错误）返回 {code, message, data}。"""
from __future__ import annotations

from typing import Any


def ok(data: Any = None) -> dict[str, Any]:
    """成功响应：code = 0。"""
    return {"code": 0, "message": "ok", "data": data}


def fail(code: int, message: str, data: Any = None) -> dict[str, Any]:
    """业务失败响应：code 使用 4xxxx 段，HTTP 状态码由调用方/异常处理器同步设置。"""
    return {"code": code, "message": message, "data": data}
