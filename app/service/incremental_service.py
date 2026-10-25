"""多期需求的增量变更分析与用例生成。

设计边界：
- 不修改既有 test_cases / requirement_cases 语义；新增快照、变更运行、
  变更明细和用例去向表，保证一期数据不被二期覆盖。
- 代码分析仍是项目级“最新事实”；每个需求版本建立一份不可变快照，
  对比时只读快照，避免后续重新分析把基线悄悄改掉。
- 未变化接口默认复用历史用例内容，但不调用 LLM；被修改接口只针对
  变化的参数/规则/鉴权生成增量测试点。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from difflib import SequenceMatcher
from pathlib import Path
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analyzer.models import BackendResult, FrontendResult, to_json_dict
from app.common.errors import BizError
from app.entity.models import (
    CaseGeneration,
    CodeAnalysis,
    Project,
    Requirement,
    RequirementCase,
    RequirementCaseChange,
    RequirementChange,
    RequirementChangeRun,
    RequirementSnapshot,
    StateMachine,
    TestCase,
    TestPoint,
)
from app.agent.test_generator_agent import GeneratedCase, generate_rule_cases_for_points
from app.llm.client import LlmUnavailable
from app.service import generation_service, settings_service

logger = logging.getLogger(__name__)

ENDPOINT_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH"}
DELETION_WORDS = ("删除", "移除", "下线", "废弃", "不再提供", "不再支持", "停止提供")
SECURITY_PATTERNS = (
    "SecurityFilterChain",
    "authorizeHttpRequests",
    "hasRole",
    "hasAuthority",
    "permitAll",
    "denyAll",
    "@PreAuthorize",
    "@Secured",
    "@RolesAllowed",
    "@EnableWebSecurity",
)
PRD_ENDPOINT_RE = re.compile(
    r"\b(GET|POST|PUT|DELETE|PATCH)\s+(/[A-Za-z0-9_\-./{}:]+)",
    re.IGNORECASE,
)
PARAM_KEY_RE = re.compile(r"\{[^/{}]+\}|:[A-Za-z_][A-Za-z0-9_]*")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
HEX_RE = re.compile(r"^[0-9a-f]{8,}$", re.IGNORECASE)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json_load(raw, default):
    if not raw:
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _sha(value) -> str:
    return hashlib.sha256(_dumps(value).encode("utf-8")).hexdigest()


def _norm_text(value) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def normalize_path(path: str) -> str:
    """接口路径规范化：只用于匹配，不改变原始路径展示。"""
    raw = str(path or "/").split("?", 1)[0].strip()
    raw = re.sub(r"/+", "/", raw)
    raw = PARAM_KEY_RE.sub("{param}", raw)
    segments = []
    for segment in raw.strip("/").split("/"):
        if segment and (segment.isdigit() or UUID_RE.match(segment) or HEX_RE.match(segment)):
            segment = "{param}"
        segments.append(segment.lower())
    return "/" + "/".join(segments)


def endpoint_key(endpoint: dict) -> str:
    return f"{str(endpoint.get('method') or '').upper()} {normalize_path(endpoint.get('path') or '')}"


def _entity_map(backend: BackendResult) -> dict[str, dict]:
    result = {}
    for entity in backend.entities:
        fields = []
        for field in entity.fields:
            constraints = [
                {
                    "rule": c.rule,
                    "value": c.value,
                }
                for c in entity.fieldConstraints
                if c.field == field.name
            ]
            fields.append({
                "name": field.name,
                "type": field.type,
                "constraints": constraints,
                "required": any(
                    c["rule"] in ("notblank", "notnull", "notempty")
                    for c in constraints
                ),
            })
        result[entity.name] = {"name": entity.name, "file": entity.file, "fields": fields}
    return result


def _expand_params(params: list[dict], entities: dict[str, dict]) -> list[dict]:
    """把 @RequestBody DTO 展开成字段级参数，便于识别必填/约束变化。"""
    expanded: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for param in params or []:
        if not isinstance(param, dict):
            continue
        item = {
            "name": str(param.get("name") or ""),
            "type": str(param.get("type") or ""),
            "source": str(param.get("source") or "unknown"),
            "required": bool(param.get("required")),
            "constraints": sorted(str(c) for c in (param.get("constraints") or [])),
        }
        if not item["name"]:
            continue
        signature = (item["name"], item["source"])
        if signature not in seen:
            expanded.append(item)
            seen.add(signature)
        dto_type = str(param.get("dtoType") or "")
        entity = entities.get(dto_type)
        if not entity:
            continue
        for field in entity.get("fields") or []:
            field_name = str(field.get("name") or "")
            if not field_name:
                continue
            constraints = [
                f"{c.get('rule')}({c.get('value')})" if c.get("value") is not None else str(c.get("rule"))
                for c in (field.get("constraints") or [])
                if c.get("rule")
            ]
            field_source = "request_body_field"
            signature = (field_name, field_source)
            if signature in seen:
                continue
            expanded.append({
                "name": field_name,
                "type": str(field.get("type") or ""),
                "source": field_source,
                "required": bool(field.get("required")),
                "constraints": sorted(constraints),
            })
            seen.add(signature)
    return expanded


def _rule_snapshot(rule) -> dict:
    return {
        "id": getattr(rule, "id", ""),
        "description": getattr(rule, "description", ""),
        "guard": getattr(rule, "guard", ""),
        "file": getattr(rule, "file", ""),
        "line": getattr(rule, "line", 0),
        "key": f"{_norm_text(getattr(rule, 'description', ''))}|{_norm_text(getattr(rule, 'guard', ''))}",
    }


def _related_rules(endpoint, rules: list[dict]) -> list[dict]:
    related = []
    method_name = str(endpoint.methodName or "").lower()
    for rule in rules:
        same_file = rule.get("file") and rule.get("file") == endpoint.file
        near = same_file and abs(int(rule.get("line") or 0) - int(endpoint.line or 0)) <= 120
        text = f"{rule.get('description') or ''} {rule.get('guard') or ''}".lower()
        semantic = bool(method_name and len(method_name) >= 4 and method_name in text)
        if near or semantic:
            related.append(rule)
    return related


def _build_security(project: Project) -> dict:
    """轻量扫描公共鉴权配置；读不到源码时显式标记 unavailable。"""
    root = Path(project.source_path or "")
    if not root.exists() or not root.is_dir():
        return {"available": False, "hash": "", "signals": [], "reason": "源码目录不可用"}
    signals = []
    files = 0
    for path in root.rglob("*.java"):
        rel = str(path.relative_to(root))
        if any(part in {"target", "build", ".git"} for part in Path(rel).parts):
            continue
        files += 1
        if files > 500:
            break
        try:
            if path.stat().st_size > 2 * 1024 * 1024:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        matched = sorted({pattern for pattern in SECURITY_PATTERNS if pattern in text})
        if matched:
            signals.append({"file": rel, "patterns": matched})
    payload = sorted(signals, key=lambda item: item["file"])
    return {
        "available": True,
        "hash": _sha(payload),
        "signals": payload[:30],
        "filesScanned": files,
    }


def _transition_snapshot(machines: list[dict]) -> list[dict]:
    out = []
    for machine in machines or []:
        for transition in machine.get("transitions") or []:
            if not transition.get("verified"):
                continue
            item = {
                "id": str(transition.get("id") or ""),
                "machine": machine.get("name") or "",
                "from": transition.get("from") or "",
                "to": transition.get("to") or "",
                "trigger": transition.get("trigger") or "",
                "key": f"{transition.get('from')}->{transition.get('to')}:{transition.get('trigger')}",
            }
            if item["id"]:
                out.append(item)
    return out


def build_snapshot(
    session: Session,
    requirement_id: str,
    *,
    analysis_id: str | None = None,
    source: str = "code_analysis",
) -> dict:
    """为需求版本固化一份接口/规则快照。"""
    requirement = session.get(Requirement, requirement_id)
    if requirement is None:
        raise BizError(40406, f"需求版本不存在: {requirement_id}", http_status=404)
    project = session.get(Project, requirement.project_id)
    if project is None:
        raise BizError(40401, f"项目不存在: {requirement.project_id}", http_status=404)

    if analysis_id:
        analysis = session.get(CodeAnalysis, analysis_id)
        if analysis is None or analysis.project_id != project.id:
            raise BizError(40410, f"代码分析记录不存在: {analysis_id}", http_status=404)
    else:
        analysis = session.scalars(
            select(CodeAnalysis)
            .where(
                CodeAnalysis.project_id == project.id,
                CodeAnalysis.status == "success",
            )
            .order_by(CodeAnalysis.created_at.desc())
            .limit(1)
        ).first()
    if analysis is None or analysis.status != "success":
        raise BizError(40060, "没有可用的成功代码分析结果，请先完成代码分析", http_status=400)

    backend = generation_service._backend_from_dict(
        _json_load(analysis.backend_result, None)
    )
    machines = [
        {
            "id": row.id,
            "name": row.name,
            "states": _json_load(row.states, []),
            "transitions": _json_load(row.transitions, []),
            "confidence": row.confidence,
        }
        for row in session.scalars(
            select(StateMachine).where(StateMachine.project_id == project.id)
        )
    ]
    entities = _entity_map(backend)
    rules = [_rule_snapshot(rule) for rule in backend.businessRules]
    endpoints = []
    for endpoint in backend.endpoints:
        related = _related_rules(endpoint, rules)
        item = {
            "id": endpoint.id,
            "method": str(endpoint.method or "").upper(),
            "path": endpoint.path or "",
            "normalizedPath": normalize_path(endpoint.path),
            "controller": endpoint.controller or "",
            "methodName": endpoint.methodName or "",
            "file": endpoint.file or "",
            "line": endpoint.line or 0,
            "params": _expand_params(getattr(endpoint, "params", []) or [], entities),
            "auth": sorted(getattr(endpoint, "auth", []) or []),
            "rules": related,
        }
        item["fingerprint"] = _sha({
            "method": item["method"],
            "path": item["normalizedPath"],
            "controller": item["controller"],
            "methodName": item["methodName"],
            "params": item["params"],
            "auth": item["auth"],
            "rules": [rule.get("key") for rule in related],
        })
        endpoints.append(item)

    security = _build_security(project)
    transitions = _transition_snapshot(machines)
    snapshot_payload = {
        "endpoints": endpoints,
        "rules": rules,
        "stateTransitions": transitions,
        "security": security,
        "warnings": _json_load(analysis.warnings, []),
    }
    fingerprint = _sha(snapshot_payload)
    row = RequirementSnapshot(
        id=uuid.uuid4().hex,
        project_id=project.id,
        requirement_id=requirement_id,
        analysis_id=analysis.id,
        baseline_requirement_id=None,
        source=source,
        endpoints=_dumps(endpoints),
        rules=_dumps(rules),
        state_transitions=_dumps(transitions),
        security=_dumps(security),
        warnings=_dumps(snapshot_payload["warnings"]),
        fingerprint=fingerprint,
    )
    session.add(row)
    session.flush()
    logger.info(
        "需求快照建立 requirement=%s endpoints=%s rules=%s",
        requirement_id,
        len(endpoints),
        len(rules),
    )
    return snapshot_to_dict(row)


def snapshot_to_dict(row: RequirementSnapshot) -> dict:
    return {
        "id": row.id,
        "projectId": row.project_id,
        "requirementId": row.requirement_id,
        "analysisId": row.analysis_id,
        "source": row.source,
        "endpoints": _json_load(row.endpoints, []),
        "rules": _json_load(row.rules, []),
        "stateTransitions": _json_load(row.state_transitions, []),
        "security": _json_load(row.security, {}),
        "warnings": _json_load(row.warnings, []),
        "fingerprint": row.fingerprint,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }


def get_latest_snapshot(session: Session, requirement_id: str) -> RequirementSnapshot | None:
    return session.scalars(
        select(RequirementSnapshot)
        .where(RequirementSnapshot.requirement_id == requirement_id)
        .order_by(RequirementSnapshot.created_at.desc())
        .limit(1)
    ).first()


def ensure_current_snapshot(session: Session, requirement: Requirement) -> dict:
    row = get_latest_snapshot(session, requirement.id)
    latest = session.scalars(
        select(CodeAnalysis)
        .where(
            CodeAnalysis.project_id == requirement.project_id,
            CodeAnalysis.status == "success",
        )
        .order_by(CodeAnalysis.created_at.desc())
        .limit(1)
    ).first()
    # 已有快照但代码分析已经更新时，当前版本必须重建，避免拿旧快照做二期对比。
    if row is not None and (
        latest is None or not row.analysis_id or row.analysis_id == latest.id
    ):
        return snapshot_to_dict(row)
    return build_snapshot(session, requirement.id)


def _endpoint_reasons(old: dict, new: dict) -> list[str]:
    reasons: list[str] = []
    if str(old.get("path") or "") != str(new.get("path") or ""):
        reasons.append(f"路径变化：{old.get('path')} → {new.get('path')}")
    for field, label in (
        ("controller", "Controller"),
        ("methodName", "方法名"),
        ("method", "HTTP 方法"),
    ):
        if str(old.get(field) or "") != str(new.get(field) or ""):
            reasons.append(f"{label}变化：{old.get(field)} → {new.get(field)}")

    old_params = {f"{p.get('name')}|{p.get('source')}": p for p in old.get("params") or []}
    new_params = {f"{p.get('name')}|{p.get('source')}": p for p in new.get("params") or []}
    for key in sorted(set(new_params) - set(old_params)):
        reasons.append(f"新增参数：{new_params[key].get('name')}")
    for key in sorted(set(old_params) - set(new_params)):
        reasons.append(f"移除参数：{old_params[key].get('name')}")
    for key in sorted(set(old_params) & set(new_params)):
        before = old_params[key]
        after = new_params[key]
        if bool(before.get("required")) != bool(after.get("required")):
            reasons.append(
                f"参数 {after.get('name')} 必填性变化：{before.get('required')} → {after.get('required')}"
            )
        if sorted(before.get("constraints") or []) != sorted(after.get("constraints") or []):
            reasons.append(f"参数 {after.get('name')} 约束变化")

    if sorted(old.get("auth") or []) != sorted(new.get("auth") or []):
        reasons.append("接口鉴权要求变化")
    old_rules = {rule.get("key") for rule in old.get("rules") or []}
    new_rules = {rule.get("key") for rule in new.get("rules") or []}
    if old_rules != new_rules:
        reasons.append("关联业务规则变化")
    return reasons


def _sentence_for(text: str, needle: str) -> str:
    lowered = text.lower()
    index = lowered.find(needle.lower())
    if index < 0:
        return ""
    start = max(0, index - 100)
    end = min(len(text), index + len(needle) + 100)
    return text[start:end]


def explicit_deletion(content: str, endpoint: dict) -> bool:
    """只有文档在接口附近明确写删除/下线，才算删除依据。"""
    if not (content or "").strip():
        return False
    text = content.replace("\r", "\n")
    method = str(endpoint.get("method") or "").lower()
    path = str(endpoint.get("path") or "").lower()
    if not path:
        return False
    candidates = [f"{method} {path}", path, normalize_path(path)]
    for candidate in candidates:
        sentence = _sentence_for(text, candidate)
        if not sentence:
            continue
        if any(word in sentence for word in ("不删除", "无需删除", "保留", "暂不删除")):
            continue
        if any(word in sentence for word in DELETION_WORDS):
            return True
    return False


def _prd_refs(content: str) -> list[dict]:
    refs = []
    for method, path in PRD_ENDPOINT_RE.findall(content or ""):
        path = str(path).rstrip(".,;:")
        item = {"method": method.upper(), "path": path}
        item["key"] = endpoint_key(item)
        refs.append(item)
    return refs


def _add_change(
    target: list[dict],
    *,
    entity_type: str,
    change_type: str,
    match_key: str,
    old_data=None,
    new_data=None,
    old_ref: str | None = None,
    new_ref: str | None = None,
    reason: str = "",
    confidence: float = 1.0,
    requires_confirmation: bool = False,
) -> dict:
    change = {
        "entity_type": entity_type,
        "change_type": change_type,
        "match_key": match_key[:255],
        "old_ref": old_ref,
        "new_ref": new_ref,
        "old_data": old_data,
        "new_data": new_data,
        "reason": reason,
        "confidence": confidence,
        "requires_confirmation": 1 if requires_confirmation else 0,
        "review_status": "pending" if requires_confirmation else "auto",
    }
    target.append(change)
    return change


def _find_path_candidate(old: dict, candidates: list[dict]):
    scored = []
    old_norm = normalize_path(old.get("path"))
    for new in candidates:
        if str(old.get("method") or "").upper() != str(new.get("method") or "").upper():
            continue
        same_method_name = bool(
            old.get("methodName") and old.get("methodName") == new.get("methodName")
        )
        score = 3.0 if same_method_name else 0.0
        if old.get("controller") and old.get("controller") == new.get("controller"):
            score += 1
        similarity = SequenceMatcher(None, old_norm, normalize_path(new.get("path"))).ratio()
        if same_method_name and similarity >= 0.6:
            score += 1
        elif not same_method_name and similarity >= 0.85:
            score += 2
        # 只有方法名一致，或路径高度相似，才允许把两个不同 key 视为同一业务接口。
        if score >= 3 and (same_method_name or similarity >= 0.85):
            scored.append((score, similarity, new))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    if not scored:
        return None, []
    top = scored[0]
    ties = [item for item in scored if item[0] == top[0] and item[1] == top[1]]
    if len(ties) > 1:
        return None, ties
    return top[2], []


def compare_snapshots(
    baseline: dict,
    current: dict,
    requirement_content: str = "",
) -> tuple[list[dict], dict]:
    """对比两份快照，返回结构化变更和统计。不写数据库。"""
    changes: list[dict] = []
    old_endpoints = list(baseline.get("endpoints") or [])
    new_endpoints = list(current.get("endpoints") or [])
    old_by_key: dict[str, list[dict]] = {}
    new_by_key: dict[str, list[dict]] = {}
    for endpoint in old_endpoints:
        old_by_key.setdefault(endpoint_key(endpoint), []).append(endpoint)
    for endpoint in new_endpoints:
        new_by_key.setdefault(endpoint_key(endpoint), []).append(endpoint)

    matched_old: set[str] = set()
    matched_new: set[str] = set()

    for key in sorted(set(old_by_key) & set(new_by_key)):
        old_list = old_by_key[key]
        new_list = new_by_key[key]
        if len(old_list) == 1 and len(new_list) == 1:
            old, new = old_list[0], new_list[0]
            reasons = _endpoint_reasons(old, new)
            if reasons:
                _add_change(
                    changes,
                    entity_type="endpoint",
                    change_type="modified",
                    match_key=key,
                    old_data=old,
                    new_data=new,
                    old_ref=old.get("id"),
                    new_ref=new.get("id"),
                    reason="；".join(reasons),
                    confidence=0.92,
                )
            else:
                _add_change(
                    changes,
                    entity_type="endpoint",
                    change_type="unchanged",
                    match_key=key,
                    old_data=old,
                    new_data=new,
                    old_ref=old.get("id"),
                    new_ref=new.get("id"),
                    reason="接口、参数、鉴权与关联规则未发现变化",
                    confidence=1.0,
                )
            matched_old.add(old.get("id"))
            matched_new.add(new.get("id"))
        else:
            _add_change(
                changes,
                entity_type="endpoint",
                change_type="uncertain",
                match_key=key,
                old_data=old_list,
                new_data=new_list,
                reason="同一方法+规范化路径存在多个接口，需人工确认匹配关系",
                confidence=0.4,
                requires_confirmation=True,
            )
            matched_old.update(item.get("id") for item in old_list)
            matched_new.update(item.get("id") for item in new_list)

    remaining_old = [ep for ep in old_endpoints if ep.get("id") not in matched_old]
    remaining_new = [ep for ep in new_endpoints if ep.get("id") not in matched_new]

    for old in remaining_old:
        candidate, ties = _find_path_candidate(old, remaining_new)
        if candidate is not None:
            reasons = _endpoint_reasons(old, candidate)
            _add_change(
                changes,
                entity_type="endpoint",
                change_type="modified",
                match_key=endpoint_key(candidate),
                old_data=old,
                new_data=candidate,
                old_ref=old.get("id"),
                new_ref=candidate.get("id"),
                reason="；".join(reasons) + "；路径/签名变化，按方法名/Controller 关联，需确认",
                confidence=0.6,
                requires_confirmation=True,
            )
            matched_old.add(old.get("id"))
            matched_new.add(candidate.get("id"))
        elif ties:
            _add_change(
                changes,
                entity_type="endpoint",
                change_type="uncertain",
                match_key=endpoint_key(old),
                old_data=old,
                new_data=[item[2] for item in ties],
                old_ref=old.get("id"),
                reason="存在多个疑似关联接口，无法可靠判断是否为同一业务接口，需人工确认",
                confidence=0.35,
                requires_confirmation=True,
            )
            matched_old.add(old.get("id"))

    for old in remaining_old:
        if old.get("id") in matched_old:
            continue
        if explicit_deletion(requirement_content, old):
            _add_change(
                changes,
                entity_type="endpoint",
                change_type="deleted",
                match_key=endpoint_key(old),
                old_data=old,
                old_ref=old.get("id"),
                reason="技术文档明确删除该接口；历史用例与执行记录保留，确认后仅标记失效",
                confidence=0.95,
                requires_confirmation=True,
            )
        else:
            _add_change(
                changes,
                entity_type="endpoint",
                change_type="uncertain",
                match_key=endpoint_key(old),
                old_data=old,
                old_ref=old.get("id"),
                reason="二期代码分析未发现该接口，但技术文档未明确删除；不能直接判定为删除",
                confidence=0.4,
                requires_confirmation=True,
            )
        matched_old.add(old.get("id"))

    for new in remaining_new:
        if new.get("id") in matched_new:
            continue
        _add_change(
            changes,
            entity_type="endpoint",
            change_type="added",
            match_key=endpoint_key(new),
            new_data=new,
            new_ref=new.get("id"),
            reason="二期代码分析新增接口",
            confidence=0.95,
        )
        matched_new.add(new.get("id"))

    # 业务规则：先按描述匹配，再比较守卫；缺失规则只标记不确定，不直接删除。
    old_rules = list(baseline.get("rules") or [])
    new_rules = list(current.get("rules") or [])
    old_rule_by_desc: dict[str, list[dict]] = {}
    new_rule_by_desc: dict[str, list[dict]] = {}
    for rule in old_rules:
        old_rule_by_desc.setdefault(_norm_text(rule.get("description")), []).append(rule)
    for rule in new_rules:
        new_rule_by_desc.setdefault(_norm_text(rule.get("description")), []).append(rule)
    matched_rule_ids: set[str] = set()
    for desc in sorted(set(old_rule_by_desc) & set(new_rule_by_desc)):
        before = old_rule_by_desc[desc][0]
        after = new_rule_by_desc[desc][0]
        matched_rule_ids.add(str(before.get("id")))
        matched_rule_ids.add(str(after.get("id")))
        if _norm_text(before.get("guard")) != _norm_text(after.get("guard")):
            _add_change(
                changes,
                entity_type="business_rule",
                change_type="modified",
                match_key=desc[:255],
                old_data=before,
                new_data=after,
                old_ref=before.get("id"),
                new_ref=after.get("id"),
                reason="业务规则描述相同但守卫条件变化",
                confidence=0.9,
            )
    for rule in old_rules:
        if str(rule.get("id")) in matched_rule_ids:
            continue
        _add_change(
            changes,
            entity_type="business_rule",
            change_type="uncertain",
            match_key=_norm_text(rule.get("description"))[:255],
            old_data=rule,
            old_ref=rule.get("id"),
            reason="二期代码分析未发现该业务规则；技术文档未明确删除，需人工确认",
            confidence=0.4,
            requires_confirmation=True,
        )
    for rule in new_rules:
        if str(rule.get("id")) in matched_rule_ids:
            continue
        _add_change(
            changes,
            entity_type="business_rule",
            change_type="added",
            match_key=_norm_text(rule.get("description"))[:255],
            new_data=rule,
            new_ref=rule.get("id"),
            reason="二期代码分析新增业务规则",
            confidence=0.9,
        )

    # 状态流转
    old_transitions = {item.get("key"): item for item in baseline.get("stateTransitions") or []}
    new_transitions = {item.get("key"): item for item in current.get("stateTransitions") or []}
    for key in sorted(set(new_transitions) - set(old_transitions)):
        _add_change(
            changes,
            entity_type="state_transition",
            change_type="added",
            match_key=str(key)[:255],
            new_data=new_transitions[key],
            new_ref=new_transitions[key].get("id"),
            reason="新增状态流转",
            confidence=0.9,
        )
    for key in sorted(set(old_transitions) - set(new_transitions)):
        _add_change(
            changes,
            entity_type="state_transition",
            change_type="uncertain",
            match_key=str(key)[:255],
            old_data=old_transitions[key],
            old_ref=old_transitions[key].get("id"),
            reason="二期代码分析未发现该状态流转；需人工确认是否删除",
            confidence=0.4,
            requires_confirmation=True,
        )

    # 公共鉴权配置
    old_security = baseline.get("security") or {}
    new_security = current.get("security") or {}
    if (
        old_security.get("available")
        and new_security.get("available")
        and old_security.get("hash") != new_security.get("hash")
    ):
        _add_change(
            changes,
            entity_type="permission",
            change_type="modified",
            match_key="security-config",
            old_data=old_security,
            new_data=new_security,
            reason="公共鉴权配置发生变化，可能影响所有受保护接口；基于安全配置文本推断",
            confidence=0.75,
            requires_confirmation=True,
        )

    # 文档提到但代码分析不存在的接口 → 冲突，不静默生成
    for ref in _prd_refs(requirement_content):
        key = ref["key"]
        if key in new_by_key:
            continue
        if key in old_by_key and explicit_deletion(requirement_content, old_by_key[key][0]):
            continue
        _add_change(
            changes,
            entity_type="conflict",
            change_type="uncertain",
            match_key=key,
            old_data=old_by_key.get(key, []),
            new_data=None,
            reason="技术文档提到该接口，但二期代码分析未找到；请确认代码分析是否为最新",
            confidence=0.3,
            requires_confirmation=True,
        )

    endpoint_counts: dict[str, int] = {}
    rule_counts: dict[str, int] = {}
    transition_counts: dict[str, int] = {}
    for change in changes:
        if change["entity_type"] == "endpoint":
            endpoint_counts[change["change_type"]] = endpoint_counts.get(change["change_type"], 0) + 1
        elif change["entity_type"] == "business_rule":
            rule_counts[change["change_type"]] = rule_counts.get(change["change_type"], 0) + 1
        elif change["entity_type"] == "state_transition":
            transition_counts[change["change_type"]] = transition_counts.get(change["change_type"], 0) + 1

    conflicts = sum(1 for change in changes if change["entity_type"] == "conflict")
    pending = sum(1 for change in changes if change["requires_confirmation"])
    summary = {
        "baselineEndpoints": len(old_endpoints),
        "currentEndpoints": len(new_endpoints),
        "endpointCounts": endpoint_counts,
        "ruleCounts": rule_counts,
        "stateTransitionCounts": transition_counts,
        "conflicts": conflicts,
        "requiresConfirmation": pending,
        "unchangedEndpoints": endpoint_counts.get("unchanged", 0),
        "hasChanges": any(
            change["change_type"] in ("added", "modified", "deleted")
            and change["entity_type"] != "conflict"
            for change in changes
        ),
    }
    return changes, summary


def _change_to_dict(row: RequirementChange) -> dict:
    return {
        "id": row.id,
        "runId": row.run_id,
        "entityType": row.entity_type,
        "changeType": row.change_type,
        "matchKey": row.match_key,
        "oldRef": row.old_ref,
        "newRef": row.new_ref,
        "oldData": _json_load(row.old_data, None),
        "newData": _json_load(row.new_data, None),
        "reason": row.reason or "",
        "confidence": row.confidence,
        "requiresConfirmation": bool(row.requires_confirmation),
        "reviewStatus": row.review_status,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }


def _case_change_to_dict(
    row: RequirementCaseChange,
    case_by_id: dict[str, TestCase],
) -> dict:
    target = case_by_id.get(row.test_case_id or "")
    baseline = case_by_id.get(row.baseline_case_id or "")
    return {
        "id": row.id,
        "runId": row.run_id,
        "changeId": row.change_id,
        "testCaseId": row.test_case_id,
        "baselineCaseId": row.baseline_case_id,
        "action": row.action,
        "targetCaseTitle": target.title if target else "",
        "baselineCaseTitle": baseline.title if baseline else "",
        "regressionImpact": row.regression_impact,
        "regressionReason": row.regression_reason or "",
        "regressionStatus": row.regression_status,
        "confidence": row.confidence,
        "reviewStatus": row.review_status,
        "reason": row.reason or "",
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }


def _case_changes_summary(rows: list[RequirementCaseChange]) -> dict:
    by_action: dict[str, int] = {}
    by_impact: dict[str, int] = {}
    regression_baseline_ids: set[str] = set()
    pending = 0
    for row in rows:
        by_action[row.action] = by_action.get(row.action, 0) + 1
        by_impact[row.regression_impact] = by_impact.get(row.regression_impact, 0) + 1
        if (
            row.baseline_case_id
            and row.regression_impact in ("direct", "indirect")
            and row.regression_status != "rejected"
        ):
            regression_baseline_ids.add(row.baseline_case_id)
        if row.review_status == "pending":
            pending += 1
    regression = len(regression_baseline_ids)
    return {
        "actions": by_action,
        "regressionImpact": by_impact,
        "regressionRecommended": regression,
        "pendingReview": pending,
        "created": by_action.get("created", 0),
        "updated": by_action.get("updated", 0),
        "reused": by_action.get("reused", 0),
        "pendingDelete": by_action.get("pending_delete", 0),
        "missing": by_action.get("missing", 0),
    }


def _run_to_dict(
    session: Session,
    run: RequirementChangeRun,
    *,
    include_changes: bool = True,
) -> dict:
    changes = list(
        session.scalars(
            select(RequirementChange)
            .where(RequirementChange.run_id == run.id)
            .order_by(RequirementChange.created_at, RequirementChange.id)
        )
    )
    case_rows = list(
        session.scalars(
            select(RequirementCaseChange)
            .where(RequirementCaseChange.run_id == run.id)
            .order_by(RequirementCaseChange.created_at, RequirementCaseChange.id)
        )
    )
    case_ids = {
        item
        for row in case_rows
        for item in (row.test_case_id, row.baseline_case_id)
        if item
    }
    case_by_id = {
        tc.id: tc
        for tc in (
            session.scalars(select(TestCase).where(TestCase.id.in_(case_ids))).all()
            if case_ids
            else []
        )
    }
    req = session.get(Requirement, run.requirement_id)
    baseline = session.get(Requirement, run.baseline_requirement_id)
    summary = _json_load(run.summary, {})
    summary["caseChanges"] = _case_changes_summary(case_rows)
    return {
        "id": run.id,
        "projectId": run.project_id,
        "requirementId": run.requirement_id,
        "requirementVersion": req.version if req else "",
        "requirementName": req.name if req else "",
        "baselineRequirementId": run.baseline_requirement_id,
        "baselineRequirementVersion": baseline.version if baseline else "",
        "baselineRequirementName": baseline.name if baseline else "",
        "status": run.status,
        "mode": run.mode,
        "profileId": run.profile_id,
        "summary": summary,
        "errorMessage": run.error_message,
        "createdAt": run.created_at.isoformat() if run.created_at else None,
        "updatedAt": run.updated_at.isoformat() if run.updated_at else None,
        "changes": [_change_to_dict(row) for row in changes] if include_changes else [],
        "caseChanges": [
            _case_change_to_dict(row, case_by_id) for row in case_rows
        ] if include_changes else [],
    }


def list_runs(session: Session, requirement_id: str, limit: int = 20) -> list[dict]:
    rows = list(
        session.scalars(
            select(RequirementChangeRun)
            .where(RequirementChangeRun.requirement_id == requirement_id)
            .order_by(RequirementChangeRun.created_at.desc())
            .limit(max(1, min(100, limit)))
        )
    )
    return [_run_to_dict(session, row, include_changes=False) for row in rows]


def get_run(session: Session, run_id: str) -> dict:
    run = session.get(RequirementChangeRun, run_id)
    if run is None:
        raise BizError(40411, f"增量分析运行不存在: {run_id}", http_status=404)
    return _run_to_dict(session, run)


def _require_baseline_snapshot(session: Session, baseline: Requirement) -> dict:
    row = get_latest_snapshot(session, baseline.id)
    if row is None:
        raise BizError(
            40950,
            f"基线版本 {baseline.version} 还没有接口快照，请先为基线建立快照后再做增量分析",
            http_status=409,
        )
    return snapshot_to_dict(row)


def analyze_incremental(
    session: Session,
    requirement_id: str,
    baseline_requirement_id: str,
    *,
    mode: str = "auto",
    profile_id: str | None = None,
) -> dict:
    """对比基线与当前需求，落库变更清单，等待用户确认/生成。"""
    current_req = session.get(Requirement, requirement_id)
    baseline = session.get(Requirement, baseline_requirement_id)
    if current_req is None:
        raise BizError(40406, f"需求版本不存在: {requirement_id}", http_status=404)
    if baseline is None:
        raise BizError(40406, f"基线版本不存在: {baseline_requirement_id}", http_status=404)
    if current_req.project_id != baseline.project_id:
        raise BizError(40061, "基线与当前需求不属于同一项目")
    if current_req.id == baseline.id:
        raise BizError(40062, "基线与当前需求不能是同一个版本")
    if not (current_req.content or "").strip():
        raise BizError(40014, "当前需求内容为空，请先填写或上传二期技术文档")

    baseline_payload = _require_baseline_snapshot(session, baseline)
    current_payload = ensure_current_snapshot(session, current_req)
    changes, summary = compare_snapshots(
        baseline_payload,
        current_payload,
        current_req.content or "",
    )

    run = RequirementChangeRun(
        id=uuid.uuid4().hex,
        project_id=current_req.project_id,
        requirement_id=current_req.id,
        baseline_requirement_id=baseline.id,
        baseline_snapshot_id=baseline_payload.get("id"),
        current_snapshot_id=current_payload.get("id"),
        status="review",
        mode=mode,
        profile_id=profile_id,
        summary=_dumps(summary),
    )
    session.add(run)
    session.flush()
    for change in changes:
        session.add(
            RequirementChange(
                id=uuid.uuid4().hex,
                run_id=run.id,
                project_id=current_req.project_id,
                requirement_id=current_req.id,
                baseline_requirement_id=baseline.id,
                entity_type=change["entity_type"],
                change_type=change["change_type"],
                match_key=change["match_key"],
                old_ref=change.get("old_ref"),
                new_ref=change.get("new_ref"),
                old_data=_dumps(change.get("old_data")) if change.get("old_data") is not None else None,
                new_data=_dumps(change.get("new_data")) if change.get("new_data") is not None else None,
                reason=change.get("reason") or "",
                confidence=change.get("confidence") or 0.0,
                requires_confirmation=change.get("requires_confirmation") or 0,
                review_status=change.get("review_status") or "auto",
            )
        )
    session.flush()
    logger.info(
        "增量分析完成 requirement=%s baseline=%s changes=%s pending=%s",
        current_req.id,
        baseline.id,
        len(changes),
        summary.get("requiresConfirmation", 0),
    )
    return _run_to_dict(session, run)


def review_change(session: Session, run_id: str, change_id: str, status: str) -> dict:
    if status not in ("confirmed", "rejected"):
        raise BizError(40063, "审核状态只能是 confirmed 或 rejected")
    run = session.get(RequirementChangeRun, run_id)
    change = session.get(RequirementChange, change_id)
    if run is None or change is None or change.run_id != run.id:
        raise BizError(40411, "变更记录不存在", http_status=404)
    change.review_status = status
    if change.entity_type == "endpoint" and change.change_type == "deleted":
        rows = list(
            session.scalars(
                select(RequirementCaseChange).where(
                    RequirementCaseChange.run_id == run.id,
                    RequirementCaseChange.change_id == change.id,
                    RequirementCaseChange.action == "pending_delete",
                )
            )
        )
        for row in rows:
            row.review_status = "confirmed" if status == "confirmed" else "rejected"
    session.flush()
    return _run_to_dict(session, run)


def review_regression(session: Session, case_change_id: str, status: str) -> dict:
    if status not in ("accepted", "rejected"):
        raise BizError(40064, "回归推荐状态只能是 accepted 或 rejected")
    row = session.get(RequirementCaseChange, case_change_id)
    if row is None:
        raise BizError(40412, "用例变更记录不存在", http_status=404)
    row.regression_status = status
    if status == "rejected":
        row.regression_impact = "none"
    session.flush()
    run = session.get(RequirementChangeRun, row.run_id)
    return _run_to_dict(session, run) if run else {}


def _change_context(changes: list[RequirementChange]) -> dict:
    ctx = {
        "direct_old_ids": set(),
        "deleted": {},
        "uncertain": {},
        "rejected_deleted": {},
        "controllers": set(),
        "files": set(),
        "auth": set(),
        "rule_ids": set(),
        "security_change": None,
        "change_by_old": {},
        "change_by_new": {},
        "rule_changes": [],
    }
    for change in changes:
        old = _json_load(change.old_data, {}) or {}
        new = _json_load(change.new_data, {}) or {}
        if change.entity_type == "endpoint":
            if change.old_ref:
                ctx["change_by_old"][change.old_ref] = change
            if change.new_ref:
                ctx["change_by_new"][change.new_ref] = change
            if change.review_status == "rejected":
                # 驳回的删除视为保留；驳回的新增/修改视为未变化，不参与影响推断。
                if change.change_type == "deleted" and change.old_ref:
                    ctx["rejected_deleted"][change.old_ref] = change
                continue
            for endpoint in (old, new):
                if isinstance(endpoint, dict):
                    if endpoint.get("controller"):
                        ctx["controllers"].add(endpoint.get("controller"))
                    if endpoint.get("file"):
                        ctx["files"].add(endpoint.get("file"))
                    ctx["auth"].update(endpoint.get("auth") or [])
            if change.change_type == "deleted" and change.old_ref:
                ctx["deleted"][change.old_ref] = change
                ctx["direct_old_ids"].add(change.old_ref)
            elif change.change_type == "uncertain" and change.old_ref:
                ctx["uncertain"][change.old_ref] = change
            elif change.change_type == "modified" and change.old_ref:
                ctx["direct_old_ids"].add(change.old_ref)
        elif change.entity_type == "business_rule" and change.review_status != "rejected":
            ctx["rule_changes"].append(change)
            if change.old_ref:
                ctx["rule_ids"].add(change.old_ref)
            if change.new_ref:
                ctx["rule_ids"].add(change.new_ref)
        elif change.entity_type == "permission" and change.review_status != "rejected":
            ctx["security_change"] = change
    return ctx


def _case_endpoints(
    case: TestCase,
    point: TestPoint | None,
    baseline_eps: dict[str, dict],
) -> list[dict]:
    refs = _json_load(case.coverage_refs, {})
    ids = refs.get("endpointIds") or []
    found = [baseline_eps[item] for item in ids if item in baseline_eps]
    if found:
        return list({ep.get("id"): ep for ep in found}.values())
    if point is not None:
        endpoint_text = str(point.endpoint or "").strip()
        match = re.match(r"^([A-Za-z]+)\s+(.+)$", endpoint_text)
        if match:
            key = endpoint_key({"method": match.group(1), "path": match.group(2)})
            for endpoint in baseline_eps.values():
                if endpoint_key(endpoint) == key:
                    return [endpoint]
        route = normalize_path(point.page_route or "")
        if route and route != "/":
            for endpoint in baseline_eps.values():
                if normalize_path(endpoint.get("path")) == route:
                    return [endpoint]
    return []


def _impact_for_case(
    endpoints: list[dict],
    refs: dict,
    ctx: dict,
) -> tuple[str, str, str | None]:
    reasons: list[str] = []
    change_id = None
    if not endpoints:
        rule_hit = set(refs.get("ruleIds") or []) & ctx["rule_ids"]
        if rule_hit:
            return "direct", "历史用例关联的业务规则发生变化", None
        return "none", "无法从历史用例关联到接口，建议人工确认", None

    for endpoint in endpoints:
        change = ctx["rejected_deleted"].get(endpoint.get("id"))
        if change is not None:
            return "direct", "删除变更被驳回，保留历史用例并建议回归", change.id
        change = ctx["change_by_old"].get(endpoint.get("id"))
        if change is not None and change.change_type in ("modified", "deleted"):
            reasons.append(f"接口 {endpoint_key(endpoint)} 发生变更")
            change_id = change.id

    rule_hit = set(refs.get("ruleIds") or []) & ctx["rule_ids"]
    if rule_hit:
        reasons.append("关联业务规则发生变化")
        change_id = change_id or (ctx["rule_changes"][0].id if ctx["rule_changes"] else None)

    if reasons:
        return "direct", "；".join(reasons), change_id

    security_change = ctx["security_change"]
    if security_change is not None:
        protected = any(endpoint.get("auth") for endpoint in endpoints)
        return (
            "direct" if protected else "indirect",
            "公共鉴权配置变化" + ("，且该接口受保护" if protected else "，可能间接影响"),
            security_change.id,
        )

    for endpoint in endpoints:
        if endpoint.get("controller") in ctx["controllers"] or endpoint.get("file") in ctx["files"]:
            return "indirect", "与变更接口共享 Controller / 源文件，建议回归", None
        if set(endpoint.get("auth") or []) & ctx["auth"]:
            return "indirect", "与变更接口共享鉴权要求，建议回归", None
    return "none", "未发现直接或间接影响", None


def _success_text(frontend) -> str:
    for text in getattr(frontend, "uiTexts", []) or []:
        if "成功" in text:
            return f"「{text}」"
    return "「操作成功」"


def _constraint_expected(param: dict) -> str:
    for constraint in param.get("constraints") or []:
        value = str(constraint)
        if re.search(r"[\u4e00-\u9fff]", value):
            message = re.sub(r"^[^(]+\(|\)$", "", value)
            if message:
                return f"「{message}」"
    return "「参数校验失败」"


def _make_point(
    *,
    change: RequirementChange,
    endpoint: dict,
    title: str,
    category: str,
    priority: str,
    expected: str,
    fields: list[str] | None = None,
    description: str = "",
    module: str = "",
    ref_kind: str = "endpoint",
    page_route: str | None = None,
) -> dict:
    route = page_route if page_route is not None else ""
    # 纯 API 接口没有 UI 页面，预期结果应描述 HTTP 行为，不能借用别的页面文案。
    if ref_kind == "endpoint" and not route:
        if category == "permission":
            expected = "接口返回未授权（HTTP 401/403）"
        elif category in ("negative", "boundary"):
            expected = "接口返回参数校验错误（HTTP 4xx）"
        else:
            expected = "接口返回成功响应（HTTP 2xx）"
    signature = _sha([title, category, expected, route])[:12]
    return {
        "id": uuid.uuid4().hex,
        "title": title[:255],
        "category": category,
        "priority": priority,
        "description": description or title,
        "pageRoute": route,
        "endpoint": f"{endpoint.get('method')} {endpoint.get('path')}".strip(),
        "fields": (fields or [])[:20],
        "expected": expected,
        "source": "incremental",
        "_change_id": change.id,
        "_new_ref": endpoint.get("id") if ref_kind == "endpoint" else change.new_ref,
        "_endpoint_key": endpoint_key(endpoint) if ref_kind == "endpoint" else change.match_key,
        "_module": module or endpoint.get("controller") or "接口增量",
        "_point_sig": signature,
        # 纯 API 接口或业务规则没有可执行 UI 页面时，只生成 manual/待补充用例
        "_manual_only": ref_kind != "endpoint" or not route,
    }


def _infer_page_route(endpoint: dict, frontend) -> str:
    """把后端接口映射到已有前端页面；纯 API 接口返回空，避免伪造 UI 步骤。"""
    pages = getattr(frontend, "pages", []) or []
    routes = [str(page.route or "") for page in pages if page.route]
    if not routes:
        return ""
    path = normalize_path(endpoint.get("path"))
    method = str(endpoint.get("method") or "").upper()
    path_text = path.lower()
    for route in routes:
        if normalize_path(route) == path:
            return route
    if method == "GET" and path_text.endswith("/orders"):
        for route in routes:
            if "list" in route.lower():
                return route
    if method == "POST" and path_text.endswith("/orders"):
        for route in routes:
            if "create" in route.lower() or "add" in route.lower():
                return route
    if method in ("PUT", "DELETE") and "{param}" in path:
        for route in routes:
            if "list" in route.lower() or "detail" in route.lower() or "edit" in route.lower():
                return route
    if any(word in path_text for word in ("/pay", "/ship", "/complete", "/cancel", "/refund")):
        for route in routes:
            if "list" in route.lower():
                return route
    if method == "GET" and "{param}" in path:
        for route in routes:
            if "detail" in route.lower():
                return route
    return ""


def _build_points_for_changes(
    changes: list[RequirementChange],
    current_snapshot: dict,
    baseline_snapshot: dict,
    frontend,
) -> list[dict]:
    points: list[dict] = []
    success = _success_text(frontend)
    transitions = current_snapshot.get("stateTransitions") or []
    rule_changes = [item for item in changes if item.entity_type == "business_rule" and item.review_status != "rejected"]

    for change in changes:
        if change.review_status == "rejected":
            continue
        if change.entity_type != "endpoint" or change.change_type not in ("added", "modified"):
            continue
        old = _json_load(change.old_data, {}) or {}
        new = _json_load(change.new_data, {}) or {}
        endpoint = new if new else old
        if not endpoint:
            continue
        page_route = _infer_page_route(endpoint, frontend)
        old_params = {f"{p.get('name')}|{p.get('source')}": p for p in old.get("params") or []}
        new_params = {f"{p.get('name')}|{p.get('source')}": p for p in new.get("params") or []}
        changed_params = []
        for key in sorted(set(old_params) | set(new_params)):
            before = old_params.get(key)
            after = new_params.get(key)
            if before is None or after is None:
                changed_params.append(after or before)
            elif (
                bool(before.get("required")) != bool(after.get("required"))
                or sorted(before.get("constraints") or []) != sorted(after.get("constraints") or [])
            ):
                changed_params.append(after)

        if change.change_type == "added":
            points.append(_make_point(
                change=change,
                endpoint=endpoint,
                title=f"{endpoint.get('method')} {endpoint.get('path')} 正常请求",
                category="functional",
                priority="P0",
                expected=success,
                fields=[p.get("name") for p in new_params.values() if p.get("required")],
                description="新增接口的主流程回归",
                page_route=page_route,
            ))
            for param in changed_params[:4]:
                points.append(_make_point(
                    change=change,
                    endpoint=endpoint,
                    title=f"{endpoint.get('method')} {endpoint.get('path')} {param.get('name')} 参数校验",
                    category="negative" if param.get("required") else "boundary",
                    priority="P1",
                    expected=_constraint_expected(param),
                    fields=[param.get("name")],
                    description=f"新增参数 {param.get('name')}，来源 {param.get('source')}",
                    page_route=page_route,
                ))
            if endpoint.get("auth"):
                points.append(_make_point(
                    change=change,
                    endpoint=endpoint,
                    title=f"{endpoint.get('method')} {endpoint.get('path')} 鉴权校验",
                    category="permission",
                    priority="P1",
                    expected="「无权限」",
                    description=f"接口鉴权要求：{'；'.join(endpoint.get('auth') or [])}",
                    page_route=page_route,
                ))
            for transition in transitions:
                trigger = str(transition.get("trigger") or "")
                text = f"{endpoint.get('path')} {endpoint.get('methodName')}"
                if trigger and trigger in text:
                    points.append(_make_point(
                        change=change,
                        endpoint=endpoint,
                        title=f"{endpoint.get('method')} {endpoint.get('path')} {trigger}状态流转",
                        category="state",
                        priority="P1",
                        expected=f"「{trigger}成功」",
                        description=f"状态从 {transition.get('from')} 到 {transition.get('to')}",
                        page_route=page_route,
                    ))
        else:
            for param in changed_params[:4]:
                action = "新增" if param.get("name") not in {p.get("name") for p in old_params.values()} else "修改"
                points.append(_make_point(
                    change=change,
                    endpoint=endpoint,
                    title=f"{endpoint.get('method')} {endpoint.get('path')} {param.get('name')} 参数变更校验",
                    category="negative" if param.get("required") else "boundary",
                    priority="P1",
                    expected=_constraint_expected(param),
                    fields=[param.get("name")],
                    description=f"{action}参数 {param.get('name')}，需验证新约束",
                    page_route=page_route,
                ))
            if sorted(old.get("auth") or []) != sorted(new.get("auth") or []):
                points.append(_make_point(
                    change=change,
                    endpoint=endpoint,
                    title=f"{endpoint.get('method')} {endpoint.get('path')} 鉴权变更校验",
                    category="permission",
                    priority="P1",
                    expected="「无权限」",
                    description="接口鉴权要求发生变化",
                    page_route=page_route,
                ))
            if old.get("path") != new.get("path"):
                points.append(_make_point(
                    change=change,
                    endpoint=endpoint,
                    title=f"{endpoint.get('method')} {endpoint.get('path')} 新路径主流程",
                    category="functional",
                    priority="P0",
                    expected=success,
                    description="接口路径变化，验证新路径可访问",
                    page_route=page_route,
                ))
            if not changed_params and sorted(old.get("auth") or []) == sorted(new.get("auth") or []) and old.get("path") == new.get("path"):
                points.append(_make_point(
                    change=change,
                    endpoint=endpoint,
                    title=f"{endpoint.get('method')} {endpoint.get('path')} 变更回归",
                    category="functional",
                    priority="P0",
                    expected=success,
                    description="接口发生变更，执行主流程回归",
                    page_route=page_route,
                ))

    for change in rule_changes[:10]:
        new = _json_load(change.new_data, {}) or {}
        description = str(new.get("description") or change.match_key or "业务规则变更")
        points.append(_make_point(
            change=change,
            endpoint={"method": "", "path": "", "id": ""},
            title=f"业务规则：{description}"[:255],
            category="functional",
            priority="P1",
            expected=f"「{description}」",
            description=f"业务规则发生变化：{description}",
            module="业务规则",
            ref_kind="rule",
        ))
    return points[:40]


def _manual_case(point: dict, requirement: Requirement) -> GeneratedCase:
    return GeneratedCase(
        title=str(point.get("title") or "增量用例")[:255],
        module=str(point.get("_module") or "接口增量")[:120],
        type=point.get("category") if point.get("category") in ("positive", "negative", "boundary", "state") else "positive",
        priority=point.get("priority") or "P1",
        preconditions=[f"需求版本：{requirement.version} {requirement.name}"],
        steps=[str(point.get("description") or point.get("title") or "")],
        expected_results=[str(point.get("expected") or point.get("title") or "操作成功")],
        structured_steps=[],
        test_data={},
        coverage_refs={
            "endpointIds": [point["_new_ref"]] if point.get("_change_id") and point.get("_new_ref") and not point.get("_module") == "业务规则" else [],
            "ruleIds": [point["_new_ref"]] if point.get("_module") == "业务规则" and point.get("_new_ref") else [],
            "transitionIds": [],
            "testPointIds": [],
        },
        source="rule",
        confidence=0.5,
        test_point_id=point.get("id"),
    )


def _generate_cases_for_points(
    session: Session,
    requirement: Requirement,
    points: list[dict],
    *,
    mode: str,
    profile_id: str | None,
    backend=None,
    frontend=None,
    machines=None,
) -> list[GeneratedCase]:
    if not points:
        return []
    if backend is None or frontend is None:
        try:
            backend, frontend, machines = generation_service.get_generation_context(
                session, requirement.project_id
            )
        except BizError:
            backend, frontend, machines = BackendResult(), FrontendResult(), []
    cases: list[GeneratedCase] = []
    manual_only = [point for point in points if point.get("_manual_only")]
    ui_points = [point for point in points if not point.get("_manual_only")]
    if ui_points and mode in ("llm", "auto"):
        client = settings_service.get_text_client(profile_id)
        if client.available():
            try:
                cases.extend(generation_service._gen_llm_for_points(
                    client, requirement, ui_points, backend, frontend, machines
                ))
            except LlmUnavailable as exc:
                logger.warning("增量用例 LLM 生成失败，降级规则: %s", exc)
    covered = {case.test_point_id for case in cases if case.test_point_id}
    missing = [point for point in ui_points if point["id"] not in covered]
    if missing:
        cases.extend(generate_rule_cases_for_points(missing, backend, frontend, machines))
    covered = {case.test_point_id for case in cases if case.test_point_id}
    for point in ui_points:
        if point["id"] not in covered:
            cases.append(_manual_case(point, requirement))
    for point in manual_only:
        cases.append(_manual_case(point, requirement))
    return cases


def _generated_from_case(case: TestCase) -> GeneratedCase:
    return GeneratedCase(
        title=case.title,
        module=case.module,
        type=case.type,
        priority=case.priority,
        preconditions=_json_load(case.preconditions, []),
        steps=_json_load(case.steps, []),
        expected_results=generation_service._as_text_list(_json_load(case.expected_results, [])),
        structured_steps=_json_load(case.structured_steps, []),
        test_data=_json_load(case.test_data, {}),
        coverage_refs=_json_load(case.coverage_refs, {}),
        source=case.source,
        confidence=case.confidence,
    )


def _remap_coverage_refs(refs: dict, baseline: dict, current: dict) -> dict:
    baseline_eps = {ep.get("id"): ep for ep in baseline.get("endpoints") or []}
    current_by_key = {endpoint_key(ep): ep for ep in current.get("endpoints") or []}
    baseline_rules = {rule.get("id"): rule for rule in baseline.get("rules") or []}
    current_rules = {
        _norm_text(rule.get("description")): rule for rule in current.get("rules") or []
    }
    baseline_transitions = {
        item.get("id"): item for item in baseline.get("stateTransitions") or []
    }
    current_transitions = {
        item.get("key"): item for item in current.get("stateTransitions") or []
    }
    mapped: dict[str, list[str]] = {"endpointIds": [], "ruleIds": [], "transitionIds": [], "testPointIds": []}
    for old_id in refs.get("endpointIds") or []:
        old = baseline_eps.get(old_id)
        if old:
            new = current_by_key.get(endpoint_key(old))
            if new:
                mapped["endpointIds"].append(new.get("id"))
    for old_id in refs.get("ruleIds") or []:
        old = baseline_rules.get(old_id)
        if old:
            new = current_rules.get(_norm_text(old.get("description")))
            if new:
                mapped["ruleIds"].append(new.get("id"))
    for old_id in refs.get("transitionIds") or []:
        old = baseline_transitions.get(old_id)
        if old:
            new = current_transitions.get(old.get("key"))
            if new:
                mapped["transitionIds"].append(new.get("id"))
    for key in mapped:
        mapped[key] = list(dict.fromkeys(item for item in mapped[key] if item))
    return mapped


def _ensure_mapping(session: Session, requirement_id: str, case_id: str) -> None:
    existing = session.scalars(
        select(RequirementCase).where(
            RequirementCase.requirement_id == requirement_id,
            RequirementCase.test_case_id == case_id,
        )
    ).first()
    if existing is None:
        session.add(
            RequirementCase(
                id=uuid.uuid4().hex,
                requirement_id=requirement_id,
                test_case_id=case_id,
                test_point_id=None,
            )
        )


def _save_incremental_case(
    session: Session,
    run: RequirementChangeRun,
    *,
    generated: GeneratedCase,
    action: str,
    change_id: str | None,
    dedupe_key: str,
    baseline_case_id: str | None,
    impact: str,
    reason: str,
    review_status: str = "auto",
) -> RequirementCaseChange:
    existing = session.scalars(
        select(RequirementCaseChange)
        .where(
            RequirementCaseChange.requirement_id == run.requirement_id,
            RequirementCaseChange.dedupe_key == dedupe_key,
        )
        .order_by(RequirementCaseChange.created_at.desc())
        .limit(1)
    ).first()
    target_case = session.get(TestCase, existing.test_case_id) if existing and existing.test_case_id else None
    if target_case is None:
        target_case = generation_service._save_case(
            session,
            run.project_id,
            generated,
            requirement_id=run.requirement_id,
            test_point_id=None,
        )
    else:
        _ensure_mapping(session, run.requirement_id, target_case.id)

    row = session.scalars(
        select(RequirementCaseChange).where(
            RequirementCaseChange.run_id == run.id,
            RequirementCaseChange.dedupe_key == dedupe_key,
        )
    ).first()
    if row is None:
        row = RequirementCaseChange(
            id=uuid.uuid4().hex,
            run_id=run.id,
            project_id=run.project_id,
            requirement_id=run.requirement_id,
            baseline_requirement_id=run.baseline_requirement_id,
            change_id=change_id,
            test_case_id=target_case.id,
            baseline_case_id=baseline_case_id,
            action=action,
            regression_impact=impact,
            regression_reason=reason,
            regression_status="suggested" if baseline_case_id and impact in ("direct", "indirect") else "suggested",
            confidence=0.8 if impact == "direct" else 0.6 if impact == "indirect" else 0.4,
            dedupe_key=dedupe_key,
            review_status=review_status,
            reason=reason,
        )
        session.add(row)
    else:
        row.test_case_id = target_case.id
        row.baseline_case_id = baseline_case_id
        row.action = action
        row.regression_impact = impact
        row.regression_reason = reason
        row.review_status = review_status
        row.reason = reason
    session.flush()
    return row


def _add_case_change(
    session: Session,
    run: RequirementChangeRun,
    *,
    action: str,
    change_id: str | None,
    dedupe_key: str,
    baseline_case_id: str | None,
    test_case_id: str | None,
    impact: str = "none",
    reason: str = "",
    review_status: str = "pending",
) -> RequirementCaseChange:
    row = session.scalars(
        select(RequirementCaseChange).where(
            RequirementCaseChange.run_id == run.id,
            RequirementCaseChange.dedupe_key == dedupe_key,
        )
    ).first()
    if row is None:
        row = RequirementCaseChange(
            id=uuid.uuid4().hex,
            run_id=run.id,
            project_id=run.project_id,
            requirement_id=run.requirement_id,
            baseline_requirement_id=run.baseline_requirement_id,
            change_id=change_id,
            test_case_id=test_case_id,
            baseline_case_id=baseline_case_id,
            action=action,
            regression_impact=impact,
            regression_reason=reason,
            regression_status="suggested",
            confidence=0.4,
            dedupe_key=dedupe_key,
            review_status=review_status,
            reason=reason,
        )
        session.add(row)
    return row


def _save_incremental_generation_snapshot(
    session: Session,
    run: RequirementChangeRun,
    case_rows: list[RequirementCaseChange],
) -> None:
    """把本次二期用例保存成 case_generations 版本，供用例生成页版本下拉回看。"""
    mappings = list(
        session.scalars(
            select(RequirementCase).where(RequirementCase.requirement_id == run.requirement_id)
        )
    )
    point_by_case = {row.test_case_id: row.test_point_id for row in mappings}
    case_ids = {
        row.test_case_id
        for row in case_rows
        if row.action in ("created", "updated", "reused") and row.test_case_id
    }
    if not case_ids:
        return
    cases = list(
        session.scalars(
            select(TestCase).where(TestCase.id.in_(case_ids)).order_by(TestCase.project_seq)
        )
    )
    snapshot = [
        {
            "id": case.id,
            "testPointId": point_by_case.get(case.id),
            "title": case.title,
            "module": case.module,
            "type": case.type,
            "priority": case.priority,
            "source": case.source,
            "qualityScore": case.quality_score,
            "expectedResults": generation_service._as_text_list(
                _json_load(case.expected_results, [])
            ),
        }
        for case in cases
    ]
    source_counts: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for case in cases:
        source_counts[case.source] = source_counts.get(case.source, 0) + 1
        by_type[case.type] = by_type.get(case.type, 0) + 1
    summary = {
        "mode": "incremental",
        "incrementalRunId": run.id,
        "baselineRequirementId": run.baseline_requirement_id,
        "caseChangeSummary": _case_changes_summary(case_rows),
        "llmCaseCount": source_counts.get("ai", 0),
        "ruleCaseCount": source_counts.get("rule", 0),
        "byType": by_type,
        "fallback": False,
        "fallbackReason": "",
    }
    existing = None
    for row in session.scalars(
        select(CaseGeneration)
        .where(CaseGeneration.requirement_id == run.requirement_id)
        .order_by(CaseGeneration.created_at.desc())
        .limit(50)
    ):
        if (_json_load(row.summary, {}) or {}).get("incrementalRunId") == run.id:
            existing = row
            break
    if existing is not None:
        existing.case_count = len(snapshot)
        existing.summary = _dumps(summary)
        existing.snapshot = _dumps(snapshot)
        existing.profile_id = run.profile_id
    else:
        session.add(
            CaseGeneration(
                id=uuid.uuid4().hex,
                project_id=run.project_id,
                requirement_id=run.requirement_id,
                mode="incremental",
                profile_id=run.profile_id,
                case_count=len(snapshot),
                summary=_dumps(summary),
                snapshot=_dumps(snapshot),
            )
        )
    session.flush()


def generate_incremental(
    session: Session,
    run_id: str,
    *,
    mode: str = "auto",
    profile_id: str | None = None,
) -> dict:
    """按已确认/自动的变更清单生成二期用例；不删除任何一期数据。"""
    run = session.get(RequirementChangeRun, run_id)
    if run is None:
        raise BizError(40411, f"增量分析运行不存在: {run_id}", http_status=404)
    if run.status == "generating":
        raise BizError(40065, "该增量任务正在生成中")
    requirement = session.get(Requirement, run.requirement_id)
    baseline = session.get(Requirement, run.baseline_requirement_id)
    if requirement is None or baseline is None:
        raise BizError(40406, "增量运行关联的需求版本不存在", http_status=404)

    run.status = "generating"
    run.mode = mode
    run.profile_id = profile_id
    run.error_message = None
    session.flush()

    try:
        changes = list(
            session.scalars(
                select(RequirementChange).where(RequirementChange.run_id == run.id)
            )
        )
        ctx = _change_context(changes)
        baseline_payload = snapshot_to_dict(session.get(RequirementSnapshot, run.baseline_snapshot_id)) \
            if run.baseline_snapshot_id else _require_baseline_snapshot(session, baseline)
        current_payload = snapshot_to_dict(session.get(RequirementSnapshot, run.current_snapshot_id)) \
            if run.current_snapshot_id else ensure_current_snapshot(session, requirement)
        baseline_eps = {ep.get("id"): ep for ep in baseline_payload.get("endpoints") or []}

        # 一期用例与测试点
        mappings = list(
            session.scalars(
                select(RequirementCase).where(RequirementCase.requirement_id == baseline.id)
            )
        )
        baseline_case_ids = [m.test_case_id for m in mappings]
        baseline_cases = list(
            session.scalars(select(TestCase).where(TestCase.id.in_(baseline_case_ids)))
        ) if baseline_case_ids else []
        baseline_points = {
            row.id: row
            for row in (
                session.scalars(
                    select(TestPoint).where(TestPoint.requirement_id == baseline.id)
                ).all()
            )
        }
        mapping_by_case = {m.test_case_id: m for m in mappings}
        baseline_case_ids_by_endpoint: dict[str, set[str]] = {}

        for case in baseline_cases:
            refs = _json_load(case.coverage_refs, {})
            point = baseline_points.get(mapping_by_case[case.id].test_point_id) \
                if case.id in mapping_by_case and mapping_by_case[case.id].test_point_id else None
            endpoints = _case_endpoints(case, point, baseline_eps)
            for endpoint in endpoints:
                baseline_case_ids_by_endpoint.setdefault(endpoint.get("id"), set()).add(case.id)

        # 1) 删除/不确定接口：不物理删除，登记待处理
        for case in baseline_cases:
            refs = _json_load(case.coverage_refs, {})
            point = baseline_points.get(mapping_by_case[case.id].test_point_id) \
                if case.id in mapping_by_case and mapping_by_case[case.id].test_point_id else None
            endpoints = _case_endpoints(case, point, baseline_eps)
            deleted = next((ctx["deleted"].get(ep.get("id")) for ep in endpoints if ctx["deleted"].get(ep.get("id"))), None)
            uncertain = next((ctx["uncertain"].get(ep.get("id")) for ep in endpoints if ctx["uncertain"].get(ep.get("id"))), None)
            if deleted is not None:
                _add_case_change(
                    session,
                    run,
                    action="pending_delete",
                    change_id=deleted.id,
                    dedupe_key=f"delete:{case.id}",
                    baseline_case_id=case.id,
                    test_case_id=case.id,
                    reason="接口删除待确认；历史用例和执行记录保留",
                    review_status="confirmed" if deleted.review_status == "confirmed" else "pending",
                )
                continue
            if uncertain is not None:
                _add_case_change(
                    session,
                    run,
                    action="missing",
                    change_id=uncertain.id,
                    dedupe_key=f"uncertain:{case.id}",
                    baseline_case_id=case.id,
                    test_case_id=None,
                    reason="接口变更尚未确认，暂不复用该历史用例",
                    review_status="pending",
                )

        # 2) 未删除用例：按影响克隆到二期，不调用模型
        for case in baseline_cases:
            refs = _json_load(case.coverage_refs, {})
            point = baseline_points.get(mapping_by_case[case.id].test_point_id) \
                if case.id in mapping_by_case and mapping_by_case[case.id].test_point_id else None
            endpoints = _case_endpoints(case, point, baseline_eps)
            if any(ctx["deleted"].get(ep.get("id")) for ep in endpoints):
                continue
            if any(ctx["uncertain"].get(ep.get("id")) for ep in endpoints):
                continue
            impact, reason, change_id = _impact_for_case(endpoints, refs, ctx)
            generated = _generated_from_case(case)
            generated.coverage_refs = _remap_coverage_refs(
                generated.coverage_refs, baseline_payload, current_payload
            )
            _save_incremental_case(
                session,
                run,
                generated=generated,
                action="reused",
                change_id=change_id,
                dedupe_key=f"reuse:{case.id}",
                baseline_case_id=case.id,
                impact=impact,
                reason=reason,
                review_status="pending" if not endpoints and not (set(refs.get("ruleIds") or []) & ctx["rule_ids"]) else "auto",
            )

        # 3) 未变化接口缺少历史用例 → 待处理，不静默跳过
        unchanged_changes = [
            change for change in changes
            if change.entity_type == "endpoint"
            and change.change_type == "unchanged"
            and change.review_status != "rejected"
        ]
        for change in unchanged_changes:
            new = _json_load(change.new_data, {}) or {}
            endpoint_id = new.get("id")
            if endpoint_id and baseline_case_ids_by_endpoint.get(endpoint_id):
                continue
            _add_case_change(
                session,
                run,
                action="missing",
                change_id=change.id,
                dedupe_key=f"missing:{endpoint_key(new)}",
                baseline_case_id=None,
                test_case_id=None,
                reason="接口未变化但缺少可关联的历史用例，需补充或人工确认",
                review_status="pending",
            )

        # 4) 新增/修改接口：只对变化测试点生成
        try:
            backend, frontend, machines = generation_service.get_generation_context(
                session, requirement.project_id
            )
        except BizError:
            backend, frontend, machines = BackendResult(), FrontendResult(), []
        points = _build_points_for_changes(
            changes, current_payload, baseline_payload, frontend
        )
        generated_cases = _generate_cases_for_points(
            session,
            requirement,
            points,
            mode=mode,
            profile_id=profile_id,
            backend=backend,
            frontend=frontend,
            machines=machines,
        )
        point_by_id = {point["id"]: point for point in points}
        for generated in generated_cases:
            point = point_by_id.get(generated.test_point_id or "")
            if point is None:
                continue
            change = session.get(RequirementChange, point.get("_change_id")) if point.get("_change_id") else None
            if change is None:
                continue
            old = _json_load(change.old_data, {}) or {}
            baseline_ids = baseline_case_ids_by_endpoint.get(old.get("id") or "", set())
            baseline_case_id = next(iter(sorted(baseline_ids)), None)
            action = "updated" if change.change_type == "modified" and baseline_case_id else "created"
            dedupe = (
                f"update:{baseline_case_id}:{point['_point_sig']}"
                if action == "updated"
                else f"gen:{point.get('_endpoint_key') or change.match_key}:{point['_point_sig']}"
            )
            if point.get("_new_ref") and not generated.coverage_refs.get("endpointIds"):
                if point.get("_module") == "业务规则":
                    generated.coverage_refs["ruleIds"] = [point["_new_ref"]]
                else:
                    generated.coverage_refs["endpointIds"] = [point["_new_ref"]]
            _save_incremental_case(
                session,
                run,
                generated=generated,
                action=action,
                change_id=change.id,
                dedupe_key=dedupe,
                baseline_case_id=baseline_case_id if action == "updated" else None,
                impact="direct",
                reason="接口/规则发生变化，生成增量用例",
            )

        case_rows = list(
            session.scalars(
                select(RequirementCaseChange).where(RequirementCaseChange.run_id == run.id)
            )
        )
        summary = _json_load(run.summary, {})
        summary["caseChanges"] = _case_changes_summary(case_rows)
        _save_incremental_generation_snapshot(session, run, case_rows)
        run.summary = _dumps(summary)
        run.status = "completed"
        run.error_message = None
        session.flush()
        logger.info(
            "增量生成完成 run=%s cases=%s",
            run.id,
            len(case_rows),
        )
        return _run_to_dict(session, run)
    except Exception as exc:  # noqa: BLE001 - 保证失败不破坏基线数据
        logger.exception("增量生成失败 run=%s", run_id)
        session.rollback()
        try:
            failed = session.get(RequirementChangeRun, run_id)
            if failed is not None:
                failed.status = "failed"
                failed.error_message = str(exc)[:4000]
                session.commit()
        except Exception:  # noqa: BLE001
            session.rollback()
        if isinstance(exc, BizError):
            raise
        raise BizError(50050, f"增量生成失败: {exc}", http_status=500) from exc
