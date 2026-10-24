"""需求版本路由：版本 CRUD、PRD 上传、测试点解析、按版本生成用例（支持 SSE）。"""
from __future__ import annotations

import asyncio
import json
import queue as queue_mod
import threading

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.common.response import ok
from app.database import SessionLocal
from app.service import generation_service, requirement_service

router = APIRouter(tags=["requirements"])


def get_session() -> Session:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class RequirementPayload(BaseModel):
    name: str | None = None
    version: str | None = None
    content: str | None = None


class GeneratePayload(BaseModel):
    mode: str = "auto"
    llmProfileId: str | None = None


@router.get("/api/projects/{pid}/requirements")
def list_requirements(pid: str, session: Session = Depends(get_session)):
    return ok(requirement_service.list_requirements(session, pid))


@router.post("/api/projects/{pid}/requirements", status_code=201)
def create_requirement(pid: str, payload: RequirementPayload, session: Session = Depends(get_session)):
    return ok(requirement_service.create_requirement(session, pid, payload.model_dump(exclude_unset=True)))


@router.get("/api/requirements/{rid}")
def get_requirement(rid: str, session: Session = Depends(get_session)):
    return ok(requirement_service.get_requirement(session, rid))


@router.put("/api/requirements/{rid}")
def update_requirement(rid: str, payload: RequirementPayload, session: Session = Depends(get_session)):
    return ok(requirement_service.update_requirement(session, rid, payload.model_dump(exclude_unset=True)))


@router.delete("/api/requirements/{rid}")
def delete_requirement(rid: str, session: Session = Depends(get_session)):
    requirement_service.delete_requirement(session, rid)
    return ok(None)


@router.post("/api/requirements/{rid}/upload")
async def upload_prd(rid: str, file: UploadFile = File(...), session: Session = Depends(get_session)):
    raw = await file.read()
    if not raw:
        from app.common.errors import BizError

        raise BizError(40005, "上传文件为空")
    return ok(requirement_service.upload_requirement_prd(session, rid, file.filename or "", raw))


@router.post("/api/requirements/{rid}/analyze", status_code=202)
def analyze(rid: str, mode: str = Query("auto", pattern="^(rule|llm|auto)$"), profileId: str | None = Query(None)):
    return ok(requirement_service.start_analysis(rid, mode, profileId))


@router.get("/api/requirements/{rid}/test-points")
def list_points(rid: str, session: Session = Depends(get_session)):
    return ok(requirement_service.list_test_points(session, rid))


@router.get("/api/requirements/{rid}/generations")
def list_generations(rid: str, limit: int = Query(50, ge=1, le=100), session: Session = Depends(get_session)):
    return ok(generation_service.list_case_generations(session, rid, limit))


@router.get("/api/requirements/{rid}/generations/{generation_id}")
def get_generation(rid: str, generation_id: str, session: Session = Depends(get_session)):
    return ok(generation_service.get_case_generation(session, rid, generation_id))


@router.post("/api/requirements/{rid}/generate")
def generate(rid: str, payload: GeneratePayload):
    return ok(generation_service.generate_requirement(rid, payload.mode, profile_id=payload.llmProfileId))


@router.get("/api/requirements/{rid}/generate/stream")
async def generate_stream(rid: str, mode: str = Query("auto", pattern="^(rule|llm|auto)$"), profileId: str | None = Query(None)):
    """按需求版本流式生成：started / progress / case / complete / error。"""
    q: queue_mod.Queue = queue_mod.Queue()

    def on_event(event: dict):
        q.put(event)

    def run():
        try:
            generation_service.generate_requirement(rid, mode, on_event=on_event, profile_id=profileId)
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
