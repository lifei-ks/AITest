"""用例管理业务：列表/详情/编辑/版本/回滚/批量删除/复制/CSV 导入导出/执行状态回写。"""
from __future__ import annotations

import csv
import io
import json
import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.quality_gate import run_quality_gate
from app.agent.test_generator_agent import GeneratedCase
from app.common.errors import BizError
from app.common.execution_enums import CASE_EXECUTION_STATUSES
from app.common.timeutil import to_utc_iso
from app.entity.models import Project, Requirement, RequirementCase, TestCase, TestCaseVersion

logger = logging.getLogger(__name__)

CSV_COLUMNS = ["序号", "标题", "模块", "类型", "优先级", "前置条件", "步骤", "预期结果", "质量分", "执行状态"]


def _get_or_404(session: Session, cid: str) -> TestCase:
    tc = session.get(TestCase, cid)
    if tc is None:
        raise BizError(40402, f"用例不存在: {cid}", http_status=404)
    return tc


def _orm_to_case(tc: TestCase) -> GeneratedCase:
    """ORM → GeneratedCase（供质量闸重跑）。"""
    return GeneratedCase(
        title=tc.title,
        module=tc.module,
        type=tc.type,
        priority=tc.priority,
        preconditions=json.loads(tc.preconditions) if tc.preconditions else [],
        steps=json.loads(tc.steps) if tc.steps else [],
        expected_results=json.loads(tc.expected_results) if tc.expected_results else [],
        structured_steps=json.loads(tc.structured_steps) if tc.structured_steps else [],
        test_data=json.loads(tc.test_data) if tc.test_data else {},
        coverage_refs=json.loads(tc.coverage_refs) if tc.coverage_refs else {},
        source=tc.source,
        confidence=tc.confidence,
    )


def _to_dict(tc: TestCase) -> dict:
    from app.service.generation_service import case_to_dict

    return case_to_dict(tc)


def _attach_requirement(session: Session, items: list[dict], pid: str | None = None) -> None:
    """批量补 requirementId / testPointId，避免前端逐条查询。

    pid 提供时只关联该项目下的需求版本，防止孤儿/跨项目映射污染显示。
    """
    ids = [i["id"] for i in items]
    if not ids:
        return
    rows = list(session.scalars(select(RequirementCase).where(RequirementCase.test_case_id.in_(ids))))
    if pid is not None:
        project_req_ids = set(
            session.scalars(select(Requirement.id).where(Requirement.project_id == pid))
        )
        rows = [r for r in rows if r.requirement_id in project_req_ids]
    mapping = {r.test_case_id: (r.requirement_id, r.test_point_id) for r in rows}
    for item in items:
        rid, tp_id = mapping.get(item["id"], (None, None))
        item["requirementId"] = rid
        item["testPointId"] = tp_id


def list_testcases(session: Session, pid: str, page: int, page_size: int,
                   module: str | None, type_: str | None, priority: str | None,
                   execution_status: str | None, keyword: str | None, sort: str | None,
                   requirement_id: str | None = None) -> dict:
    stmt = select(TestCase).where(TestCase.project_id == pid)
    if module:
        stmt = stmt.where(TestCase.module == module)
    if type_:
        stmt = stmt.where(TestCase.type == type_)
    if priority:
        stmt = stmt.where(TestCase.priority == priority)
    if execution_status:
        stmt = stmt.where(TestCase.execution_status == execution_status)
    if keyword:
        stmt = stmt.where(TestCase.title.like(f"%{keyword}%"))
    if requirement_id:
        # 用 Python 列表而非子查询：MySQL 下 test_cases 与 requirement_cases 的
        # 字符串列 collation 可能不同（0900_ai_ci / general_ci），直接比较会报 1267。
        if requirement_id == "__none__":
            # 未关联版本：排除该项目下所有已被某个需求版本关联的用例
            project_case_ids = list(session.scalars(select(TestCase.id).where(TestCase.project_id == pid)))
            mapped = (
                list(
                    session.scalars(
                        select(RequirementCase.test_case_id).where(
                            RequirementCase.test_case_id.in_(project_case_ids)
                        )
                    )
                )
                if project_case_ids
                else []
            )
            if mapped:
                stmt = stmt.where(TestCase.id.not_in(mapped))
        else:
            mapped_ids = list(
                session.scalars(
                    select(RequirementCase.test_case_id).where(
                        RequirementCase.requirement_id == requirement_id
                    )
                )
            )
            stmt = stmt.where(TestCase.id.in_(mapped_ids or ["__none__"]))

    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0

    order = {"projectSeq": TestCase.project_seq.asc(),
             "qualityScore": TestCase.quality_score.desc(),
             "createdAt": TestCase.created_at.desc()}.get(sort, TestCase.project_seq.asc())
    stmt = stmt.order_by(order).offset((page - 1) * page_size).limit(page_size)
    items = [_to_dict(t) for t in session.scalars(stmt)]
    _attach_requirement(session, items, pid=pid)
    return {"total": total, "page": page, "pageSize": page_size, "items": items, "testCases": items}


