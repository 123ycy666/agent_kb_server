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
    pool_recycle=1800,       # 主动换掉超过 30 分钟的空闲连接（见下方 MySQL 注释）
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

# 连接/读写超时（秒）。见 _mysql_url 注释：用来把"静默死连接"的等待从
# 操作系统的 TCP 重传超时（~20s）压到可控范围。
MYSQL_CONNECT_TIMEOUT = 5
MYSQL_READ_TIMEOUT = 10

# 空闲连接主动回收阈值（秒）。
# ⚠️ 这是"登录很慢"的根治点：公网上的 MySQL 连接空闲久了会被 NAT/防火墙
#    静默丢弃，而 pool_pre_ping 去 ping 这条死连接时没有 RST 可等，
#    只能干等到 TCP 重传超时 —— 实测**首个登录请求卡 19.7 秒**，之后才 0.2 秒。
#    设 300 后，空闲超过 5 分钟的连接在借出前就被丢弃重建，不再触发该等待。
MYSQL_POOL_RECYCLE_SECONDS = 300


def _mysql_url(db_name: str) -> str:
    # ⚠️ 用户名/密码必须 URL 编码：密码含 @ : / 等字符时
    #    （如 8FcXqrhYM@）会破坏 URL 结构，报
    #    "Can't connect to MySQL server on '@47.121.183.81'" 这类莫名错误。
    from urllib.parse import quote_plus

    user = quote_plus(settings.mysql_user)
    pwd = quote_plus(settings.mysql_password)
    # ⚠️ 三个超时必须显式给：MySQL 在公网上，中间隔着 NAT/防火墙，
    #    连接被静默丢弃（不回 RST）时，默认要等操作系统 TCP 重传超时（Windows ~20s）
    #    才会报错。给了 read_timeout 就能在 10s 内快速失败 → 立刻换新连接。
    return (
        f"mysql+pymysql://{user}:{pwd}"
        f"@{settings.mysql_host}:{settings.mysql_port}/{db_name}"
        f"?charset={settings.mysql_charset}"
        f"&connect_timeout={MYSQL_CONNECT_TIMEOUT}"
        f"&read_timeout={MYSQL_READ_TIMEOUT}"
        f"&write_timeout={MYSQL_READ_TIMEOUT}"
    )


# 员工认证只读库（dlj_data）。连接池小一点：只用于登录与同步。
mysql_business_engine = create_engine(
    _mysql_url(settings.mysql_db_business),
    pool_pre_ping=True,
    # 见 MYSQL_POOL_RECYCLE_SECONDS 的注释：这条是"首次登录慢 20 秒"的根治点。
    pool_recycle=MYSQL_POOL_RECYCLE_SECONDS,
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
