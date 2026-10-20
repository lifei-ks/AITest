"""V4 生成服务测试：TC id 分配 + 端到端生成落库。"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from app.database import Base, SessionLocal, get_engine
from app.entity.models import CodeAnalysis, Project
from app.entity.models import TestCase as TestCaseModel
from app.service import analysis_service, generation_service

ORDER_SYSTEM = Path(__file__).resolve().parent.parent / "scripts" / "order-system"


@pytest.fixture(scope="module", autouse=True)
def _create_tables():
    Base.metadata.create_all(get_engine())
    yield


def test_next_tc_id_increments_no_conflict():
    """连续分配 15 个 id（超过 9，覆盖字符串字典序问题），必须唯一且递增。"""
    with SessionLocal() as s:
        ids = []
        for i in range(15):
            tid = generation_service._next_tc_id(s)
            ids.append(tid)
            s.add(TestCaseModel(id=tid, project_seq=i + 1, project_id="x", title=f"t{i}"))
        s.commit()
        assert len(set(ids)) == 15
        assert ids[0] == "TC-000001"
        assert ids[-1] == "TC-000015"


def test_generate_end_to_end():
    """建项目 → 分析 → 生成 → 落库，且可重复生成不冲突。"""
    with SessionLocal() as s:
        p = Project(name="生成端到端", source_type="local_path", source_path=str(ORDER_SYSTEM))
        s.add(p)
        s.commit()
        pid = p.id
        a = CodeAnalysis(project_id=pid, status="running")
        s.add(a)
        s.commit()
        aid = a.id

    analysis_service.run_analysis(pid, aid)

    # 生成两次，验证 TC id 不冲突，且重新生成会清空旧的生成用例
    r1 = generation_service.generate(pid, "rule")
    r2 = generation_service.generate(pid, "rule")
    assert r1["kept"] >= 12
    assert {"positive", "negative", "boundary", "state"} <= set(r1["byType"])

    with SessionLocal() as s:
        rows = list(s.scalars(select(TestCaseModel).where(TestCaseModel.project_id == pid)))
        ids = [r.id for r in rows]
        assert len(ids) == len(set(ids)), "TC id 不得重复"
        assert len(rows) == r2["kept"]  # 重新生成清空旧 rule/ai 用例，只保留最新一批
