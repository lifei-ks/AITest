"""代码分析编排：scan → 解析 → 状态机 → 落库。纯规则，零 LLM。"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.state_machine_agent import extract_state_machines
from app.analyzer.scanner import list_by_ext, scan
from app.analyzer.spring import analyze_backend
from app.analyzer.vue import analyze_frontend
from app.database import SessionLocal
from app.entity.models import CodeAnalysis, Project, StateMachine
from app.analyzer.models import to_json_dict

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def trigger_analysis(pid: str) -> str:
    """创建 running 记录，后台线程跑分析，立即返回 analysisId。"""
    with SessionLocal() as session:
        project = session.get(Project, pid)
        if project is None:
            from app.common.errors import BizError

            raise BizError(40401, f"项目不存在: {pid}", http_status=404)
        if project.source_type != "local_path" or not project.source_path:
            from app.common.errors import BizError

            raise BizError(40006, "仅支持本地路径项目进行分析（请先填写 sourcePath）")

        analysis = CodeAnalysis(project_id=pid, status="running", created_at=_now())
        session.add(analysis)
        session.commit()
        analysis_id = analysis.id

    t = threading.Thread(target=run_analysis, args=(pid, analysis_id), daemon=True)
    t.start()
    return analysis_id


def run_analysis(pid: str, analysis_id: str) -> None:
    """后台执行静态分析并写回结果。"""
    session = SessionLocal()
    try:
        project = session.get(Project, pid)
        analysis = session.get(CodeAnalysis, analysis_id)
        if project is None or analysis is None:
            return

        # 静态扫描可能耗时较长：先结束只读事务，避免远程 MySQL 关闭空闲连接
        session.commit()

        warnings: list[str] = []
        backend = None
        frontend = None

        root = project.source_path
        scan_result = scan(root)
        if not scan_result.files:
            analysis.status = "failed"
            analysis.error_message = f"源码目录不存在或为空: {root}"
            session.commit()
            return

        if scan_result.backend == "java-spring":
            java = list_by_ext(scan_result, {".java"})
            backend = analyze_backend(java, root)
        else:
            warnings.append("未识别到 Spring Boot 后端（缺 pom.xml / build.gradle）")

        if scan_result.frontend == "vue":
            vue_js = list_by_ext(scan_result, {".vue", ".js"})
            frontend = analyze_frontend(vue_js, root)
        else:
            warnings.append("未识别到 Vue 前端（缺 package.json 或 .vue）")

        # 状态机（依赖后端解析结果）
        machines = []
        if backend is not None:
            java = list_by_ext(scan_result, {".java"})
            machines = extract_state_machines(backend, java, root)

        # 落库
        analysis.backend_result = json.dumps(to_json_dict(backend), ensure_ascii=False) if backend else None
        analysis.frontend_result = json.dumps(to_json_dict(frontend), ensure_ascii=False) if frontend else None
        analysis.warnings = json.dumps(warnings, ensure_ascii=False)
        analysis.status = "success"
        analysis.error_message = None

        # 清旧状态机，写新
        session.query(StateMachine).filter(StateMachine.project_id == pid).delete()
        for sm in machines:
            session.add(
                StateMachine(
                    project_id=pid,
                    name=sm.name,
                    states=json.dumps(sm.states, ensure_ascii=False),
                    transitions=json.dumps([t.to_dict() for t in sm.transitions], ensure_ascii=False),
                    confidence=sm.confidence,
                )
            )

        # 回填 tech_stack
        project.tech_stack = json.dumps(scan_result.tech_stack, ensure_ascii=False)
        session.commit()
        logger.info("分析完成 project=%s endpoints=%s machines=%s", pid, len(backend.endpoints) if backend else 0, len(machines))
    except Exception as exc:  # noqa: BLE001 - 后台任务需兜底记录
        logger.exception("分析失败 project=%s", pid)
        try:
            analysis = session.get(CodeAnalysis, analysis_id)
            if analysis:
                analysis.status = "failed"
                analysis.error_message = str(exc)
                session.commit()
        finally:
            session.rollback()
    finally:
        session.close()


def get_latest_analysis(session: Session, pid: str) -> CodeAnalysis | None:
    return session.scalars(
        select(CodeAnalysis)
        .where(CodeAnalysis.project_id == pid)
        .order_by(CodeAnalysis.created_at.desc())
        .limit(1)
    ).first()


def get_analysis_result(session: Session, pid: str) -> dict:
    a = get_latest_analysis(session, pid)
    if a is None:
        return {"status": None, "backendResult": None, "frontendResult": None, "stateMachines": [], "warnings": []}
    machines = list(session.scalars(select(StateMachine).where(StateMachine.project_id == pid)))
    return {
        "status": a.status,
        "backendResult": json.loads(a.backend_result) if a.backend_result else None,
        "frontendResult": json.loads(a.frontend_result) if a.frontend_result else None,
        "stateMachines": [
            {
                "id": m.id,
                "name": m.name,
                "states": json.loads(m.states) if m.states else [],
                "transitions": json.loads(m.transitions) if m.transitions else [],
                "confidence": m.confidence,
            }
            for m in machines
        ],
        "warnings": json.loads(a.warnings) if a.warnings else [],
        "errorMessage": a.error_message,
    }


def list_state_machines(session: Session, pid: str) -> list[dict]:
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
