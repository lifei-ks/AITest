"""V4 端到端验证：项目 → 分析 → 生成 → 落库（真实 HTTP + MySQL）。

用法：.venv/Scripts/python.exe scripts/v4_e2e.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.entity.models import TestCase  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

BASE = "http://127.0.0.1:8100"
ORDER_SYSTEM = "D:/python_code/AITest/scripts/order-system"


def main() -> None:
    # 1. 建项目（复用同名）
    projs = httpx.get(f"{BASE}/api/projects", params={"keyword": "order-system"}).json()["data"]
    proj = next((p for p in projs if p["name"] == "order-system"), None)
    if not proj:
        r = httpx.post(
            f"{BASE}/api/projects",
            json={"name": "order-system", "sourceType": "local_path", "sourcePath": ORDER_SYSTEM},
        )
        r.raise_for_status()
        proj = r.json()["data"]
    pid = proj["id"]
    print(f"[1] 项目: {proj['name']} ({pid[:8]})")

    # 2. 分析（异步，轮询到结束）
    httpx.post(f"{BASE}/api/projects/{pid}/analyze")
    status = None
    for _ in range(30):
        time.sleep(0.5)
        a = httpx.get(f"{BASE}/api/projects/{pid}/analysis").json()["data"]
        status = a["status"]
        if status in ("success", "failed"):
            break
    print(f"[2] 分析: {status}")

    # 3. 生成（规则模式，同步）
    g = httpx.post(f"{BASE}/api/projects/{pid}/generate", json={"mode": "rule"}).json()["data"]
    print(f"[3] 生成统计: 生成{g['generated']} 保留{g['kept']} 丢弃{g['dropped']}")
    print(f"    四类分布: {g['byType']}")

    # 4. 落库校验
    with SessionLocal() as s:
        total = s.scalar(select(func.count()).select_from(TestCase).where(TestCase.project_id == pid))
        rows = list(s.scalars(select(TestCase).where(TestCase.project_id == pid)))
        types = {}
        for r in rows:
            types[r.type] = types.get(r.type, 0) + 1
        print(f"[4] 落库: test_cases={total} 类型分布={types}")
        sample = rows[0] if rows else None
        if sample:
            print(f"    示例: {sample.id} {sample.title} (质量分{sample.quality_score})")

    ok = total >= 12 and {"positive", "negative", "boundary", "state"} <= set(types)
    print("\nRESULT:", "PASS" if ok else "FAIL")


if __name__ == "__main__":
    main()
