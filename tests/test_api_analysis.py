"""V3 分析服务端到端测试：样本源码 → 分析 → 落库 → tech_stack 回填。"""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select

from app.database import SessionLocal
from app.entity.models import CodeAnalysis, Project, StateMachine
from app.service import analysis_service

SAMPLE = Path(__file__).resolve().parent.parent / "scripts" / "sample_src"


def test_analysis_end_to_end():
    with SessionLocal() as s:
        p = Project(name="分析端到端", source_type="local_path", source_path=str(SAMPLE))
        s.add(p)
        s.commit()
        pid = p.id
        a = CodeAnalysis(project_id=pid, status="running")
        s.add(a)
        s.commit()
        aid = a.id

    analysis_service.run_analysis(pid, aid)

    with SessionLocal() as s:
        a = s.get(CodeAnalysis, aid)
        assert a.status == "success"
        backend = json.loads(a.backend_result)
        assert len(backend["endpoints"]) >= 5
        assert backend["language"] == "java-spring"

        frontend = json.loads(a.frontend_result)
        assert frontend["language"] == "vue"

        machines = list(s.scalars(select(StateMachine).where(StateMachine.project_id == pid)))
        assert len(machines) >= 1
        transitions = json.loads(machines[0].transitions)
        assert len(transitions) >= 4
        assert all(t["verified"] for t in transitions)

        p = s.get(Project, pid)
        tech = json.loads(p.tech_stack)
        assert tech["backend"] == "java-spring"
        assert tech["frontend"] == "vue"


def test_analysis_nonexistent_source_path():
    with SessionLocal() as s:
        p = Project(name="空路径", source_type="local_path", source_path="D:/no/such/dir_xyz")
        s.add(p)
        s.commit()
        pid = p.id
        a = CodeAnalysis(project_id=pid, status="running")
        s.add(a)
        s.commit()
        aid = a.id

    analysis_service.run_analysis(pid, aid)

    with SessionLocal() as s:
        a = s.get(CodeAnalysis, aid)
        assert a.status == "failed"
        assert "不存在" in a.error_message or "为空" in a.error_message
