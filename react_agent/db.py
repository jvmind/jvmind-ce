"""数据库引擎、Session 工厂、Base

环境变量 DATABASE_URL 指定连接串，默认 SQLite。
连接池参数（仅 PostgreSQL 生效）：DB_POOL_SIZE、DB_MAX_OVERFLOW、
DB_POOL_TIMEOUT、DB_POOL_PRE_PING、DB_POOL_RECYCLE。
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager

try:
    from dotenv import load_dotenv
    from pathlib import Path as _Path
    _env_path = _Path(__file__).resolve().parents[1] / ".env"
    load_dotenv(dotenv_path=str(_env_path), override=False)
except Exception:
    pass

from sqlalchemy import UniqueConstraint, create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker


_logger = logging.getLogger(__name__)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/app.db")
if DATABASE_URL.startswith("sqlite"):
    from pathlib import Path
    _url = DATABASE_URL
    if _url.startswith("sqlite:///"):
        _db_path = _url[len("sqlite:///"):]
        if not _db_path.startswith("/") and "://" not in _db_path:
            Path(_db_path).parent.mkdir(parents=True, exist_ok=True)
_CONNECT_ARGS = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "20"))
_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "10"))
_POOL_TIMEOUT = int(os.getenv("DB_POOL_TIMEOUT", "30"))
_POOL_PRE_PING = os.getenv("DB_POOL_PRE_PING", "1").lower() in ("1", "true", "yes")
_POOL_RECYCLE = int(os.getenv("DB_POOL_RECYCLE", "3600"))

engine = create_engine(DATABASE_URL, connect_args=_CONNECT_ARGS,
                       pool_size=_POOL_SIZE, max_overflow=_MAX_OVERFLOW,
                       pool_timeout=_POOL_TIMEOUT,
                       pool_pre_ping=_POOL_PRE_PING, pool_recycle=_POOL_RECYCLE,
                       pool_reset_on_return="rollback")
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


@event.listens_for(engine, "checkin")
def _ensure_clean_on_checkin(dbapi_connection, connection_record):
    try:
        if not dbapi_connection.closed:
            dbapi_connection.rollback()
    except Exception:
        connection_record.invalidate()


class Base(DeclarativeBase):
    pass


def init_db():
    """建表 + 自动迁移缺失列。"""
    Base.metadata.create_all(bind=engine)
    _auto_migrate()
    _seed_default_settings()
    _ensure_default_user()


def get_db():
    """FastAPI 依赖：每请求获取数据库会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        try:
            db.rollback()
        except Exception:
            _logger.warning("DB rollback failed during get_db cleanup")
        db.close()


