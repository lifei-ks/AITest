"""数据库基础设施：engine / SessionLocal / Base / session_scope。

- SQLAlchemy 2.0 DeclarativeBase 风格（V2 起所有模型继承 Base，用 Mapped/mapped_column）
- SQLite 时监听 connect 事件开启 WAL 与 foreign_keys
- session_scope()：事务型会话上下文，正常退出 commit，异常 rollback
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


def build_engine(database_url: str) -> Engine:
    """按 URL 创建 engine；SQLite 附加 PRAGMA。独立成函数便于测试创建临时库 engine。

    pool_pre_ping / pool_recycle：远程 MySQL 连接空闲超时后，池中旧连接会在下一次请求
    才报错（表现为偶发 500）。提前探活与定期回收可避免。
    """
    engine = create_engine(
        database_url,
        echo=False,
        future=True,
        pool_pre_ping=True,
        pool_recycle=1800,
    )

    if database_url.startswith("sqlite"):
        # SQLite 文件库确保父目录存在（URL 形如 sqlite:///D:/x/data/a.db）
        url_path = database_url.split("///", 1)[1]
        if url_path and url_path != ":memory:":
            Path(url_path).parent.mkdir(parents=True, exist_ok=True)

        @event.listens_for(engine, "connect")
        def _sqlite_pragma(dbapi_conn, _record):  # pragma: no cover - 驱动回调
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def create_session_factory(bind_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(
        bind=bind_engine,
        autoflush=False,
        expire_on_commit=False,
        class_=Session,
    )


_engine = build_engine(get_settings().database_url)
SessionLocal = create_session_factory(_engine)


def get_engine() -> Engine:
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    """事务边界：with 块内正常返回即 commit，抛异常即 rollback。"""
    session: Session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
