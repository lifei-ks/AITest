"""从 ORM 元数据生成 MySQL DDL（migrations/mysql/001_init.sql）。

用法：.venv/Scripts/python.exe scripts/gen_mysql_ddl.py
说明：
- 用 SQLAlchemy 内置 mysql 方言编译 CREATE TABLE，不连接数据库
- 每张表追加 ENGINE=InnoDB DEFAULT CHARSET=utf8mb4（TASK_BOOK M5 要求）
- 本项目用手写关联 id（不用 ForeignKey），级联删除在 service 层实现，故 DDL 无外键
- id/created_at 等默认值由 Python 端 default 生成，DDL 不写 DEFAULT（用 ORM 插入时仍生效）
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateIndex, CreateTable

from app.database import Base

import app.entity.models  # noqa: F401  确保所有模型注册到 Base.metadata

OUT = Path(__file__).resolve().parent.parent / "migrations" / "mysql" / "001_init.sql"


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    parts = [
        "-- AICase Core MySQL 初始化 DDL（由 scripts/gen_mysql_ddl.py 生成）",
        "-- 建库后执行：mysql -u root -p <库名> < migrations/mysql/001_init.sql",
        f"-- 共 {len(Base.metadata.tables)} 张表",
        "",
        "SET NAMES utf8mb4;",
        "",
    ]
    for table in Base.metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=mysql.dialect())).rstrip()
        ddl += " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;"
        parts.append(ddl)
        # index=True 与复合索引需要单独生成 CREATE INDEX（CreateTable 不渲染索引）
        for index in sorted(table.indexes, key=lambda i: i.name):
            parts.append(str(CreateIndex(index).compile(dialect=mysql.dialect())) + ";")
        parts.append("")
    OUT.write_text("\n".join(parts), encoding="utf-8")
    print(f"已生成 {OUT}（{len(Base.metadata.tables)} 张表）")


if __name__ == "__main__":
    main()