@contextmanager
def session_scope():
    """非路由辅助函数用的会话上下文管理器。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        try:
            db.rollback()
        except Exception:
            _logger.warning("DB rollback failed during session_scope cleanup")
        db.close()


_DEFAULT_USER_ID = "user_local"
_DEFAULT_USER_USERNAME = "local"
_DEFAULT_USER_EMAIL = "local@jvmind.local"


def _ensure_default_user():
    """确保单用户（默认 admin）存在。无认证：所有请求都按此用户身份运行。"""
    from .models import UserModel
    from .user_manager_db import _hash_password
    db = SessionLocal()
    try:
        existing = db.query(UserModel).filter(UserModel.id == _DEFAULT_USER_ID).first()
        if existing:
            return
        user = UserModel(
            id=_DEFAULT_USER_ID,
            username=_DEFAULT_USER_USERNAME,
            email=_DEFAULT_USER_EMAIL,
            password_hash=_hash_password(""),
            is_admin=1,
            created_at=_now_str(),
        )
        db.add(user)
        db.commit()
    finally:
        try:
            db.rollback()
        except Exception:
            pass
        db.close()


def _seed_default_settings():
    from .models import SystemSettingModel
    defaults = {"max_input_length": "100000"}
    db = SessionLocal()
    try:
        for k, v in defaults.items():
            existing = db.query(SystemSettingModel).filter(SystemSettingModel.key == k).first()
            if not existing:
                db.add(SystemSettingModel(key=k, value=v, updated_at=_now_str()))
        db.commit()
    finally:
        try:
            db.rollback()
        except Exception:
            pass
        db.close()


def _now_str():
    from .timeutil import now_str
    return now_str()


def _auto_migrate():
    """检测并添加模型中定义了但数据库表缺失的列、唯一约束、普通索引。

    支持 SQLite 与 PostgreSQL（仅追加：加列、加唯一索引、加普通索引）。
    """
    try:
        inspector = inspect(engine)
        existing_tables = set(inspector.get_table_names())
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            existing_cols = {c["name"] for c in inspector.get_columns(table.name)}
            for col in table.columns:
                if col.name not in existing_cols:
                    col_type = _generic_col_type(col)
                    with engine.connect() as conn:
                        try:
                            conn.execute(text(
                                f"ALTER TABLE {table.name} ADD COLUMN {col.name} {col_type}"
                            ))
                            conn.commit()
                            print(f"[auto_migrate] added column {table.name}.{col.name} ({col_type})")
                        except Exception:
                            conn.rollback()
                            raise
        existing_index_names = _existing_index_names()
        for table in Base.metadata.sorted_tables:
            for constraint in table.constraints:
                if isinstance(constraint, UniqueConstraint) and constraint.name:
                    cols = ", ".join(table.c[c.name].name for c in constraint.columns)
                    sql = f'CREATE UNIQUE INDEX IF NOT EXISTS "{constraint.name}" ON {table.name} ({cols})'
                    if constraint.name not in existing_index_names:
                        with engine.connect() as conn:
                            try:
                                conn.execute(text(sql))
                                conn.commit()
                                print(f"[auto_migrate] added unique index {constraint.name} on {table.name}({cols})")
                                existing_index_names.add(constraint.name)
                            except Exception:
                                conn.rollback()
                                raise
        for table in Base.metadata.sorted_tables:
            for idx in table.indexes:
                if not idx.name:
                    continue
                if idx.name in existing_index_names:
                    continue
                cols = ", ".join(f'"{c.name}"' for c in idx.columns)
                sql = f'CREATE INDEX IF NOT EXISTS "{idx.name}" ON {table.name} ({cols})'
                with engine.connect() as conn:
                    try:
                        conn.execute(text(sql))
                        conn.commit()
                        print(f"[auto_migrate] added index {idx.name} on {table.name}({cols})")
                        existing_index_names.add(idx.name)
                    except Exception:
                        conn.rollback()
                        raise
    except Exception as e:
        print(f"[auto_migrate] warning: {e}")


def _existing_index_names():
    names = set()
    try:
        with engine.connect() as conn:
            if DATABASE_URL.startswith("sqlite"):
                rows = conn.execute(text(
                    "SELECT name FROM sqlite_master WHERE type='index' AND name IS NOT NULL"
                ))
            else:
                rows = conn.execute(text("SELECT indexname FROM pg_indexes"))
            for r in rows:
                names.add(r[0])
            conn.rollback()
    except Exception as e:
        print(f"[auto_migrate] could not list existing indexes: {e}")
    return names


def _generic_col_type(col):
    import sqlalchemy.types as t
    typ = col.type
    if isinstance(typ, t.Integer):
        return "INTEGER"
    if isinstance(typ, t.Text):
        return "TEXT"
    if isinstance(typ, t.Float):
        return "REAL" if DATABASE_URL.startswith("sqlite") else "DOUBLE PRECISION"
    if isinstance(typ, t.Boolean):
        return "INTEGER" if DATABASE_URL.startswith("sqlite") else "BOOLEAN"
    return "TEXT"


_POOL_WARN_THRESHOLD = float(os.getenv("POOL_WARN_THRESHOLD", "0.8"))


def get_pool_stats() -> dict:
    pool = getattr(engine, "pool", None)
    if pool is None:
        return {
            "engine": "sqlite (no pool)",
            "pool_type": "NullPool",
            "pool_size": 0,
            "checked_in": 0,
            "checked_out": 0,
            "overflow": 0,
            "total_connections": 0,
            "usage_ratio": 0,
            "healthy": True,
        }
    try:
        size = getattr(pool, "size", lambda: 0)()
        checkedin = getattr(pool, "checkedin", lambda: 0)()
        checkedout = getattr(pool, "checkedout", lambda: 0)()
        overflow = getattr(pool, "overflow", lambda: 0)()
    except Exception:
        size = checkedin = checkedout = overflow = 0

    total = checkedin + checkedout
    usage = checkedout / total if total > 0 else 0
    pool_type = type(pool).__name__

    return {
        "engine": "postgresql" if not DATABASE_URL.startswith("sqlite") else "sqlite",
        "pool_type": pool_type,
        "pool_size": size,
        "checked_in": checkedin,
        "checked_out": checkedout,
        "overflow": max(0, overflow),
        "total_connections": total,
        "usage_ratio": round(usage, 4),
        "healthy": usage < _POOL_WARN_THRESHOLD if total > 0 else True,
    }


def check_pool_health() -> dict:
    stats = get_pool_stats()
    if not stats.get("healthy", True) and stats.get("total_connections", 0) > 0:
        _logger.warning(
            "[pool_health] Connection pool near exhaustion: "
            "checked_out=%d, total=%d, usage=%.1f%%",
            stats["checked_out"], stats["total_connections"],
            stats["usage_ratio"] * 100,
        )
    return stats