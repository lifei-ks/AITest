"""V7 脑图 + 覆盖矩阵测试。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.database import Base, SessionLocal, get_engine
from app.entity.models import CodeAnalysis, ExecutionRecord, Project, TestCase
from app.service import analysis_service, coverage_service, generation_service, mindmap_service

ORDER_SYSTEM = Path(__file__).resolve().parent.parent / "scripts" / "order-system"


@pytest.fixture(scope="module", autouse=True)
def _setup():
    Base.metadata.create_all(get_engine())
    with SessionLocal() as s:
        p = Project(name="脑图测试", source_type="local_path", source_path=str(ORDER_SYSTEM))
        s.add(p)
        s.commit()
        pid = p.id
        a = CodeAnalysis(project_id=pid, status="running")
        s.add(a)
        s.commit()
        aid = a.id
    analysis_service.run_analysis(pid, aid)
    generation_service.generate(pid, "rule")
    yield


def _pid():
    with SessionLocal() as s:
        return s.scalars(select(Project).where(Project.name == "脑图测试")).first().id


def test_mindmap_tree_structure():
    pid = _pid()
    data = mindmap_service.build_tree(SessionLocal(), pid)
    tree = data["tree"]
    assert tree["id"] == "root"
    assert data["statistics"]["cases"] >= 12
    # 四层：root -> module -> type -> case -> (pre/steps/exp)
    module = tree["children"][0]
    assert module["id"].startswith("module-")
    type_node = module["children"][0]
    assert type_node["id"].startswith("type-")
    case = type_node["children"][0]
    assert case["id"].startswith("case-")
    assert any(c["id"].startswith(("pre-", "steps-", "exp-")) for c in case["children"])


def test_mindmap_exports():
    pid = _pid()
    data = mindmap_service.build_tree(SessionLocal(), pid)
    md = mindmap_service.export_markdown(data)
    assert md.startswith("- ")
    xmind = mindmap_service.export_xmind(data)
    assert xmind.startswith(b"PK")  # zip 魔数


def test_coverage_executed_filters_passed():
    """执行覆盖必须只算 passed 的执行记录。"""
    pid = _pid()
    with SessionLocal() as s:
        cases = list(s.scalars(select(TestCase).where(TestCase.project_id == pid)))
        # 找一条有 transitionIds 的 state 用例
        state_case = next(c for c in cases if c.type == "state" and json.loads(c.coverage_refs or "{}").get("transitionIds"))
        tid = json.loads(state_case.coverage_refs)["transitionIds"][0]

        # 造一条 failed 执行记录（引用该 transition）
        s.add(ExecutionRecord(
            id="EX-fail0001", project_id=pid, test_case_id=state_case.id,
            test_case_title=state_case.title, test_case_snapshot="{}", status="failed",
        ))
        s.commit()

    m = coverage_service.coverage_matrix(SessionLocal(), pid)
    # failed 执行不该算执行覆盖
    executed_tids = set()
    for sm in m["stateMachines"]:
        for t in sm["transitions"]:
            if t.get("executed"):
                executed_tids.add(t.get("id"))
    assert tid not in executed_tids  # 只有 failed 执行，不算覆盖

    # 再加 passed 执行，该 transition 应变为 executed
    with SessionLocal() as s:
        s.add(ExecutionRecord(
            id="EX-pass0001", project_id=pid, test_case_id=state_case.id,
            test_case_title=state_case.title, test_case_snapshot="{}", status="passed",
        ))
        s.commit()
    m2 = coverage_service.coverage_matrix(SessionLocal(), pid)
    executed2 = {t.get("id") for sm in m2["stateMachines"] for t in sm["transitions"] if t.get("executed")}
    assert tid in executed2
