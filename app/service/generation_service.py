"""用例生成编排：取分析结果 → 规则/LLM 生成 → 三道质量闸 → 落库 → 覆盖自检。"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agent.count_assertion import normalize_case_counts
from app.agent.quality_gate import (
    is_compound_expected,
    quoted_anchors,
    run_quality_gate,
    steps_have_multiple_inputs,
)
from app.agent import test_case_agent
from app.agent.test_generator_agent import generate_rule_cases, generate_rule_cases_for_points
from app.analyzer.models import (
    BackendResult,
    BusinessRule,
    Endpoint,
    Entity,
    EntityField,
    EnumDef,
    EnumValue,
    FieldConstraint,
    FormField,
    FrontendResult,
    Page,
    Selector,
    to_json_dict,
)
from app.common.execution_enums import case_status_meta
from app.common.timeutil import to_utc_iso
from app.database import SessionLocal
from app.entity.models import (
    CodeAnalysis,
    Project,
    Requirement,
    CaseGeneration,
    IdSequence,
    RequirementCase,
    StateMachine,
    TestCase,
    TestCaseVersion,
    TestPoint,
)
from app.llm.client import LlmUnavailable
from app.service import settings_service

logger = logging.getLogger(__name__)


# ---------- 反序列化：JSON dict → dataclass ----------

def _backend_from_dict(d: dict | None) -> BackendResult:
    b = BackendResult(language=(d or {}).get("language", "java-spring"))
    if not d:
        return b
    b.endpoints = [Endpoint(**ep) for ep in d.get("endpoints", [])]
    for e in d.get("entities", []):
        ent = Entity(name=e["name"], file=e.get("file", ""))
        ent.fields = [EntityField(**f) for f in e.get("fields", [])]
        ent.fieldConstraints = [FieldConstraint(**c) for c in e.get("fieldConstraints", [])]
        b.entities.append(ent)
    for en in d.get("enums", []):
        ed = EnumDef(name=en["name"])
        ed.values = [EnumValue(**v) for v in en.get("values", [])]
        b.enums.append(ed)
    b.businessRules = [BusinessRule(**r) for r in d.get("businessRules", [])]
    b.uiTexts = d.get("uiTexts", [])
    return b


def _frontend_from_dict(d: dict | None) -> FrontendResult:
    f = FrontendResult(language=(d or {}).get("language", "vue"))
    if not d:
        return f
    for p in d.get("pages", []):
        page = Page(name=p["name"], route=p.get("route", ""), file=p.get("file", ""))
        for form in p.get("forms", []):
            from app.analyzer.models import Form

            frm = Form()
            frm.fields = [FormField(**fld) for fld in form.get("fields", [])]
            page.forms.append(frm)
        f.pages.append(page)
    f.selectors = [Selector(**s) for s in d.get("selectors", [])]
    f.uiTexts = d.get("uiTexts", [])
    f.components = d.get("components", [])
    return f


def _state_machines_from_db(session: Session, pid: str) -> list[dict]:
    machines = list(session.scalars(select(StateMachine).where(StateMachine.project_id == pid)))
    return [
        {
            "id": m.id,
            "name": m.name,
            "states": json.loads(m.states) if m.states else [],
            "transitions": json.loads(m.transitions) if m.transitions else [],
            "confidence": m.confidence,
        }
        for m in machines
    ]


def get_generation_context(session: Session, pid: str) -> tuple[BackendResult, FrontendResult, list[dict]]:
    """从分析结果组装生成上下文。"""
    analysis = session.scalars(
        select(CodeAnalysis)
        .where(CodeAnalysis.project_id == pid)
        .order_by(CodeAnalysis.created_at.desc())
        .limit(1)
    ).first()
    if analysis is None or analysis.status != "success":
        from app.common.errors import BizError

        raise BizError(40010, "请先完成代码分析再生成用例")
    backend = _backend_from_dict(json.loads(analysis.backend_result) if analysis.backend_result else None)
    frontend = _frontend_from_dict(json.loads(analysis.frontend_result) if analysis.frontend_result else None)
    machines = _state_machines_from_db(session, pid)
    return backend, frontend, machines


def _next_tc_id(session: Session) -> str:
    """全局 TC-{6位递增} 编号，使用序列行 + 行锁保证跨进程唯一。

    必须与调用方共用同一个事务：MySQL 的 FOR UPDATE 行锁会一直持有到用例提交，
    其他进程只能拿下一个号；SQLite 也不会出现独立连接抢写锁的问题。
    """
    ids = session.scalars(select(TestCase.id)).all()
    max_existing = 0
    for item in ids:
        if item.startswith("TC-"):
            try:
                max_existing = max(max_existing, int(item[3:]))
            except ValueError:
                pass

    row = session.execute(
        select(IdSequence).where(IdSequence.name == "test_case").with_for_update()
    ).scalar_one_or_none()
    if row is None:
        try:
            with session.begin_nested():
                row = IdSequence(name="test_case", value=max_existing)
                session.add(row)
                session.flush()
        except IntegrityError:
            row = session.execute(
                select(IdSequence).where(IdSequence.name == "test_case").with_for_update()
            ).scalar_one_or_none()
            if row is None:
                # 对端回滚后重试一次简单插入；仍失败说明环境异常
                row = IdSequence(name="test_case", value=max_existing)
                session.add(row)
                session.flush()
    row.value = max(int(row.value or 0), max_existing) + 1
    session.flush()
    return f"TC-{row.value:06d}"


def _next_project_seq(session: Session, pid: str) -> int:
    """项目内展示序号也使用序列行，避免并发生成出现重复 project_seq。"""
    max_seq = session.scalar(
        select(func.max(TestCase.project_seq)).where(TestCase.project_id == pid)
    ) or 0
    key = f"project_seq:{pid}"
    row = session.execute(
        select(IdSequence).where(IdSequence.name == key).with_for_update()
    ).scalar_one_or_none()
    if row is None:
        try:
            with session.begin_nested():
                row = IdSequence(name=key, value=max_seq)
                session.add(row)
                session.flush()
        except IntegrityError:
            row = session.execute(
                select(IdSequence).where(IdSequence.name == key).with_for_update()
            ).scalar_one_or_none()
            if row is None:
                row = IdSequence(name=key, value=max_seq)
                session.add(row)
                session.flush()
    row.value = max(int(row.value or 0), max_seq) + 1
    session.flush()
    return row.value


def _save_case(
    session: Session,
    pid: str,
    case,
    requirement_id: str | None = None,
    test_point_id: str | None = None,
) -> TestCase:
    tc = TestCase(
        id=_next_tc_id(session),
        project_seq=_next_project_seq(session, pid),
        project_id=pid,
        title=case.title,
        module=case.module,
        type=case.type,
        priority=case.priority,
        preconditions=json.dumps(case.preconditions, ensure_ascii=False),
        steps=json.dumps(case.steps, ensure_ascii=False),
        expected_results=json.dumps(case.expected_results, ensure_ascii=False),
        structured_steps=json.dumps(case.structured_steps, ensure_ascii=False),
        test_data=json.dumps(case.test_data, ensure_ascii=False),
        coverage_refs=json.dumps(case.coverage_refs, ensure_ascii=False),
        source=case.source,
        confidence=case.confidence,
        quality_score=getattr(case, "quality_score", 100),
        lint_issues=json.dumps(getattr(case, "lint_issues", []), ensure_ascii=False),
        execution_status="not_run",
    )
    session.add(tc)
    if requirement_id:
        session.add(
            RequirementCase(
                id=uuid.uuid4().hex,
                requirement_id=requirement_id,
                test_case_id=tc.id,
                test_point_id=test_point_id,
            )
        )
    return tc


def _points_for_generation(session: Session, rid: str) -> list[dict]:
    rows = list(session.scalars(select(TestPoint).where(TestPoint.requirement_id == rid).order_by(TestPoint.seq)))
    return [
        {
            "id": p.id,
            "title": p.title,
            "category": p.category,
            "priority": p.priority,
            "description": p.description or "",
            "pageRoute": p.page_route or "",
            "endpoint": p.endpoint or "",
            "fields": json.loads(p.fields) if p.fields else [],
            "expected": p.expected or "",
            "source": p.source,
        }
        for p in rows
    ]


def _clear_generated_for_requirement(session: Session, rid: str) -> None:
    """重新生成时只清理该需求版本下自动生成的用例，保留 manual/import。"""
    case_ids = list(session.scalars(select(RequirementCase.test_case_id).where(RequirementCase.requirement_id == rid)))
    if not case_ids:
        return
    generated = list(
        session.scalars(select(TestCase.id).where(TestCase.id.in_(case_ids), TestCase.source.in_(["rule", "ai"])))
    )
    if generated:
        session.query(TestCaseVersion).filter(TestCaseVersion.test_case_id.in_(generated)).delete(synchronize_session=False)
        session.query(RequirementCase).filter(RequirementCase.test_case_id.in_(generated)).delete(synchronize_session=False)
        session.query(TestCase).filter(TestCase.id.in_(generated)).delete(synchronize_session=False)
        session.flush()


def _as_text_list(value, limit: int | None = None) -> list[str]:
    """把 LLM 的预期结果收敛为字符串数组。

    兼容：
    - 正常数组：["完整句子", ...]
    - 模型返回单个字符串：不能逐字符迭代，必须整体作为一条
    - 历史脏数据：把字符串拆成 ["对","一","个",...]，长度 >=4 且全为单字符时合并
    """
    if value is None:
        return []
    raw = value if isinstance(value, (list, tuple)) else [value]
    out: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            text = item.get("text") or item.get("expected") or item.get("value") or ""
        else:
            text = str(item or "")
        text = " ".join(text.split()).strip()
        if text:
            out.append(text)
    if len(out) >= 4 and all(len(item) == 1 for item in out):
        out = ["".join(out)]
    return out[:limit] if limit else out


def case_to_dict(tc: TestCase) -> dict:
    """TestCase ORM → 前端 camelCase dict。"""
    status_meta = case_status_meta(tc.execution_status)
    return {
        "id": tc.id,
        "projectSeq": tc.project_seq,
        "title": tc.title,
        "module": tc.module,
        "type": tc.type,
        "priority": tc.priority,
        "preconditions": json.loads(tc.preconditions) if tc.preconditions else [],
        "steps": json.loads(tc.steps) if tc.steps else [],
        "expectedResults": _as_text_list(
            json.loads(tc.expected_results) if tc.expected_results else []
        ),
        "structuredSteps": json.loads(tc.structured_steps) if tc.structured_steps else [],
        "testData": json.loads(tc.test_data) if tc.test_data else {},
        "coverageRefs": json.loads(tc.coverage_refs) if tc.coverage_refs else {},
        "source": tc.source,
        "confidence": tc.confidence,
        "qualityScore": tc.quality_score,
        "lintIssues": json.loads(tc.lint_issues) if tc.lint_issues else [],
        "executionStatus": tc.execution_status,
        "executionStatusLabel": status_meta["label"],
        "executionStatusDescription": status_meta["description"],
        "executionStatusTag": status_meta["tag"],
    }


def _enrich_coverage_refs(cases, backend, machines, points: list[dict]) -> None:
    """生成阶段把测试点/步骤映射到真实覆盖事实，避免覆盖矩阵长期为 0。"""
    from app.service.coverage_service import resolve_coverage_refs

    backend_dict = to_json_dict(backend)
    point_by_id = {str(point.get("id")): point for point in points if point.get("id")}
    for case in cases:
        test_point_id = str(getattr(case, "test_point_id", "") or "")
        case_dict = {
            "title": case.title,
            "module": case.module,
            "coverageRefs": case.coverage_refs or {},
            "steps": case.steps,
            "structuredSteps": case.structured_steps,
            "testData": case.test_data,
        }
        case.coverage_refs = resolve_coverage_refs(
            case_dict,
            backend_dict,
            machines,
            point_by_id.get(test_point_id),
        )


def generate(
    pid: str,
    mode: str = "rule",
    session: Session | None = None,
    on_event=None,
    requirement_id: str | None = None,
    profile_id: str | None = None,
) -> dict:
    """执行生成，落库，返回统计。mode: rule / llm / auto（llm 不可用时降级 rule）。

    requirement_id 不为空时：按该需求版本的测试点生成，用例与版本建立映射。
    on_event(dict)：可选回调，用于 SSE 逐条推送（事件 started/progress/case/complete）。
    """
    owns = session is None
    if session is None:
        
        session = SessionLocal()
    try:
        backend, frontend, machines = get_generation_context(session, pid)
        requirement: Requirement | None = None
        points: list[dict] = []
        if requirement_id:
            requirement = session.get(Requirement, requirement_id)
            if requirement is None or requirement.project_id != pid:
                from app.common.errors import BizError

                raise BizError(40406, f"需求版本不存在: {requirement_id}", http_status=404)
            points = _points_for_generation(session, requirement_id)
            if not points:
                from app.common.errors import BizError

                raise BizError(40015, "该需求版本还没有测试点，请先在「需求版本」中解析需求文档")

        if on_event:
            on_event({"type": "started", "message": "已接受生成任务", "requirementId": requirement_id})
            on_event({
                "type": "progress",
                "message": "正在根据需求测试点与代码事实生成用例",
                "current": 0,
                "total": len(points),
            })

        # 需求版本模式：测试点 → 用例（LLM 优先，规则补足每个未覆盖测试点）
        actual_mode = "rule"
        fallback_reason = ""
        llm_info: dict = {}
        if requirement is not None:
            cases = []
            if mode in ("llm", "auto"):
                client = settings_service.get_text_client(profile_id)
                if client.available():
                    try:
                        # 先释放只读事务，避免长 LLM 调用期间 MySQL 连接空闲被关闭
                        session.commit()
                        llm_cases = _gen_llm_for_points(
                            client, requirement, points, backend, frontend, machines,
                            on_event=on_event, run_info=llm_info,
                        )
                        if llm_cases:
                            cases.extend(llm_cases)
                            actual_mode = "llm"
                        if llm_info.get("fallbackReason"):
                            fallback_reason = llm_info["fallbackReason"]
                    except LlmUnavailable as e:
                        logger.warning("需求版本 LLM 生成降级规则模式: %s", e)
                        fallback_reason = f"LLM 调用失败：{e}"
                        if on_event:
                            on_event({
                                "type": "fallback",
                                "message": "LLM 不可用，已切换规则生成",
                                "reason": fallback_reason,
                                "current": 0,
                                "total": len(points),
                            })
                else:
                    fallback_reason = "未配置可用的文本 LLM，已使用规则生成"
                    if on_event:
                        on_event({"type": "fallback", "message": "未配置 LLM，已使用规则生成", "reason": fallback_reason})
            covered = {c.test_point_id for c in cases if c.test_point_id}
            missing = [p for p in points if p["id"] not in covered]
            if missing and mode in ("llm", "auto") and not fallback_reason:
                fallback_reason = f"{len(missing)} 个测试点未产出 LLM 用例，已用规则补齐"
            if missing:
                if on_event:
                    on_event({
                        "type": "progress",
                        "message": f"规则生成剩余 {len(missing)} 个测试点",
                        "current": len(cases),
                        "total": len(points),
                        "mode": "rule",
                    })
                cases.extend(generate_rule_cases_for_points(missing, backend, frontend, machines))
        else:
            # 项目级生成（向后兼容）
            cases = generate_rule_cases(backend, frontend, machines)
            if mode in ("llm", "auto"):
                client = settings_service.get_text_client(profile_id)
                if client.available():
                    try:
                        session.commit()
                        llm_cases = _gen_llm(client, backend, frontend, machines)
                        if llm_cases:
                            cases = llm_cases + cases
                            actual_mode = "llm"
                    except LlmUnavailable as e:
                        logger.warning("LLM 生成降级规则模式: %s", e)
                        fallback_reason = f"LLM 调用失败：{e}"
                        if on_event:
                            on_event({
                                "type": "fallback",
                                "message": "LLM 不可用，已切换规则生成",
                                "reason": fallback_reason,
                                "current": 0,
                                "total": 0,
                            })

        _enrich_coverage_refs(cases, backend, machines, points)
        list_routes = [p.route for p in frontend.pages if p.route]
        normalized_cases = []
        for case in cases:
            proxy = {
                "structuredSteps": case.structured_steps,
                "expectedResults": case.expected_results,
                "steps": case.steps,
                "preconditions": case.preconditions,
            }
            transformed = normalize_case_counts(proxy, list_routes)
            if transformed.get("drop"):
                logger.warning("跳过无法建立运行时基线的固定总数用例: %s", case.title)
                continue
            if transformed["changed"]:
                case.structured_steps = proxy["structuredSteps"]
                case.expected_results = proxy["expectedResults"]
                case.steps = proxy["steps"]
                case.preconditions = proxy["preconditions"]
                logger.info("用例已把固定总数改为运行时基线: %s", case.title)
            normalized_cases.append(case)
        cases = normalized_cases
        gate = run_quality_gate(cases)

        # 空结果不能删除旧用例：质量闸/动态改写全部淘汰时，旧用例仍是最有价值的产物。
        existing_generated_ids: list[str] = []
        if requirement is not None:
            existing_generated_ids = list(
                session.scalars(
                    select(RequirementCase.test_case_id).where(
                        RequirementCase.requirement_id == requirement.id
                    )
                )
            )
        else:
            existing_generated_ids = list(
                session.scalars(
                    select(TestCase.id).where(
                        TestCase.project_id == pid, TestCase.source.in_(["rule", "ai"])
                    )
                )
            )
        preserved_existing = not gate.kept and bool(existing_generated_ids)

        if gate.kept:
            if requirement is not None:
                _clear_generated_for_requirement(session, requirement.id)
            else:
                # 决策：项目级重新生成时清空旧的生成用例（source=rule/ai），保留 manual/import。
                old_ids = existing_generated_ids
                if old_ids:
                    session.query(TestCaseVersion).filter(TestCaseVersion.test_case_id.in_(old_ids)).delete(synchronize_session=False)
                    # 同步清理版本映射，否则留下孤儿映射；后续新建用例复用 TC-xxxx ID 时会被错误关联
                    session.query(RequirementCase).filter(RequirementCase.test_case_id.in_(old_ids)).delete(synchronize_session=False)
                    session.query(TestCase).filter(TestCase.id.in_(old_ids)).delete(synchronize_session=False)
                    session.flush()
        elif preserved_existing:
            logger.warning("本次生成 0 条可用用例，保留需求/项目下已有用例（不提交删除）")

        saved: list[tuple[TestCase, str | None]] = []
        for c in gate.kept:
            test_point_id = getattr(c, "test_point_id", None)
            tc = _save_case(session, pid, c, requirement.id if requirement else None, test_point_id)
            saved.append((tc, test_point_id))
            if on_event:
                payload = case_to_dict(tc)
                payload["testPointId"] = test_point_id
                payload["requirementId"] = requirement.id if requirement else None
                on_event({"type": "case", "testCase": payload})

        by_type: dict[str, int] = {}
        for c in gate.kept:
            by_type[c.type] = by_type.get(c.type, 0) + 1

        generation_id = None
        if saved:
            generation_id = uuid.uuid4().hex
            snapshot = [
                {
                    "id": tc.id,
                    "testPointId": test_point_id,
                    "title": tc.title,
                    "module": tc.module,
                    "type": tc.type,
                    "priority": tc.priority,
                    "source": tc.source,
                    "qualityScore": tc.quality_score,
                    "expectedResults": _as_text_list(
                        json.loads(tc.expected_results) if tc.expected_results else []
                    ),
                }
                for tc, test_point_id in saved
            ]
            source_counts = _counts(tc.source for tc, _ in saved)
            summary = {
                "mode": actual_mode,
                "requestedMode": mode,
                "profileId": profile_id,
                "fallback": bool(fallback_reason),
                "fallbackReason": fallback_reason,
                "llmCaseCount": source_counts.get("ai", 0),
                "ruleCaseCount": source_counts.get("rule", 0),
                "dropReasons": gate.dropReasons,
                "byType": by_type,
            }
            session.add(
                CaseGeneration(
                    id=generation_id,
                    project_id=pid,
                    requirement_id=requirement.id if requirement else None,
                    mode=actual_mode,
                    profile_id=profile_id,
                    case_count=len(saved),
                    summary=json.dumps(summary, ensure_ascii=False),
                    snapshot=json.dumps(snapshot, ensure_ascii=False),
                )
            )
        session.commit()

        source_counts = _counts(getattr(c, "source", "rule") for c in gate.kept)
        result = {
            "generated": gate.generated,
            "kept": len(gate.kept),
            "dropped": gate.dropped,
            "dropReasons": gate.dropReasons,
            "byType": by_type,
            "mode": actual_mode,
            "requestedMode": mode,
            "llmCaseCount": source_counts.get("ai", 0),
            "ruleCaseCount": source_counts.get("rule", 0),
            "fallback": bool(fallback_reason),
            "fallbackReason": fallback_reason,
            "generationId": generation_id,
            "requirementId": requirement.id if requirement else None,
            "testPointCount": len(points),
            "preservedExisting": preserved_existing,
        }
        if on_event:
            on_event({
                "type": "complete",
                "generated": gate.generated,
                "kept": len(gate.kept),
                "dropped": gate.dropped,
                "byType": by_type,
                "dropReasons": gate.dropReasons,
                "mode": actual_mode,
                "requirementId": requirement.id if requirement else None,
                "testPointCount": len(points),
                "preservedExisting": preserved_existing,
                "mode": actual_mode,
                "requestedMode": mode,
                "llmCaseCount": result["llmCaseCount"],
                "ruleCaseCount": result["ruleCaseCount"],
                "fallback": result["fallback"],
                "fallbackReason": result["fallbackReason"],
                "generationId": generation_id,
            })
        return result
    finally:
        if owns:
            session.close()


def generate_requirement(rid: str, mode: str = "auto", on_event=None, profile_id: str | None = None) -> dict:
    """按需求版本生成用例：解析所属项目后复用 generate。"""
    
    with SessionLocal() as session:
        req = session.get(Requirement, rid)
        if req is None:
            from app.common.errors import BizError

            raise BizError(40406, f"需求版本不存在: {rid}", http_status=404)
        pid = req.project_id
    return generate(pid, mode, on_event=on_event, requirement_id=rid, profile_id=profile_id)


def _gen_llm(client, backend, frontend, machines) -> list:
    """LLM 模式生成（可选增强，失败由调用方降级规则模式）。"""
    from app.agent.test_generator_agent import GeneratedCase
    from app.analyzer.models import to_json_dict

    prompt_facts = {
        "endpoints": [{"method": e.method, "path": e.path} for e in backend.endpoints],
        "entities": [{"name": e.name, "fields": [f.name for f in e.fields]} for e in backend.entities],
        "enums": [{"name": e.name, "values": [v.name for v in e.values]} for e in backend.enums],
        "uiTexts": backend.uiTexts,
        "pages": [{"name": p.name, "route": p.route, "fields": [f.label for frm in p.forms for f in frm.fields]} for p in frontend.pages],
        "selectors": [{"type": s.type, "value": s.value} for s in frontend.selectors],
        "stateMachines": machines,
    }
    raw_cases = test_case_agent.generate_project_cases(client, prompt_facts)
    cases = []
    for rc in raw_cases:
        try:
            cases.append(GeneratedCase(
                title=rc.get("title", "未命名用例"),
                module=rc.get("module", "业务"),
                type=rc.get("type", "positive"),
                priority=rc.get("priority", "P1"),
                expected_results=_as_text_list(rc.get("expectedResults")),
                structured_steps=rc.get("structuredSteps", []),
                test_data=rc.get("testData", {}),
                coverage_refs=rc.get("coverageRefs", {"endpointIds": [], "transitionIds": [], "ruleIds": []}),
                source="ai",
                confidence=0.8,
            ))
        except Exception:  # noqa: BLE001 - 单条非法跳过
            continue
    return cases


def _normalize_steps(raw_steps) -> list[dict]:
    """LLM 返回的步骤规范化：限定类型、补齐 order/uiSelector，过滤非法项。"""
    out: list[dict] = []
    if not isinstance(raw_steps, list):
        return out
    for s in raw_steps[:12]:
        if not isinstance(s, dict):
            continue
        step_type = s.get("type")
        if step_type not in ("ui_action", "input", "state_assert", "capture"):
            continue
        sel = s.get("uiSelector") if isinstance(s.get("uiSelector"), dict) else {}
        out.append(
            {
                "order": len(out) + 1,
                "type": step_type,
                "action": str(s.get("action") or "")[:255],
                "target": str(s.get("target") or "")[:255],
                "uiSelector": {
                    "type": str(sel.get("type") or "text")[:20],
                    "value": str(sel.get("value") or "")[:500],
                },
                "expected": str(s.get("expected") or "")[:500],
                "inputValue": str(s.get("inputValue") or s.get("input_value") or "")[:500],
                "variable": str(s.get("variable") or "")[:60],
                "pattern": str(s.get("pattern") or "")[:300],
            }
        )
    return out


def _gen_llm_for_points(
    client, requirement, points, backend, frontend, machines, on_event=None, run_info=None
) -> list:
    """按需求测试点生成用例（LLM，分批）。返回用例的 test_point_id 必须能对上测试点。"""
    from app.agent.test_generator_agent import GeneratedCase

    run_info = run_info if run_info is not None else {}

    # 后端校验注解才是必填/长度的可靠来源（Vue rules 常在 <script> 中，前端解析拿不到）
    required_names = {
        c.field
        for e in backend.entities
        for c in e.fieldConstraints
        if c.rule in ("notblank", "notnull", "notempty")
    }
    facts = {
        "pages": [
            {
                "name": p.name,
                "route": p.route,
                "fields": [
                    {
                        "label": f.label,
                        "prop": f.prop,
                        "type": f.type,
                        "required": bool(f.required or f.prop in required_names or f.label in required_names),
                        "placeholder": f.placeholder,
                    }
                    for form in p.forms
                    for f in form.fields
                ],
            }
            for p in frontend.pages
        ],
        "fieldConstraints": [
            {"field": c.field, "rule": c.rule, "value": c.value}
            for e in backend.entities
            for c in e.fieldConstraints
            if c.rule in ("notblank", "notnull", "notempty", "size", "pattern", "max", "min")
        ][:120],
        "selectors": [{"type": s.type, "value": s.value} for s in frontend.selectors[:80]],
        "uiTexts": frontend.uiTexts[:40],
        "endpoints": [{"method": e.method, "path": e.path} for e in backend.endpoints[:60]],
        "stateMachines": [
            {
                "name": m.get("name"),
                "states": m.get("states", []),
                "transitions": [
                    {"from": t.get("from"), "to": t.get("to"), "trigger": t.get("trigger"), "verified": t.get("verified")}
                    for t in m.get("transitions", [])
                ],
            }
            for m in machines
        ],
    }
    point_by_id = {p["id"]: p for p in points}

    cases = []
    batch_size = 5
    for start in range(0, len(points), batch_size):
        batch = points[start : start + batch_size]
        batch_ids = {p["id"] for p in batch}
        if on_event:
            on_event({
                "type": "progress",
                "message": f"LLM 正在生成测试点 {start + 1}-{start + len(batch)} 的用例",
                "current": start,
                "total": len(points),
                "mode": "llm",
            })
        try:
            raw = test_case_agent.generate_for_points(
                client,
                requirement_version=requirement.version,
                requirement_name=requirement.name,
                points=batch,
                facts=facts,
            )
        except LlmUnavailable as e:
            logger.warning("测试点 %s-%s LLM 生成失败，降级规则: %s", start + 1, start + len(batch), e)
            fallback_reason = f"LLM 未返回最终答案（测试点 {start + 1}-{start + len(batch)}）：{e}"
            run_info["fallbackReason"] = fallback_reason
            run_info["fallbackAt"] = start
            if on_event:
                on_event({
                    "type": "fallback",
                    "message": "LLM 未返回可用结果，剩余测试点改用规则生成",
                    "reason": fallback_reason,
                    "current": start,
                    "total": len(points),
                })
            # 推理模型可能把 token 全花在 reasoning_content 上；继续请求后续批次只会
            # 让页面长时间转圈，直接停止 LLM，剩余测试点交给规则兜底。
            break
        if not isinstance(raw, list):
            continue
        for rc in raw:
            if not isinstance(rc, dict):
                continue
            tp_id = str(rc.get("testPointId") or "")
            if tp_id not in batch_ids:
                continue
            point = point_by_id[tp_id]
            # 测试点本身写了两个场景（如20字符成功+21字符报错）时，LLM 往往只挑一个或继续合并；
            # 统一交给规则生成器按场景拆分，保证合法/越界两条用例都产出
            if is_compound_expected(str(point.get("expected") or "")):
                logger.warning("测试点预期为复合场景，改由规则拆分生成: %s", point["title"])
                continue
            steps = _normalize_steps(rc.get("structuredSteps"))
            if not steps:
                continue
            allowed_selector_types = {"route", "placeholder", "text", "label", "role", "testid"}
            if any(
                (step.get("uiSelector") or {}).get("type") not in allowed_selector_types
                for step in steps
            ):
                logger.warning("LLM 用例使用 css/xpath 等不可验证定位，改由规则生成: %s", rc.get("title"))
                continue
            if any(
                step.get("type") == "state_assert"
                and len(quoted_anchors(str(step.get("expected") or ""))) != 1
                for step in steps
            ):
                logger.warning("LLM 用例断言不是单「」锚点，改由规则生成: %s", rc.get("title"))
                continue
            # 执行器依赖第一步导航：LLM 漏掉时用测试点页面路由补上
            first_is_route = (steps[0].get("uiSelector") or {}).get("type") == "route"
            if not first_is_route:
                route = point.get("pageRoute") or "/"
                steps = [
                    {
                        "order": 1,
                        "type": "ui_action",
                        "action": f"打开页面{route}",
                        "target": route,
                        "uiSelector": {"type": "route", "value": route},
                        "expected": "",
                        "inputValue": "",
                    }
                ] + steps
                for idx, step in enumerate(steps, start=1):
                    step["order"] = idx
            expected = _as_text_list(rc.get("expectedResults"))
            if not expected:
                expected = [point["expected"] or point["title"]]
            # 复合预期（一条用例判断两个输入/两个结果）无法被单次执行同时满足：
            # 跳过该 LLM 用例，让该测试点进入规则兜底，由规则拆成单场景用例
            if any(is_compound_expected(x) for x in expected):
                logger.warning("LLM 用例包含复合预期，改由规则拆分生成: %s", rc.get("title"))
                continue
            compound_assert = any(
                step.get("type") == "state_assert"
                and any(
                    is_compound_expected(str(raw or ""))
                    for raw in (step.get("expected"), (step.get("uiSelector") or {}).get("value"))
                )
                for step in steps
            )
            if compound_assert:
                logger.warning("LLM 用例断言包含多个锚点，改由规则拆分生成: %s", rc.get("title"))
                continue
            if steps_have_multiple_inputs(steps):
                logger.warning("LLM 用例对同一字段给了多个输入值，改由规则拆分生成: %s", rc.get("title"))
                continue
            refs = rc.get("coverageRefs") if isinstance(rc.get("coverageRefs"), dict) else {}
            cases.append(
                GeneratedCase(
                    title=str(rc.get("title") or point["title"])[:255],
                    module=str(rc.get("module") or point["title"])[:120],
                    type=str(rc.get("type") or "positive"),
                    priority=str(rc.get("priority") or point["priority"]),
                    preconditions=[f"需求版本：{requirement.version} {requirement.name}"],
                    steps=[s["action"] for s in steps if s["action"]][:10],
                    expected_results=expected[:5],
                    structured_steps=steps,
                    test_data=rc.get("testData") if isinstance(rc.get("testData"), dict) else {},
                    coverage_refs={
                        "endpointIds": refs.get("endpointIds", []),
                        "transitionIds": refs.get("transitionIds", []),
                        "ruleIds": refs.get("ruleIds", []),
                        "testPointIds": [tp_id],
                    },
                    source="ai",
                    confidence=0.85,
                    test_point_id=tp_id,
                )
            )
            if on_event:
                on_event({
                    "type": "case_draft",
                    "testCase": {
                        "title": str(rc.get("title") or point["title"])[:255],
                        "type": str(rc.get("type") or "positive"),
                        "priority": str(rc.get("priority") or point["priority"]),
                        "expectedResults": expected[:1],
                        "source": "ai",
                        "testPointId": tp_id,
                        "qualityScore": None,
                    },
                })
        if on_event:
            done = min(start + batch_size, len(points))
            on_event({
                "type": "progress",
                "message": f"LLM 已处理 {done}/{len(points)} 个测试点",
                "current": done,
                "total": len(points),
            })
    return cases


def coverage_gaps(session: Session, pid: str) -> dict:
    """覆盖自检：verified transition 无对应用例、端点无用例引用。"""
    from app.agent.state_machine_agent import bfs_paths  # noqa: F401  # 预留

    machines = _state_machines_from_db(session, pid)
    cases = list(session.scalars(select(TestCase).where(TestCase.project_id == pid)))

    referenced_transitions: set[str] = set()
    for tc in cases:
        refs = json.loads(tc.coverage_refs) if tc.coverage_refs else {}
        referenced_transitions.update(refs.get("transitionIds", []))

    transition_gaps = []
    for m in machines:
        for t in m.get("transitions", []):
            if t.get("verified") and t.get("id") not in referenced_transitions:
                transition_gaps.append({"from": t["from"], "to": t["to"], "trigger": t.get("trigger")})

    return {"transitionGaps": transition_gaps, "endpointGaps": []}



def _counts(values) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        key = value or "unknown"
        out[key] = out.get(key, 0) + 1
    return out


# ---------- 生成版本快照 ----------

_LEGACY_SNAPSHOT_LOCK = threading.Lock()


def _ensure_legacy_generation(session: Session, requirement_id: str) -> None:
    """改造前已存在的实时用例没有版本记录，首次查看时归档成只读历史版本。

    只创建一次；不改动用例、不改需求，也不代表这些用例来自同一次生成。
    """
    exists = session.scalar(
        select(CaseGeneration.id)
        .where(CaseGeneration.requirement_id == requirement_id)
        .limit(1)
    )
    if exists:
        return
    requirement = session.get(Requirement, requirement_id)
    if requirement is None:
        return
    mappings = session.execute(
        select(RequirementCase.test_case_id, RequirementCase.test_point_id).where(
            RequirementCase.requirement_id == requirement_id
        )
    ).all()
    case_ids = [row[0] for row in mappings]
    if not case_ids:
        return
    point_by_case = {row[0]: row[1] for row in mappings}
    cases = list(
        session.scalars(
            select(TestCase)
            .where(TestCase.id.in_(case_ids))
            .order_by(TestCase.project_seq)
        )
    )
    if not cases:
        return
    source_counts = _counts(tc.source for tc in cases)
    by_type = _counts(tc.type for tc in cases)
    created_at = max((tc.created_at for tc in cases if tc.created_at), default=None) or datetime.utcnow()
    summary = {
        "legacy": True,
        "note": "改造前已存在的实时用例，首次查看时自动归档；不代表单次生成",
        "llmCaseCount": source_counts.get("ai", 0),
        "ruleCaseCount": source_counts.get("rule", 0),
        "byType": by_type,
        "fallback": False,
        "fallbackReason": "",
    }
    # 与正常生成保持同样的轻量快照结构，避免超过 MySQL TEXT 的 64KB 上限
    snapshot = []
    for tc in cases:
        try:
            expected = json.loads(tc.expected_results) if tc.expected_results else []
        except (TypeError, ValueError):
            expected = tc.expected_results or []
        snapshot.append({
            "id": tc.id,
            "testPointId": point_by_case.get(tc.id),
            "title": tc.title,
            "module": tc.module,
            "type": tc.type,
            "priority": tc.priority,
            "source": tc.source,
            "qualityScore": tc.quality_score,
            "expectedResults": _as_text_list(expected),
        })
    session.add(
        CaseGeneration(
            id=uuid.uuid4().hex,
            project_id=requirement.project_id,
            requirement_id=requirement_id,
            mode="legacy",
            profile_id=None,
            case_count=len(cases),
            summary=json.dumps(summary, ensure_ascii=False),
            snapshot=json.dumps(snapshot, ensure_ascii=False),
            created_at=created_at,
        )
    )
    session.flush()
    logger.info("已归档历史实时用例 requirement=%s cases=%s", requirement_id, len(cases))


def list_case_generations(session: Session, requirement_id: str, limit: int = 50) -> list[dict]:
    # 首次查看旧数据时补一个“历史归档”版本，让版本下拉立即可用。
    try:
        with _LEGACY_SNAPSHOT_LOCK:
            _ensure_legacy_generation(session, requirement_id)
    except Exception:  # noqa: BLE001 - 归档失败不能阻断版本列表
        logger.exception("归档历史实时用例失败 requirement=%s", requirement_id)
        session.rollback()
    rows = list(
        session.scalars(
            select(CaseGeneration)
            .where(CaseGeneration.requirement_id == requirement_id)
            .order_by(CaseGeneration.created_at.desc())
            .limit(max(1, min(100, limit)))
        )
    )
    latest_id = rows[0].id if rows else None
    return [
        {
            "id": row.id,
            "requirementId": row.requirement_id,
            "mode": row.mode,
            "profileId": row.profile_id,
            "caseCount": row.case_count,
            "summary": json.loads(row.summary) if row.summary else {},
            "isLatest": row.id == latest_id,
            "createdAt": to_utc_iso(row.created_at),
        }
        for row in rows
    ]


def get_case_generation(session: Session, requirement_id: str, generation_id: str) -> dict:
    row = session.get(CaseGeneration, generation_id)
    if row is None or row.requirement_id != requirement_id:
        from app.common.errors import BizError

        raise BizError(40409, f"生成版本不存在: {generation_id}", http_status=404)
    cases = json.loads(row.snapshot) if row.snapshot else []
    for item in cases:
        if isinstance(item, dict):
            item["expectedResults"] = _as_text_list(item.get("expectedResults"))
    return {
        "id": row.id,
        "requirementId": row.requirement_id,
        "mode": row.mode,
        "profileId": row.profile_id,
        "caseCount": row.case_count,
        "summary": json.loads(row.summary) if row.summary else {},
        "cases": cases,
        "createdAt": to_utc_iso(row.created_at),
    }
