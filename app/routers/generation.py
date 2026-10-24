"""用例生成路由：触发生成 + SSE 流式输出。"""
from __future__ import annotations

import asyncio
import json
import queue as queue_mod
import threading

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.common.response import ok
from app.service import generation_service

router = APIRouter(prefix="/api/projects", tags=["generation"])


class GenerateRequest(BaseModel):
    mode: str = Field(default="rule", pattern="^(rule|llm|auto)$")
    modules: list[str] | None = None
    types: list[str] | None = None
    max_cases: int | None = None
    llmProfileId: str | None = None


@router.post("/{pid}/generate")
def generate(pid: str, req: GenerateRequest):
    result = generation_service.generate(pid, req.mode, profile_id=req.llmProfileId)
    return ok(result)


@router.get("/{pid}/generate/stream")
async def generate_stream(pid: str):
    """SSE 流式生成：started / progress / case / complete / error。"""
    q: queue_mod.Queue = queue_mod.Queue()

    def on_event(event: dict):
        q.put(event)

    def run():
        try:
            generation_service.generate(pid, "rule", on_event=on_event)
            q.put({"type": "__end__"})
        except Exception as exc:  # noqa: BLE001 - SSE 需兜底转 error 事件
            q.put({"type": "error", "message": str(exc)})

    threading.Thread(target=run, daemon=True).start()

    async def gen():
        while True:
            event = await asyncio.to_thread(q.get)
            if event.get("type") == "__end__":
                break
            payload = json.dumps(event, ensure_ascii=False)
            yield f"event: {event['type']}\ndata: {payload}\n\n"
            if event["type"] in ("complete", "error", "cancelled"):
                break

    return StreamingResponse(gen(), media_type="text/event-stream")
