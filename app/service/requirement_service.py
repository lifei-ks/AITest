"""需求版本服务：版本 CRUD/PRD 解析 + 测试点抽取（LLM 优先，规则兜底）。

数据关系：
  Project 1--n Requirement 1--n TestPoint
  Requirement 1--n RequirementCase n--1 TestCase

设计决策：
- 不改造已有 test_cases 表（线上 MySQL 加列需迁移），用 requirement_cases 映射表关联版本。
- 需求文档更新后旧的测试点视为过期：清空测试点、状态回到 draft；已生成用例保留，重新生成时按版本替换。
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.common.errors import BizError
from app.common.timeutil import to_utc_iso
from app.database import SessionLocal
from app.entity.models import (
    CaseGeneration,
    Project,
    Requirement,
    RequirementCase,
    RequirementCaseChange,
    RequirementChange,
    RequirementChangeRun,
    RequirementSnapshot,
    TestCase,
    TestCaseVersion,
    TestPoint,
)
from app.agent import test_point_agent
from app.llm.client import LlmClient, LlmUnavailable
from app.service import settings_service
from app.service.project_service import parse_prd_text

logger = logging.getLogger(__name__)

CATEGORIES = {"functional", "negative", "boundary", "state", "api", "permission"}
PRIORITIES = {"P0", "P1", "P2"}
STATUS_TEXT = {"draft": "待解析", "analyzing": "解析中", "ready": "已解析", "failed": "解析失败"}

# PRD 文本 → 测试点的启发式关键词
_ACTION_WORDS = (
    "支持", "可以", "能够", "应", "必须", "需要", "禁止", "不能", "不允许",
    "校验", "验证", "展示", "显示", "提交", "保存", "新增", "创建", "修改",
    "删除", "查询", "搜索", "登录", "注册", "支付", "发货", "取消", "退款",
    "审核", "导入", "导出", "上传", "下载", "跳转", "返回", "计算", "扣减",
)
_NEGATIVE_WORDS = (
    "不能为空", "必填", "为空", "不允许", "禁止", "失败", "异常", "非法", "重复",
    "格式", "不正确", "不符合", "超出",
)
_BOUNDARY_WORDS = ("超过", "不超过", "最多", "最少", "长度", "上限", "下限", "范围", "大于", "小于", "位")
_STATE_WORDS = ("状态", "流转", "已支付", "待支付", "已发货", "已完成", "已取消", "退款", "支付", "发货")
_IGNORE_LINE_PREFIX = ("目录", "修订", "版本记录", "文档说明", "背景", "术语", "参考")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get_or_404(session: Session, rid: str) -> Requirement:
    req = session.get(Requirement, rid)
    if req is None:
        raise BizError(40406, f"需求版本不存在: {rid}", http_status=404)
    return req


def _get_project_or_404(session: Session, pid: str) -> Project:
    proj = session.get(Project, pid)
    if proj is None:
        raise BizError(40401, f"项目不存在: {pid}", http_status=404)
    return proj


# ---------- 懒迁移：把项目原有 PRD 变成第一个需求版本 ----------

def ensure_initial_requirement(session: Session, project: Project) -> None:
    if not (project.prd_content or "").strip():
        return
    exists = session.scalar(
        select(func.count()).select_from(Requirement).where(Requirement.project_id == project.id)
    )
    if exists:
        return
    session.add(
        Requirement(
            id=uuid.uuid4().hex,
            project_id=project.id,
            name="历史需求（迁移）",
            version="V1.0",
            content=project.prd_content,
            status="draft",
        )
    )
    session.flush()
    logger.info("项目 %s 的历史 PRD 已迁移为需求版本 V1.0", project.id)


# ---------- 查询 ----------


def _invalidate_generated_cases(session: Session, rid: str) -> None:
    """需求正文变化后，删除由旧内容生成的用例并解除映射。

    manual/import 用例保留，但它们指向旧测试点的映射必须移除，避免 Pipeline 复用
    与新版需求不匹配的用例。
    """
    case_ids = list(
        session.scalars(select(RequirementCase.test_case_id).where(RequirementCase.requirement_id == rid))
    )
    if not case_ids:
        return
    generated = list(
        session.scalars(
            select(TestCase.id).where(TestCase.id.in_(case_ids), TestCase.source.in_(["rule", "ai"]))
        )
    )
    if generated:
        session.query(TestCaseVersion).filter(TestCaseVersion.test_case_id.in_(generated)).delete(
            synchronize_session=False
        )
        session.query(TestCase).filter(TestCase.id.in_(generated)).delete(synchronize_session=False)
    session.query(RequirementCase).filter(RequirementCase.requirement_id == rid).delete(
        synchronize_session=False
    )
    session.flush()

def _counts(session: Session, req_ids: list[str]) -> tuple[dict[str, int], dict[str, int]]:
    if not req_ids:
        return {}, {}
    points = dict(
        session.execute(
            select(TestPoint.requirement_id, func.count()).where(TestPoint.requirement_id.in_(req_ids)).group_by(TestPoint.requirement_id)
        ).all()
    )
    cases = dict(
        session.execute(
            select(RequirementCase.requirement_id, func.count())
            .where(RequirementCase.requirement_id.in_(req_ids))
            .group_by(RequirementCase.requirement_id)
        ).all()
    )
    return points, cases


def requirement_to_dict(req: Requirement, point_count: int = 0, case_count: int = 0) -> dict:
    return {
        "id": req.id,
        "projectId": req.project_id,
        "name": req.name,
        "version": req.version,
        "status": req.status,
        "statusText": STATUS_TEXT.get(req.status, req.status),
        "errorMessage": req.error_message,
        "sourceFile": req.source_file,
        "contentLength": len(req.content or ""),
        "testPointCount": point_count,
        "caseCount": case_count,
        "createdAt": to_utc_iso(req.created_at),
        "updatedAt": to_utc_iso(req.updated_at),
    }


def list_requirements(session: Session, pid: str) -> list[dict]:
    project = _get_project_or_404(session, pid)
    ensure_initial_requirement(session, project)
    reqs = list(
        session.scalars(
            select(Requirement)
            .where(Requirement.project_id == pid)
            # 同一秒创建多个版本时用版本号稳定排序，避免“最新版本”退化成 V1
            .order_by(Requirement.created_at.asc(), Requirement.version.asc())
        )
    )
    points, cases = _counts(session, [r.id for r in reqs])
    return [requirement_to_dict(r, points.get(r.id, 0), cases.get(r.id, 0)) for r in reqs]


def get_requirement(session: Session, rid: str) -> dict:
    req = _get_or_404(session, rid)
    points, cases = _counts(session, [req.id])
    data = requirement_to_dict(req, points.get(req.id, 0), cases.get(req.id, 0))
    data["content"] = req.content or ""
    return data


def _check_version_unique(session: Session, pid: str, version: str, exclude_id: str | None = None) -> None:
    stmt = select(Requirement).where(Requirement.project_id == pid, Requirement.version == version)
    dup = session.scalars(stmt).first()
    if dup and dup.id != exclude_id:
        raise BizError(40012, f"版本号 {version} 已存在，请换一个")


def create_requirement(session: Session, pid: str, data: dict) -> dict:
    _get_project_or_404(session, pid)
    name = (data.get("name") or "").strip()
    version = (data.get("version") or "").strip()
    if not name:
        raise BizError(40013, "需求名称不能为空")
    if not version:
        raise BizError(40013, "版本号不能为空")
    _check_version_unique(session, pid, version)
    req = Requirement(
        id=uuid.uuid4().hex,
        project_id=pid,
        name=name,
        version=version,
        content=data.get("content") or "",
        status="draft",
    )
    session.add(req)
    session.flush()
    return requirement_to_dict(req)


def update_requirement(session: Session, rid: str, data: dict) -> dict:
    req = _get_or_404(session, rid)
    if "version" in data and data["version"]:
        version = data["version"].strip()
        _check_version_unique(session, req.project_id, version, exclude_id=rid)
        req.version = version
    if "name" in data and data["name"]:
        req.name = data["name"].strip()
    if "content" in data and data["content"] is not None:
        content = data["content"]
        if content != (req.content or ""):
            # 需求内容变更：旧测试点作废，等待重新解析
            req.content = content
            session.query(TestPoint).filter(TestPoint.requirement_id == rid).delete(synchronize_session=False)
            _invalidate_generated_cases(session, rid)
            req.status = "draft"
            req.error_message = None
    session.flush()
    points, cases = _counts(session, [rid])
    return requirement_to_dict(req, points.get(rid, 0), cases.get(rid, 0))


def upload_requirement_prd(session: Session, rid: str, filename: str, raw: bytes) -> dict:
    req = _get_or_404(session, rid)
    text = parse_prd_text(filename, raw)
    req.content = text
    req.source_file = filename
    req.status = "draft"
    req.error_message = None
    session.query(TestPoint).filter(TestPoint.requirement_id == rid).delete(synchronize_session=False)
    _invalidate_generated_cases(session, rid)
    session.flush()
    return requirement_to_dict(req)


def delete_requirement(session: Session, rid: str) -> None:
    req = _get_or_404(session, rid)
    case_ids = list(
        session.scalars(select(RequirementCase.test_case_id).where(RequirementCase.requirement_id == rid))
    )
    if case_ids:
        # 自动生成的用例随版本删除；手工/导入用例保留，仅解除版本关联
        generated = list(
            session.scalars(
                select(TestCase.id).where(TestCase.id.in_(case_ids), TestCase.source.in_(["rule", "ai"]))
            )
        )
        if generated:
            session.query(TestCaseVersion).filter(TestCaseVersion.test_case_id.in_(generated)).delete(synchronize_session=False)
            session.query(TestCase).filter(TestCase.id.in_(generated)).delete(synchronize_session=False)
        session.query(RequirementCase).filter(RequirementCase.requirement_id == rid).delete(synchronize_session=False)
    session.query(TestPoint).filter(TestPoint.requirement_id == rid).delete(synchronize_session=False)
    session.query(CaseGeneration).filter(CaseGeneration.requirement_id == rid).delete(synchronize_session=False)
    session.query(RequirementCaseChange).filter(RequirementCaseChange.requirement_id == rid).delete(synchronize_session=False)
    session.query(RequirementChange).filter(RequirementChange.requirement_id == rid).delete(synchronize_session=False)
    session.query(RequirementChangeRun).filter(RequirementChangeRun.requirement_id == rid).delete(synchronize_session=False)
    session.query(RequirementSnapshot).filter(RequirementSnapshot.requirement_id == rid).delete(synchronize_session=False)
    session.delete(req)
    # 删除是破坏性操作：显式提交，避免依赖请求结束后的隐式提交导致前端刷新读到旧数据
    session.commit()


# ---------- 测试点 ----------

def test_point_to_dict(tp: TestPoint) -> dict:
    return {
        "id": tp.id,
        "requirementId": tp.requirement_id,
        "seq": tp.seq,
        "title": tp.title,
        "category": tp.category,
        "priority": tp.priority,
        "description": tp.description or "",
        "pageRoute": tp.page_route or "",
        "endpoint": tp.endpoint or "",
        "fields": json.loads(tp.fields) if tp.fields else [],
        "expected": tp.expected or "",
        "source": tp.source,
        "confidence": tp.confidence,
    }


def list_test_points(session: Session, rid: str) -> list[dict]:
    _get_or_404(session, rid)
    points = list(
        session.scalars(select(TestPoint).where(TestPoint.requirement_id == rid).order_by(TestPoint.seq))
    )
    return [test_point_to_dict(p) for p in points]


def get_points_for_generation(session: Session, rid: str) -> list[dict]:
    return list_test_points(session, rid)


# ---------- 测试点抽取：LLM 优先 ----------

def _safe_json_loads(raw: str | None, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def _load_code_facts(session: Session, pid: str):
    """代码事实（页面/字段/接口/状态机）；未分析时返回空事实，仍可基于 PRD 出测试点。"""
    from app.service.generation_service import get_generation_context

    try:
        backend, frontend, machines = get_generation_context(session, pid)
        return backend, frontend, machines
    except BizError:
        logger.info("项目 %s 尚未完成代码分析，测试点仅基于需求文档", pid)
        return None, None, []


def _facts_for_prompt(backend, frontend, machines) -> dict:
    pages = []
    if frontend:
        for p in frontend.pages:
            fields = []
            for form in p.forms:
                for f in form.fields:
                    fields.append(
                        {
                            "label": f.label,
                            "prop": f.prop,
                            "type": f.type,
                            "required": f.required,
                            "placeholder": f.placeholder,
                            "rules": f.rules,
                        }
                    )
            pages.append({"name": p.name, "route": p.route, "fields": fields})
    endpoints = []
    if backend:
        endpoints = [{"method": e.method, "path": e.path, "controller": e.controller} for e in backend.endpoints[:60]]
    return {
        "pages": pages,
        "endpoints": endpoints,
        "uiTexts": (frontend.uiTexts[:40] if frontend else []),
        "stateMachines": [
            {
                "name": m.get("name"),
                "states": [s.get("label") or s.get("name") for s in m.get("states", [])],
                "transitions": [
                    {"from": t.get("from"), "to": t.get("to"), "trigger": t.get("trigger"), "verified": t.get("verified")}
                    for t in m.get("transitions", [])
                ],
            }
            for m in machines
        ],
    }


def _normalize_points(raw, source: str) -> list[dict]:
    """保留原内部入口；实现已收敛到 Agent 1，避免两份规则漂移。"""
    return test_point_agent.normalize_points(raw, source)


def _extract_points_llm(client: LlmClient, req: Requirement, backend, frontend, machines) -> list[dict]:
    facts = _facts_for_prompt(backend, frontend, machines)
    content = (req.content or "")[:12000]
    return test_point_agent.extract_points(
        client,
        requirement_version=req.version,
        requirement_name=req.name,
        content=content,
        facts=facts,
    )


# ---------- 测试点抽取：规则兜底 ----------

def _clean_line(line: str) -> str:
    s = line.strip()
    s = re.sub(r"^#{1,6}\s*", "", s)
    s = re.sub(r"^[-*+]\s+", "", s)
    s = re.sub(r"^\d+[.、)]\s*", "", s)
    s = re.sub(r"^\[[ xX]\]\s*", "", s)
    s = s.replace("**", "").replace("`", "").strip()
    return s


def _candidate_lines(text: str, limit: int = 18) -> list[str]:
    out: list[str] = []
    for raw in (text or "").splitlines():
        # 标题只是分组，不是测试点（如「## 订单查询增强」）
        if raw.strip().startswith("#"):
            continue
        s = _clean_line(raw)
        if len(s) < 6 or len(s) > 120:
            continue
        if s.startswith(_IGNORE_LINE_PREFIX):
            continue
        if not any(w in s for w in _ACTION_WORDS + _NEGATIVE_WORDS + _BOUNDARY_WORDS + _STATE_WORDS):
            continue
        if s not in out:
            out.append(s)
        if len(out) >= limit:
            break
    return out


# 路由英文片段 → 中文关键词，用于把 PRD 文本匹配到页面
ROUTE_CN = {
    "order": "订单", "orders": "订单", "create": "创建", "add": "新增", "new": "新增",
    "list": "列表", "index": "列表", "detail": "详情", "edit": "编辑", "update": "修改",
    "pay": "支付", "ship": "发货", "cancel": "取消", "refund": "退款", "login": "登录",
    "user": "用户", "product": "商品", "goods": "商品", "search": "搜索", "query": "查询",
}


def _page_cn_tokens(page) -> set[str]:
    words = re.findall(r"[A-Za-z]+", f"{page.name} {page.route}")
    return {ROUTE_CN[w.lower()] for w in words if w.lower() in ROUTE_CN}


def _match_page(line: str, frontend):
    """先把路由英文片段翻成中文关键词做匹配，再退化为页面名/路由直接包含。"""
    if not frontend:
        return None, []
    # 列表/搜索/筛选类描述优先落到列表页
    if any(w in line for w in ("列表", "搜索", "查询", "筛选")):
        for p in frontend.pages:
            if "list" in (p.route or "").lower():
                return p, [f for form in p.forms for f in form.fields]
    best = None
    best_score = 0
    for p in frontend.pages:
        fields = [f for form in p.forms for f in form.fields]
        score = 0
        if p.name and p.name in line:
            score += 3
        route_key = (p.route or "").strip("/")
        if route_key and route_key in line:
            score += 3
        tokens = _page_cn_tokens(p)
        if tokens:
            score += sum(1 for t in tokens if t in line)
        if score > best_score:
            best, best_score = (p, fields), score
    if best and best_score > 0:
        return best
    return None, []


def _success_text(frontend) -> str:
    if frontend:
        for t in frontend.uiTexts:
            if "成功" in t:
                return t
    return "操作成功"


_TRANSITION_WORDS = ("支付", "发货", "取消", "退款", "完成", "确认收货", "变为", "流转")

# 代码字段名 → 需求里可能使用的中文名（用于判断 PRD 是否已覆盖该字段）
FIELD_CN = {
    "phone": "手机号", "mobile": "手机号", "receivername": "收货人", "receiver": "收货人",
    "address": "地址", "amount": "金额", "price": "价格", "remark": "备注",
    "email": "邮箱", "username": "用户名", "name": "姓名", "orderno": "订单号",
}


def _rule_category(line: str) -> str:
    if any(w in line for w in _BOUNDARY_WORDS):
        return "boundary"
    if any(w in line for w in _NEGATIVE_WORDS):
        return "negative"
    # 「按状态筛选」只是在说状态列，不等于状态流转
    if any(w in line for w in _TRANSITION_WORDS) and ("状态" in line or "流转" in line or "变为" in line):
        return "state"
    return "functional"


def _extract_points_rule(req: Requirement, backend, frontend, machines) -> list[dict]:
    points: list[dict] = []
    seen: set[str] = set()

    def add(title: str, category: str, priority: str, description: str = "", page_route: str = "",
            endpoint: str = "", fields: list | None = None, expected: str = "", confidence: float = 0.7):
        title = title.strip()[:255]
        if not title or title in seen:
            return
        seen.add(title)
        points.append(
            {
                "title": title,
                "category": category if category in CATEGORIES else "functional",
                "priority": priority if priority in PRIORITIES else "P1",
                "description": description[:2000],
                "pageRoute": page_route[:255],
                "endpoint": endpoint[:255],
                "fields": (fields or [])[:20],
                "expected": expected[:1000],
                "source": "rule",
                "confidence": confidence,
            }
        )

    # 1) 需求条目 → 测试点
    for line in _candidate_lines(req.content or ""):
        cat = _rule_category(line)
        page, fields = _match_page(line, frontend)
        # 状态流转用例通常在列表页操作（支付/发货按钮），匹配不到时挂到列表页
        if cat == "state" and page is None and frontend:
            page = next((p for p in frontend.pages if "list" in (p.route or "").lower()), None)
        pri = "P0" if cat in ("functional", "state") and any(w in line for w in ("提交", "支付", "发货", "退款", "创建", "登录")) else "P1"
        # 需求里用「」/引号写明的提示文案，优先作为断言锚点
        quoted = re.search(r"[「\"“]([^」\"”]{2,30})[」\"”]", line)
        if quoted:
            expected = quoted.group(1)
        else:
            expected = _success_text(frontend) if cat in ("functional", "state") else ""
        add(
            title=line[:40],
            category=cat,
            priority=pri,
            description=line,
            page_route=(page.route if page else ""),
            fields=[f.prop or f.label for f in fields],
            expected=expected,
        )

    # 2) 代码事实兜底：原则上只服务本期需求提到的页面；PRD 完全没内容时才全量兜底
    prd_count = len(points)
    has_state_point = any(p["category"] == "state" for p in points)
    covered_routes = {p["pageRoute"] for p in points if p["pageRoute"]}
    used_text = " ".join(f"{p['title']} {p['description']}" for p in points)

    if frontend:
        for page in frontend.pages:
            if page.route not in covered_routes:
                continue
            fields = [f for form in page.forms for f in form.fields]
            if not fields:
                continue
            required = [f for f in fields if f.required]
            page_has_functional = any(
                p["category"] == "functional" and p["pageRoute"] == page.route for p in points
            )
            if not page_has_functional:
                add(
                    title=f"{page.name}：填写合法信息提交成功",
                    category="functional",
                    priority="P0",
                    description=f"{page.name}（{page.route}）表单主流程",
                    page_route=page.route,
                    fields=[f.prop or f.label for f in fields],
                    expected=_success_text(frontend),
                )
            for f in required[:4]:
                label = f.label or f.prop
                cn = FIELD_CN.get((f.prop or "").lower(), label)
                if label in used_text or cn in used_text:
                    continue
                add(
                    title=f"{page.name}：{label}为空校验",
                    category="negative",
                    priority="P1",
                    description=f"{label} 为必填项",
                    page_route=page.route,
                    fields=[f.prop or label],
                    expected=f"{label}不能为空",
                )
    if backend and prd_count == 0:
        required_by_field = {}
        for e in backend.entities:
            for c in e.fieldConstraints:
                if c.rule in ("size", "pattern", "max", "min"):
                    required_by_field.setdefault(c.field, []).append(c)
                elif c.rule in ("notblank", "notnull", "notempty"):
                    required_by_field.setdefault(c.field, [])
        for field_name, constraints in list(required_by_field.items())[:10]:
            cn = FIELD_CN.get(field_name.lower(), field_name)
            if field_name in used_text or cn in used_text:
                continue
            for c in constraints[:1]:
                add(
                    title=f"{field_name}：边界值校验",
                    category="boundary",
                    priority="P1",
                    description=f"{field_name} 约束 {c.rule}={c.value}",
                    fields=[field_name],
                    expected=f"{field_name}格式不正确",
                )
    for sm in machines:
        if prd_count > 0 and not has_state_point:
            break
        for t in sm.get("transitions", []):
            if not t.get("verified"):
                continue
            if t.get("trigger") and t["trigger"] in used_text:
                continue
            add(
                title=f"{sm.get('name')}：{t.get('trigger')}（{t.get('from')}→{t.get('to')}）",
                category="state",
                priority="P0",
                description=f"状态从 {t.get('from')} 流转到 {t.get('to')}",
                expected=f"{t.get('trigger')}成功",
            )
    return points[:40]


# ---------- 后台解析任务 ----------

def parse_points(session: Session, req: Requirement, mode: str = "auto", profile_id: str | None = None) -> list[dict]:
    """同步抽取测试点（工作流和后台任务共用）。

    保持原解析策略：LLM 优先，LLM 不可用/空结果时规则兜底；mode=rule 时只用规则。
    """
    backend, frontend, machines = _load_code_facts(session, req.project_id)
    points: list[dict] = []
    if mode in ("llm", "auto"):
        client = settings_service.get_text_client(profile_id)
        if client.available():
            try:
                # LLM 可能耗时数十秒：先结束只读事务，避免 MySQL 空闲断开连接
                session.commit()
                points = _extract_points_llm(client, req, backend, frontend, machines)
            except LlmUnavailable as e:
                logger.warning("需求测试点 LLM 解析失败，降级规则模式: %s", e)
                session.rollback()
    if not points:
        points = _extract_points_rule(req, backend, frontend, machines)
    return test_point_agent.filter_static_structure_points(points)


def save_points(session: Session, req: Requirement, points: list[dict]) -> None:
    """整版替换测试点；调用方负责 commit。"""
    session.query(TestPoint).filter(TestPoint.requirement_id == req.id).delete(synchronize_session=False)
    for i, p in enumerate(points, start=1):
        session.add(
            TestPoint(
                id=uuid.uuid4().hex,
                requirement_id=req.id,
                project_id=req.project_id,
                seq=i,
                title=p["title"],
                category=p["category"],
                priority=p["priority"],
                description=p.get("description") or "",
                page_route=p.get("pageRoute") or None,
                endpoint=p.get("endpoint") or None,
                fields=json.dumps(p.get("fields") or [], ensure_ascii=False),
                expected=p.get("expected") or "",
                source=p.get("source") or "rule",
                confidence=p.get("confidence") or 0.7,
            )
        )
    req.status = "ready"
    req.error_message = None
    session.flush()


def start_analysis(rid: str, mode: str = "auto", profile_id: str | None = None) -> dict:
    with SessionLocal() as session:
        req = _get_or_404(session, rid)
        if not (req.content or "").strip():
            raise BizError(40014, "需求内容为空，请先填写或上传需求文档")
        req.status = "analyzing"
        req.error_message = None
        session.commit()
    threading.Thread(target=_run_analysis, args=(rid, mode, profile_id), daemon=True).start()
    return {"requirementId": rid, "status": "analyzing"}


def _run_analysis(rid: str, mode: str, profile_id: str | None = None) -> None:
    session = SessionLocal()
    try:
        req = session.get(Requirement, rid)
        if req is None:
            return
        points = parse_points(session, req, mode, profile_id)
        save_points(session, req, points)
        session.commit()
        logger.info("需求测试点解析完成 requirement=%s points=%s", rid, len(points))
    except Exception as exc:  # noqa: BLE001 - 后台任务需兜底记录
        logger.exception("需求测试点解析失败 requirement=%s", rid)
        session.rollback()
        try:
            req = session.get(Requirement, rid)
            if req:
                req.status = "failed"
                req.error_message = str(exc)[:2000]
                session.commit()
        finally:
            session.rollback()
    finally:
        session.close()


def wait_analysis(rid: str, timeout: float = 0.0) -> dict:
    """可选轮询辅助（测试/脚本用）。timeout<=0 不等待。"""
    deadline = time.time() + timeout
    while True:
        with SessionLocal() as session:
            req = _get_or_404(session, rid)
            if req.status in ("ready", "failed") or time.time() >= deadline:
                return get_requirement(session, rid)
        time.sleep(1)
