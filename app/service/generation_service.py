"""用例生成编排：取分析结果 → 规则/LLM 生成 → 三道质量闸 → 落库 → 覆盖自检。"""
from __future__ import annotations

import json
import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

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
from app.config import get_settings
from app.entity.models import (
    CodeAnalysis,
    Project,
    Requirement,
    RequirementCase,
    StateMachine,
    TestCase,
    TestCaseVersion,
    TestPoint,
)
from app.llm.client import LlmClient, LlmUnavailable

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
    """全局 TC-{6位递增} 编号。

    注意：session autoflush=False，必须先 flush 让已 add 的用例可见；且用数字比较，
    不能 MAX(id) 字符串字典序（否则 TC-000010 会被排在 TC-000002 前面）。
    """
    session.flush()
    ids = session.scalars(select(TestCase.id)).all()
    max_num = 0
    for i in ids:
        if i.startswith("TC-"):
            try:
                max_num = max(max_num, int(i[3:]))
            except ValueError:
                pass
    return f"TC-{max_num + 1:06d}"


def _next_project_seq(session: Session, pid: str) -> int:
    max_seq = session.scalar(
        select(func.max(TestCase.project_seq)).where(TestCase.project_id == pid)
    )
    return (max_seq or 0) + 1


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
        "expectedResults": json.loads(tc.expected_results) if tc.expected_results else [],
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
) -> dict:
    """执行生成，落库，返回统计。mode: rule / llm / auto（llm 不可用时降级 rule）。

    requirement_id 不为空时：按该需求版本的测试点生成，用例与版本建立映射。
    on_event(dict)：可选回调，用于 SSE 逐条推送（事件 started/progress/case/complete）。
    """
    owns = session is None
    if session is None:
        from app.database import SessionLocal

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
        if requirement is not None:
            cases = []
            client = LlmClient(get_settings().llm_api_key, get_settings().llm_base_url, get_settings().llm_model)
            if mode in ("llm", "auto") and client.available():
                try:
                    # 先释放只读事务，避免长 LLM 调用期间 MySQL 连接空闲被关闭
                    session.commit()
                    llm_cases = _gen_llm_for_points(
                        client, requirement, points, backend, frontend, machines, on_event=on_event
                    )
                    if llm_cases:
                        cases.extend(llm_cases)
                        actual_mode = "llm"
                except LlmUnavailable as e:
                    logger.warning("需求版本 LLM 生成降级规则模式: %s", e)
            covered = {c.test_point_id for c in cases if c.test_point_id}
            missing = [p for p in points if p["id"] not in covered]
            cases.extend(generate_rule_cases_for_points(missing, backend, frontend, machines))
        else:
            # 项目级生成（向后兼容）
            cases = generate_rule_cases(backend, frontend, machines)
            if mode in ("llm", "auto"):
                client = LlmClient(get_settings().llm_api_key, get_settings().llm_base_url, get_settings().llm_model)
                if client.available():
                    try:
                        session.commit()
                        llm_cases = _gen_llm(client, backend, frontend, machines)
                        if llm_cases:
                            cases = llm_cases + cases
                            actual_mode = "llm"
                    except LlmUnavailable as e:
                        logger.warning("LLM 生成降级规则模式: %s", e)

        _enrich_coverage_refs(cases, backend, machines, points)
        gate = run_quality_gate(cases)

        if requirement is not None:
            _clear_generated_for_requirement(session, requirement.id)
        else:
            # 决策：项目级重新生成时清空旧的生成用例（source=rule/ai），保留 manual/import。
            old_ids = list(
                session.scalars(
                    select(TestCase.id).where(TestCase.project_id == pid, TestCase.source.in_(["rule", "ai"]))
                )
            )
            if old_ids:
                session.query(TestCaseVersion).filter(TestCaseVersion.test_case_id.in_(old_ids)).delete(synchronize_session=False)
                # 同步清理版本映射，否则留下孤儿映射；后续新建用例复用 TC-xxxx ID 时会被错误关联
                session.query(RequirementCase).filter(RequirementCase.test_case_id.in_(old_ids)).delete(synchronize_session=False)
                session.query(TestCase).filter(TestCase.id.in_(old_ids)).delete(synchronize_session=False)
                session.flush()

        for c in gate.kept:
            test_point_id = getattr(c, "test_point_id", None)
            tc = _save_case(session, pid, c, requirement.id if requirement else None, test_point_id)
            if on_event:
                payload = case_to_dict(tc)
                payload["testPointId"] = test_point_id
                payload["requirementId"] = requirement.id if requirement else None
                on_event({"type": "case", "testCase": payload})
        session.commit()

        by_type: dict[str, int] = {}
        for c in gate.kept:
            by_type[c.type] = by_type.get(c.type, 0) + 1

        result = {
            "generated": gate.generated,
            "kept": len(gate.kept),
            "dropped": gate.dropped,
            "dropReasons": gate.dropReasons,
            "byType": by_type,
            "mode": actual_mode,
            "requirementId": requirement.id if requirement else None,
            "testPointCount": len(points),
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
            })
        return result
    finally:
        if owns:
            session.close()


def generate_requirement(rid: str, mode: str = "auto", on_event=None) -> dict:
    """按需求版本生成用例：解析所属项目后复用 generate。"""
    from app.database import SessionLocal

    with SessionLocal() as session:
        req = session.get(Requirement, rid)
        if req is None:
            from app.common.errors import BizError

            raise BizError(40406, f"需求版本不存在: {rid}", http_status=404)
        pid = req.project_id
    return generate(pid, mode, on_event=on_event, requirement_id=rid)


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
                expected_results=rc.get("expectedResults", []),
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
        if step_type not in ("ui_action", "input", "state_assert"):
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
            }
        )
    return out


def _gen_llm_for_points(client, requirement, points, backend, frontend, machines, on_event=None) -> list:
    """按需求测试点生成用例（LLM，分批）。返回用例的 test_point_id 必须能对上测试点。"""
    from app.agent.test_generator_agent import GeneratedCase

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
            continue
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
            expected = [str(x) for x in (rc.get("expectedResults") or []) if str(x).strip()]
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