def get_testcase(session: Session, cid: str) -> dict:
    tc = _get_or_404(session, cid)
    data = _to_dict(tc)
    _attach_requirement(session, [data], pid=tc.project_id)
    return data


def _apply_payload(tc: TestCase, payload: dict) -> None:
    for field in ("title", "module", "type", "priority", "source"):
        if field in payload and payload[field] is not None:
            setattr(tc, field, payload[field])
    for field in ("preconditions", "steps", "expected_results", "structured_steps", "test_data"):
        if field in payload and payload[field] is not None:
            setattr(tc, field, json.dumps(payload[field], ensure_ascii=False))


def _rerun_gate(tc: TestCase) -> None:
    case = _orm_to_case(tc)
    gate = run_quality_gate([case])
    if not gate.kept:
        raise BizError(40011, "用例未通过质量闸（无预期结果或得分过低）")
    tc.quality_score = gate.kept[0].quality_score
    tc.lint_issues = json.dumps(gate.kept[0].lint_issues, ensure_ascii=False)


def create_testcase(session: Session, pid: str, payload: dict) -> TestCase:
    if not payload.get("structured_steps"):
        raise BizError(40012, "structuredSteps 不能为空")
    from app.service.generation_service import _next_tc_id, _next_project_seq

    tc = TestCase(
        id=_next_tc_id(session),
        project_seq=_next_project_seq(session, pid),
        project_id=pid,
        title=payload.get("title") or "未命名用例",
        module=payload.get("module") or "",
        type=payload.get("type") or "positive",
        priority=payload.get("priority") or "P1",
        source=payload.get("source") or "manual",
        confidence=payload.get("confidence", 1.0),
    )
    _apply_payload(tc, payload)
    _rerun_gate(tc)
    session.add(tc)
    session.flush()
    return tc


def update_testcase(session: Session, cid: str, payload: dict) -> TestCase:
    tc = _get_or_404(session, cid)
    if "structured_steps" in payload and not payload["structured_steps"]:
        raise BizError(40012, "structuredSteps 不能为空")
    # 落版本（内容未变也产版本，实现简单）
    snapshot = _to_dict(tc)
    max_v = session.scalar(
        select(func.max(TestCaseVersion.version)).where(TestCaseVersion.test_case_id == cid)
    ) or 0
    session.add(TestCaseVersion(test_case_id=cid, version=max_v + 1, snapshot=json.dumps(snapshot, ensure_ascii=False)))
    _apply_payload(tc, payload)
    _rerun_gate(tc)
    session.flush()
    return tc


def delete_testcases(session: Session, cids: list[str]) -> None:
    session.query(TestCase).filter(TestCase.id.in_(cids)).delete(synchronize_session=False)
    session.query(TestCaseVersion).filter(TestCaseVersion.test_case_id.in_(cids)).delete(synchronize_session=False)
    session.query(RequirementCase).filter(RequirementCase.test_case_id.in_(cids)).delete(synchronize_session=False)


def list_versions(session: Session, cid: str) -> list[dict]:
    versions = list(session.scalars(
        select(TestCaseVersion).where(TestCaseVersion.test_case_id == cid).order_by(TestCaseVersion.version.desc())
    ))
    return [
        {"id": v.id, "version": v.version, "snapshot": json.loads(v.snapshot) if v.snapshot else {},
         "changeNote": v.change_note, "createdAt": to_utc_iso(v.created_at)}
        for v in versions
    ]


