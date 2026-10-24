"""修复历史 LLM 用例把 expectedResults 字符串逐字符拆开的问题。

只处理“长度 >= 4 且全部为单字符”的明显脏数据：
- 实时用例：优先用关联测试点的 expected 补成完整预期句；
- 生成快照 / 用例版本 / 执行快照：优先同步实时用例的修复结果，否则合并为一条。

脚本可重复执行；修复完成后再次运行不会产生变更。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.database import SessionLocal
from app.entity.models import (
    CaseGeneration,
    ExecutionRecord,
    RequirementCase,
    TestCase,
    TestCaseVersion,
    TestPoint,
)


def is_char_split(value) -> bool:
    return (
        isinstance(value, list)
        and len(value) >= 4
        and all(isinstance(item, str) and len(item.strip()) == 1 for item in value)
    )


def load_json(raw, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def main() -> None:
    repaired: dict[str, list[str]] = {}
    changed_cases = 0
    changed_snapshots = 0

    with SessionLocal() as session:
        mappings = session.execute(
            select(RequirementCase.test_case_id, RequirementCase.test_point_id)
        ).all()
        point_by_case = {row[0]: row[1] for row in mappings}
        points = {
            row.id: row.expected or ""
            for row in session.scalars(select(TestPoint)).all()
        }

        for tc in session.scalars(select(TestCase)).all():
            expected = load_json(tc.expected_results, [])
            if not is_char_split(expected):
                continue
            point_expected = (points.get(point_by_case.get(tc.id)) or "").strip()
            repaired_text = (
                f"{tc.title}（预期：{point_expected}）" if point_expected else tc.title
            )
            repaired[tc.id] = [repaired_text]
            tc.expected_results = json.dumps(repaired[tc.id], ensure_ascii=False)
            changed_cases += 1

        for row in session.scalars(select(TestCaseVersion)).all():
            snapshot = load_json(row.snapshot, {})
            if not isinstance(snapshot, dict):
                continue
            expected = snapshot.get("expectedResults")
            if not is_char_split(expected):
                continue
            snapshot["expectedResults"] = repaired.get(
                snapshot.get("id"), ["".join(str(x) for x in expected)]
            )
            row.snapshot = json.dumps(snapshot, ensure_ascii=False)
            changed_snapshots += 1

        for row in session.scalars(select(CaseGeneration)).all():
            snapshot = load_json(row.snapshot, [])
            if not isinstance(snapshot, list):
                continue
            changed = False
            for item in snapshot:
                if not isinstance(item, dict):
                    continue
                expected = item.get("expectedResults")
                if not is_char_split(expected):
                    continue
                item["expectedResults"] = repaired.get(
                    item.get("id"), ["".join(str(x) for x in expected)]
                )
                changed = True
            if changed:
                row.snapshot = json.dumps(snapshot, ensure_ascii=False)
                changed_snapshots += 1

        for row in session.scalars(select(ExecutionRecord)).all():
            snapshot = load_json(row.test_case_snapshot, {})
            if not isinstance(snapshot, dict):
                continue
            expected = snapshot.get("expectedResults")
            if not is_char_split(expected):
                continue
            snapshot["expectedResults"] = repaired.get(
                snapshot.get("id"), ["".join(str(x) for x in expected)]
            )
            row.test_case_snapshot = json.dumps(snapshot, ensure_ascii=False)
            changed_snapshots += 1

        session.commit()

    print(f"repaired live cases: {changed_cases}")
    print(f"repaired snapshots: {changed_snapshots}")
    for cid, expected in repaired.items():
        print(f"  {cid}: {expected[0]}")


if __name__ == "__main__":
    main()
