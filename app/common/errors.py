"""业务异常 + 全局异常处理器。

- BizError：业务错误，code 用 4xxxx 段，HTTP 状态同步
- RequestValidationError → 400 / code 40001（参数校验失败也是业务失败的一种）
- 未捕获异常 → 500 / code 50000，message 不含堆栈（堆栈写日志）
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.common.response import fail

logger = logging.getLogger(__name__)


class BizError(Exception):
    """业务异常。code 为 4xxxx 段业务码，http_status 同步设置（默认 400）。"""

    def __init__(self, code: int, message: str, http_status: int = status.HTTP_400_BAD_REQUEST):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(BizError)
    async def _biz_error_handler(_req: Request, exc: BizError) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status, content=fail(exc.code, exc.message))

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(_req: Request, exc: RequestValidationError) -> JSONResponse:
        # 取第一条校验错误的可读描述，避免前端拿到一长串 errors 数组
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(x) for x in first.get("loc", []))
        msg = first.get("msg", "参数校验失败")
        message = f"参数校验失败: {loc} {msg}" if loc else f"参数校验失败: {msg}"
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=fail(40001, message),
        )

    @app.exception_handler(Exception)
    async def _unhandled_handler(_req: Request, exc: Exception) -> JSONResponse:
        logger.exception("未捕获异常: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=fail(50000, "服务器内部错误"),
        )
