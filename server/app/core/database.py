"""数据库引擎、会话与声明式基类。

双数据库布局（2026-10-14 合并后）：
- **PostgreSQL**（知识库内容库）：文档 / 切片 / 向量 / RBAC / 审计，SQLAlchemy ORM 管理
- **MySQL**（dlj_data 等）：统一用户表 t_employee 只读认证 + agent 侧数据，
  这里仅提供引擎与会话工厂，ORM 模型仍以 PG 为主

采用同步 SQLAlchemy 2.0：
- 与 Celery 任务天然一致，避免异步 session 在任务队列中的传播难题
- FastAPI 会自动把同步 def 路由丢进线程池，不会阻塞事件循环
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings

engine = create_engine(
    settings.database_url,
    echo=settings.db_echo,
    pool_pre_ping=True,      # 断线自动重连
    pool_size=10,
    max_overflow=20,
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：请求级数据库会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════
#  MySQL（统一用户表 t_employee 等）
# ══════════════════════════════════════════════════════════════

def _mysql_url(db_name: str) -> str:
    # ⚠️ 用户名/密码必须 URL 编码：密码含 @ : / 等字符时
    #    （如 8FcXqrhYM@）会破坏 URL 结构，报
    #    "Can't connect to MySQL server on '@47.121.183.81'" 这类莫名错误。
    from urllib.parse import quote_plus

    user = quote_plus(settings.mysql_user)
    pwd = quote_plus(settings.mysql_password)
    return (
        f"mysql+pymysql://{user}:{pwd}"
        f"@{settings.mysql_host}:{settings.mysql_port}/{db_name}"
        f"?charset={settings.mysql_charset}"
    )


# 员工认证只读库（dlj_data）。连接池小一点：只用于登录与同步。
mysql_business_engine = create_engine(
    _mysql_url(settings.mysql_db_business),
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    future=True,
)

MySQLBusinessSession = sessionmaker(
    bind=mysql_business_engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


def get_mysql_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：MySQL 会话（员工认证 / 同步用）。"""
    db = MySQLBusinessSession()
    try:
        yield db
    finally:
        db.close()


def check_mysql_connection() -> tuple[bool, str]:
    """健康检查用：探测 MySQL 连通性与 t_employee 可读性。"""
    try:
        with mysql_business_engine.connect() as conn:
            cnt = conn.execute(
                text("SELECT COUNT(*) FROM t_employee WHERE deleted_flag = 0")
            ).scalar_one()
            return True, f"MySQL {settings.mysql_host}:{settings.mysql_port} | t_employee 有效员工 {cnt} 人"
    except Exception as exc:  # noqa: BLE001 - 健康检查需要吞掉所有异常
        return False, str(exc)


def check_connection() -> tuple[bool, str]:
    """健康检查用：探测数据库连通性与扩展可用性。"""
    try:
        with engine.connect() as conn:
            version = conn.execute(text("SELECT version()")).scalar_one()
            has_vector = conn.execute(
                text("SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname = 'vector')")
            ).scalar_one()
            has_bigm = conn.execute(
                text("SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname = 'pg_bigm')")
            ).scalar_one()
            detail = (
                f"{version.split(',')[0]} | pgvector={'✓' if has_vector else '✗'} "
                f"| pg_bigm={'✓' if has_bigm else '✗'}"
            )
            return True, detail
    except Exception as exc:  # noqa: BLE001 - 健康检查需要吞掉所有异常
        return False, str(exc)
