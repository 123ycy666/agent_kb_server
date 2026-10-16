"""数据库初始化：建表、建扩展、写入种子数据。

设计说明
--------
P0-A 阶段使用 `metadata.create_all()` 快速建表（开发友好），
同时保留 Alembic 配置供后续演进使用。两者不冲突：
create_all 只在表不存在时创建，不会破坏已有结构。

⚠️ 必须按顺序做三件事：
1. 启用 pgvector 扩展（否则 vector 类型的表建不出来）
2. 建表（含 HNSW 向量索引与 GIN 标签索引）
3. 写入种子数据（权限点、内置角色、管理员账号）
"""

from __future__ import annotations

import logging

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import Base, SessionLocal, engine
from app.core.security import hash_password
from app.models import AppUser

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
#  增量迁移（幂等）
# ══════════════════════════════════════════════════════════════

# ⚠️ 重要认知：`Base.metadata.create_all()` 只在**表不存在**时建表，
#    对已存在的表**不会**补加新列或新索引。
#    因此每个后续阶段新增的字段都必须在这里显式登记一条幂等 DDL，
#    否则老库升级后会直接报 "column ... does not exist"。
EXTRA_DDL: list[tuple[str, str]] = [
    # ── 知识库「参与智能问答」标记（2026-10-15）────────────────
    # 用「库上打标记」代替「配置里写 id 列表」：自增 id 会因删库/建库漂移
    # （删了不复用、新建拿更大值），写死的配置会悬空或失效。
    (
        "knowledge_base.qa_enabled 列（是否参与智能问答默认检索范围）",
        "ALTER TABLE knowledge_base ADD COLUMN IF NOT EXISTS qa_enabled boolean NOT NULL DEFAULT false",
    ),
    # ── P0-B：关键词检索路 ──────────────────────────────────
    (
        "document_chunk.search_text 列（关键词检索）",
        "ALTER TABLE document_chunk ADD COLUMN IF NOT EXISTS search_text tsvector",
    ),
    (
        "document_chunk.search_text 的 GIN 索引",
        "CREATE INDEX IF NOT EXISTS idx_chunk_fts ON document_chunk USING gin (search_text)",
    ),
    # ── 版本链下架可回滚 ────────────────────────────────────
    # ⚠️ 为什么要这两个字段：下架旧版原先是「单向」操作
    #    （is_active=False + 旧 chunk is_retrievable=False），系统答不出
    #    「谁取代了我」。一旦取代者被删除，旧版就永久不可检索且无恢复入口。
    (
        "document.superseded_by 列（谁取代了我，用于删除时回滚复活）",
        "ALTER TABLE document ADD COLUMN IF NOT EXISTS superseded_by bigint",
    ),
    (
        "document.retired_reason 列（下架原因：版本升级 / 冲突覆盖）",
        "ALTER TABLE document ADD COLUMN IF NOT EXISTS retired_reason varchar(32)",
    ),
    (
        "document.superseded_by 的索引（删除时按继任者反查）",
        "CREATE INDEX IF NOT EXISTS idx_doc_superseded_by ON document (superseded_by)",
    ),
    # ── 冲突隔离细化到切片 ──────────────────────────────────
    # ⚠️ 为什么加：冲突挂起原先落在**文档级**（doc.has_conflict 一票否决整篇），
    #    一个切片冲突就让整份文档从检索里消失。改为标记冲突切片后，
    #    需要区分「被新版取代」与「被冲突隔离」两种下架原因，否则复活时会误判。
    (
        "document_chunk.retired_reason 列（切片下架原因：version / conflict / overwrite）",
        "ALTER TABLE document_chunk ADD COLUMN IF NOT EXISTS retired_reason varchar(16)",
    ),
    (
        "document_chunk.superseded_by 列（切片级取代关系，删除新文档时精确恢复）",
        "ALTER TABLE document_chunk ADD COLUMN IF NOT EXISTS superseded_by bigint",
    ),
    (
        "document_chunk.superseded_by 的索引（删除文档时反查被它取代的切片）",
        "CREATE INDEX IF NOT EXISTS idx_chunk_superseded_by ON document_chunk (superseded_by)",
    ),
    # ── parent-child 检索上下文 ────────────────────────────────
    # ⚠️ 父块文本仅作返回上下文，不参与向量索引，故只需加列，无需索引。
    (
        "document_chunk.parent_content 列（父块上下文，parent-child 检索）",
        "ALTER TABLE document_chunk ADD COLUMN IF NOT EXISTS parent_content text",
    ),
]


def ensure_extra_columns() -> list[str]:
    """补齐后续阶段新增的列与索引。幂等，可反复执行。"""
    applied: list[str] = []
    with engine.connect() as conn:
        for label, sql in EXTRA_DDL:
            try:
                conn.execute(text(sql))
                conn.commit()
                applied.append(label)
            except Exception as exc:  # noqa: BLE001
                conn.rollback()
                logger.warning("补齐「%s」失败：%s", label, exc)
    return applied


def seed_admin(db: Session) -> dict:
    """从统一用户表 t_employee 同步管理员投影（幂等）。

    2026-10-14 起不再本地创建管理员：管理员权威在 HR 系统
    （dlj_data.t_employee，administrator_flag=1）。启动时把在任管理员
    同步成 PG 投影行，首个登录前的管理操作即可用。

    MySQL 不可达时不阻塞启动（记警告），等登录时再自动同步。
    """
    try:
        from app.core.database import MySQLBusinessSession
        from app.services import employee
        from sqlalchemy import text

        mysql_db = MySQLBusinessSession()
        try:
            rows = mysql_db.execute(
                text(
                    "SELECT employee_id, login_name, actual_name, email, phone, "
                    "administrator_flag, disabled_flag, deleted_flag "
                    "FROM t_employee WHERE administrator_flag = 1 AND deleted_flag = 0"
                )
            ).mappings().all()
        finally:
            mysql_db.close()

        synced = 0
        for row in rows:
            emp = employee.EmployeeInfo(
                employee_id=int(row["employee_id"]),
                login_name=row["login_name"],
                actual_name=row["actual_name"],
                email=row["email"],
                phone=row["phone"],
                administrator_flag=True,
                disabled=bool(row["disabled_flag"]),
                deleted=False,
            )
            employee.sync_projection(db, emp)
            synced += 1
        return {"synced": synced, "source": "t_employee"}
    except Exception as exc:  # noqa: BLE001 - MySQL 不可达不阻塞启动
        logger.warning("t_employee 管理员同步跳过（MySQL 暂不可达，登录时会自动同步）：%s", exc)
        return {"synced": 0, "source": "t_employee", "skipped": str(exc)}


def init_database(verbose: bool = True) -> dict:
    """完整初始化流程。幂等，可重复调用。"""
    result: dict = {}

    result["extensions"] = ensure_extensions()
    create_tables()
    result["tables"] = "created"
    result["migrations"] = ensure_extra_columns()

    db = SessionLocal()
    try:
        result["admin_sync"] = seed_admin(db)
    finally:
        db.close()

    if verbose:
        logger.info("数据库初始化完成：%s", result)
    return result


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    print(init_database())
