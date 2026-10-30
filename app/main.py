"""FastAPI 入口：实例创建、路由注册、CORS、启动钩子（自动 create_all）。"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.common.errors import register_exception_handlers
from app.common.response import ok
from app.config import get_settings
from app.database import Base, ensure_schema_compat, get_engine
from app.routers import (
    analysis,
    executions,
    generation,
    mindmap,
    projects,
    requirements,
    settings,
    testcases,
    workflows,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 启动即建表（V2 起有模型后生效；幂等）
    Base.metadata.create_all(get_engine())
    ensure_schema_compat(get_engine())
    # 后台线程不会跨进程存活：上次进程遗留的 running 运行必须显式标记失败
    from app.service import workflow_service

    interrupted = workflow_service.recover_interrupted_runs()
    if interrupted:
        logger.warning("已将 %s 个被服务重启中断的工作流标记为 failed", interrupted)
    logger.info("启动完成，数据库: %s", get_settings().database_url)
    yield


app = FastAPI(
    title=get_settings().app_name,
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    # 决策：开发期放开本地常用端口。理由：本项目无用户体系，无 CSRF 面。
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)


@app.get("/api/health")
def health() -> dict:
    """健康检查：统一信封 code=0。"""
    return ok({"status": "up", "app": get_settings().app_name, "version": app.version})


# ---- 业务路由 ----
app.include_router(projects.router)
app.include_router(requirements.router)
app.include_router(analysis.router)
app.include_router(generation.router)
app.include_router(testcases.router)
app.include_router(executions.router)
app.include_router(mindmap.router)
app.include_router(settings.router)
app.include_router(workflows.router)
