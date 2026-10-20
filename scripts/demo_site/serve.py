"""内置演示靶站：FastAPI 静态服务 + SPA 路由回退（端口 8200）。

路由：/ → index.html；/orders/create → create.html；/orders/list → list.html；未知 → index.html
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

BASE = Path(__file__).resolve().parent
STATIC = BASE / "static"

app = FastAPI()

# 路由 → 文件映射（SPA 回退）
ROUTES = {
    "/": "index.html",
    "/orders/create": "create.html",
    "/orders/list": "list.html",
}


@app.get("/{path:path}")
async def serve(path: str):
    key = "/" + path if path else "/"
    target = ROUTES.get(key)
    if target and (STATIC / target).exists():
        return FileResponse(STATIC / target)
    # SPA 回退：未知路径回 index
    return FileResponse(STATIC / "index.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("serve:app", host="127.0.0.1", port=8200, reload=False)