def rollback_version(session: Session, cid: str, vid: str) -> TestCase:
    tc = _get_or_404(session, cid)
    version = session.get(TestCaseVersion, vid)
    if version is None or version.test_case_id != cid:
        raise BizError(40403, "版本不存在", http_status=404)
    snap = json.loads(version.snapshot)
    _apply_payload(tc, snap)
    _rerun_gate(tc)
    # 回滚也产新版本（不回退版本号）
    max_v = session.scalar(select(func.max(TestCaseVersion.version)).where(TestCaseVersion.test_case_id == cid)) or 0
    session.add(TestCaseVersion(test_case_id=cid, version=max_v + 1, snapshot=json.dumps(snap, ensure_ascii=False), change_note=f"回滚到版本 {version.version}"))
    session.flush()
    return tc


def copy_to(session: Session, cids: list[str], target_project_id: str) -> int:
    if session.get(Project, target_project_id) is None:
        raise BizError(40401, "目标项目不存在", http_status=404)
    from app.service.generation_service import _next_tc_id, _next_project_seq

    count = 0
    for cid in cids:
        src = session.get(TestCase, cid)
        if src is None:
            continue
        tc = TestCase(
            id=_next_tc_id(session),
            project_seq=_next_project_seq(session, target_project_id),
            project_id=target_project_id,
            title=src.title, module=src.module, type=src.type, priority=src.priority,
            preconditions=src.preconditions, steps=src.steps, expected_results=src.expected_results,
            structured_steps=src.structured_steps, test_data=src.test_data, coverage_refs=src.coverage_refs,
            source=src.source, confidence=src.confidence, quality_score=src.quality_score,
            lint_issues=src.lint_issues, execution_status="not_run",
        )
        session.add(tc)
        count += 1
    session.flush()
    return count


def set_execution_status(session: Session, cid: str, status: str) -> None:
    tc = _get_or_404(session, cid)
    if status not in CASE_EXECUTION_STATUSES:
        raise BizError(
            40013,
            f"非法执行状态: {status}；可选值: {', '.join(CASE_EXECUTION_STATUSES)}",
        )
    tc.execution_status = status


def export_csv(session: Session, pid: str) -> str:
    cases = list(session.scalars(select(TestCase).where(TestCase.project_id == pid).order_by(TestCase.project_seq)))
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_COLUMNS)
    for tc in cases:
        pre = " | ".join(json.loads(tc.preconditions) if tc.preconditions else [])
        steps = " | ".join(json.loads(tc.steps) if tc.steps else [])
        exp = " | ".join(json.loads(tc.expected_results) if tc.expected_results else [])
        writer.writerow([tc.project_seq, tc.title, tc.module, tc.type, tc.priority, pre, steps, exp, tc.quality_score, tc.execution_status])
    return buf.getvalue()


def import_csv(session: Session, pid: str, content: str) -> int:
    reader = csv.DictReader(io.StringIO(content))
    if reader.fieldnames is None:
        raise BizError(40014, "CSV 缺少表头")
    missing = [c for c in CSV_COLUMNS if c not in reader.fieldnames]
    if missing:
        raise BizError(40014, f"CSV 缺少列: {', '.join(missing)}")
    from app.service.generation_service import _next_tc_id, _next_project_seq

    count = 0
    for row in reader:
        tc = TestCase(
            id=_next_tc_id(session),
            project_seq=_next_project_seq(session, pid),
            project_id=pid,
            title=row["标题"] or "未命名用例",
            module=row["模块"] or "",
            type=row["类型"] or "positive",
            priority=row["优先级"] or "P1",
            preconditions=json.dumps([x.strip() for x in row["前置条件"].split("|") if x.strip()], ensure_ascii=False),
            steps=json.dumps([x.strip() for x in row["步骤"].split("|") if x.strip()], ensure_ascii=False),
            expected_results=json.dumps([x.strip() for x in row["预期结果"].split("|") if x.strip()], ensure_ascii=False),
            structured_steps="[]",
            test_data="{}",
            coverage_refs=json.dumps({"endpointIds": [], "transitionIds": [], "ruleIds": []}),
            source="import",
            confidence=1.0,
            quality_score=int(row["质量分"]) if row.get("质量分") else 100,
            lint_issues="[]",
            execution_status=(
                row["执行状态"]
                if row["执行状态"] in CASE_EXECUTION_STATUSES
                else "not_run"
            ),
        )
        session.add(tc)
        count += 1
    session.flush()
    return count
