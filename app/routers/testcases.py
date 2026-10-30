"""用例管理路由。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.common.response import ok
from app.database import SessionLocal
from app.dto.projects import CamelModel
from app.service import case_review_service
from app.service import testcase_service

router = APIRouter(prefix="/api/projects", tags=["testcases"])


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


class TestCasePayload(CamelModel):
    title: str | None = None
    module: str | None = None
    type: str | None = None
    priority: str | None = None
    preconditions: list | None = None
    steps: list | None = None
    expected_results: list | None = None
    structured_steps: list | None = None
    test_data: dict | None = None
    source: str | None = None
    confidence: float | None = None


class BatchDelete(BaseModel):
    ids: list[str]


class CopyTo(BaseModel):
    ids: list[str]
    targetProjectId: str


class ExecStatus(BaseModel):
    status: str


class AiReviewBody(BaseModel):
    caseIds: list[str]


class AiFixBody(BaseModel):
    caseId: str
    issues: list[dict] = Field(default_factory=list)


@router.get("/{pid}/testcases")
def list_testcases(
    pid: str,
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=200),
    module: str | None = None,
    type: str | None = None,
    priority: str | None = None,
    executionStatus: str | None = None,
    keyword: str | None = None,
    sort: str | None = None,
    requirementId: str | None = None,
    session: Session = Depends(get_session),
):
    return ok(testcase_service.list_testcases(
        session, pid, page, pageSize, module, type, priority, executionStatus, keyword, sort, requirementId
    ))


@router.post("/{pid}/testcases", status_code=201)
def create_testcase(pid: str, payload: TestCasePayload, session: Session = Depends(get_session)):
    tc = testcase_service.create_testcase(session, pid, payload.model_dump(exclude_none=True))
    return ok(testcase_service._to_dict(tc))


@router.get("/{pid}/testcases/export")
def export_csv(pid: str, format: str = "csv", session: Session = Depends(get_session)):
    content = testcase_service.export_csv(session, pid)
    return Response(
        content=content.encode("utf-8-sig"),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=testcases.csv"},
    )


@router.post("/{pid}/testcases/import")
async def import_csv(pid: str, file: UploadFile = File(...), session: Session = Depends(get_session)):
    raw = await file.read()
    content = raw.decode("utf-8-sig", errors="replace")
    count = testcase_service.import_csv(session, pid, content)
    return ok({"imported": count})


@router.post("/{pid}/testcases/copy-to")
def copy_to(pid: str, payload: CopyTo, session: Session = Depends(get_session)):
    count = testcase_service.copy_to(session, payload.ids, payload.targetProjectId)
    return ok({"copied": count})


@router.post("/{pid}/testcases/ai-review")
def ai_review(pid: str, payload: AiReviewBody, session: Session = Depends(get_session)):
    return ok(case_review_service.review_cases(session, pid, payload.caseIds))


@router.post("/{pid}/testcases/ai-fix")
def ai_fix(pid: str, payload: AiFixBody, session: Session = Depends(get_session)):
    return ok(case_review_service.repair_case(session, pid, payload.caseId, payload.issues))


@router.delete("/{pid}/testcases/batch")
def batch_delete(pid: str, payload: BatchDelete, session: Session = Depends(get_session)):
    testcase_service.delete_testcases(session, payload.ids)
    return ok(None)


@router.get("/{pid}/testcases/{cid}")
def get_testcase(pid: str, cid: str, session: Session = Depends(get_session)):
    return ok(testcase_service.get_testcase(session, cid))


@router.put("/{pid}/testcases/{cid}")
def update_testcase(pid: str, cid: str, payload: TestCasePayload, session: Session = Depends(get_session)):
    tc = testcase_service.update_testcase(session, cid, payload.model_dump(exclude_unset=True))
    return ok(testcase_service._to_dict(tc))


@router.delete("/{pid}/testcases/{cid}")
def delete_testcase(pid: str, cid: str, session: Session = Depends(get_session)):
    testcase_service.delete_testcases(session, [cid])
    return ok(None)


@router.put("/{pid}/testcases/{cid}/execution-status")
def set_execution_status(pid: str, cid: str, payload: ExecStatus, session: Session = Depends(get_session)):
    testcase_service.set_execution_status(session, cid, payload.status)
    return ok(None)


@router.get("/{pid}/testcases/{cid}/versions")
def list_versions(pid: str, cid: str, session: Session = Depends(get_session)):
    return ok(testcase_service.list_versions(session, cid))


@router.post("/{pid}/testcases/{cid}/versions/{vid}/rollback")
def rollback(pid: str, cid: str, vid: str, session: Session = Depends(get_session)):
    tc = testcase_service.rollback_version(session, cid, vid)
    return ok(testcase_service._to_dict(tc))


@router.post("/{pid}/testcases/{cid}/review")
def review(pid: str, cid: str, session: Session = Depends(get_session)):
    tc = testcase_service._get_or_404(session, cid)
    testcase_service._rerun_gate(tc, session)
    session.flush()
    return ok({"qualityScore": tc.quality_score, "lintIssues": __import__("json").loads(tc.lint_issues) if tc.lint_issues else []})
