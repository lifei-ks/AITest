"""端到端自检：靶站 → 分析 → 生成 → 执行 → 断言（独立临时库，不污染开发库）。

用法：.venv/Scripts/python.exe scripts/e2e.py
验收：生成 >=12 条，执行通过率 >=90%，截图 >= 用例数×步骤数，每条 1 个 WebM
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_TMP = Path(tempfile.mkdtemp(prefix="aicase-e2e-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP / 'e2e.db').as_posix()}"
os.environ["OUTPUT_DIR"] = str(_TMP / "outputs")

from sqlalchemy import select  # noqa: E402

from app.database import Base, SessionLocal, get_engine  # noqa: E402
from app.entity.models import CodeAnalysis, ExecutionRecord, ExecutionStep, Project, TestCase  # noqa: E402
from app.service import analysis_service, coverage_service, execution_service, generation_service  # noqa: E402

SAMPLE = str(Path(__file__).resolve().parent / "sample_src")
TARGET = "http://127.0.0.1:8200"


def main() -> None:
    Base.metadata.create_all(get_engine())
    demo = Path(__file__).resolve().parent / "demo_site"
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "serve:app", "--port", "8200"],
        cwd=str(demo), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(2)
    try:
        with SessionLocal() as s:
            p = Project(name="e2e", source_type="local_path", source_path=SAMPLE, target_url=TARGET)
            s.add(p)
            s.commit()
            pid = p.id
            a = CodeAnalysis(project_id=pid, status="running")
            s.add(a)
            s.commit()
            aid = a.id

        analysis_service.run_analysis(pid, aid)
        gen = generation_service.generate(pid, "rule")
        print(f"[生成] 保留 {gen['kept']} 条，分布 {gen['byType']}")

        with SessionLocal() as s:
            cases = list(s.scalars(select(TestCase).where(TestCase.project_id == pid)))

        results = []
        for tc in cases:
            r = execution_service.execute_single(pid, tc.id, TARGET, "programmatic")
            results.append(r)
            print(f"  [{r['status']:8}] {tc.type:9} {tc.title[:36]}")
            if r["status"] not in ("passed", "skipped") and r.get("llmReview"):
                review = r["llmReview"]
                print(
                    f"    AI={review.get('verdict')} 原因={review.get('reason')} "
                    f"问题={[(x.get('severity'), x.get('problem')) for x in review.get('issues', [])]}"
                )

        passed = sum(1 for r in results if r["status"] == "passed")
        rate = passed / len(results) if results else 0
        with SessionLocal() as s:
            step_count = len(list(s.scalars(select(ExecutionStep))))
            shot_count = sum(1 for st in s.scalars(select(ExecutionStep)) if st.screenshot_after)
            video_count = len(list(s.scalars(select(ExecutionRecord).where(ExecutionRecord.video_path.isnot(None)))))
            matrix = coverage_service.coverage_matrix(s, pid)

        print(f"\n通过率: {passed}/{len(results)} = {rate:.0%}")
        print(f"步骤数: {step_count}  截图数: {shot_count}  视频数: {video_count}")
        print(
            "覆盖矩阵: "
            + " | ".join(
                f"{name} 规划 {block['plannedCovered']}/{block['total']} "
                f"执行 {block['executedCovered']}/{block['total']}"
                for name, block in (
                    ("接口", matrix["apiEndpoint"]),
                    ("状态转换", matrix["stateTransition"]),
                    ("业务规则", matrix["businessRule"]),
                )
            )
        )

        coverage_blocks = (
            matrix["apiEndpoint"],
            matrix["stateTransition"],
            matrix["businessRule"],
        )
        coverage_ok = all(
            block["total"] > 0
            and block["plannedCovered"] > 0
            and block["executedCovered"] > 0
            and 0 <= block["executedRate"] <= block["plannedRate"] <= 1
            for block in coverage_blocks
        )
        ok = (
            gen["kept"] >= 12
            and rate >= 0.9
            and shot_count >= step_count
            and video_count >= len(results)
            and coverage_ok
        )
        print("\nRESULT:", "PASS" if ok else "FAIL")
    finally:
        proc.terminate()


if __name__ == "__main__":
    main()
