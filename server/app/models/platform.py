"""平台侧实体：异步任务、审计日志。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# ══════════════════════════════════════════════════════════════
#  异步任务
# ══════════════════════════════════════════════════════════════

# pending | running | done | failed | conflict_pending
TASK_STATUSES = ("pending", "running", "done", "failed", "conflict_pending")


class AsyncTask(Base):
    """异步任务。文档上传、解析、向量化、冲突检测均为耗时操作，统一走异步。"""

    __tablename__ = "async_task"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # doc_ingest | conflict_scan | reindex | kb_delete
    task_type: Mapped[str] = mapped_column(String(32), nullable=False)
    ref_id: Mapped[int | None] = mapped_column(BigInteger)
    kb_id: Mapped[int | None] = mapped_column(BigInteger)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stage: Mapped[str | None] = mapped_column(String(32))
    error_msg: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    created_by_app: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[int | None] = mapped_column(BigInteger)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("idx_task_status", "status"),
        Index("idx_task_ref", "task_type", "ref_id"),
    )

    def __repr__(self) -> str:
        return f"<AsyncTask {self.id} {self.task_type} {self.status}>"


# ══════════════════════════════════════════════════════════════
#  审计日志
# ══════════════════════════════════════════════════════════════


class AuditLog(Base):
    """审计日志。

    ⚠️ 检索类操作的 detail 中必须包含 hit_doc_ids。
       否则当机密文档外泄时，无法追溯「谁通过搜索看到了它」——
       这是企业级审计的核心留痕点。
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # user | app | system
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    actor_id: Mapped[int | None] = mapped_column(BigInteger)
    actor_name: Mapped[str | None] = mapped_column(String(64))
    actor_app_id: Mapped[str | None] = mapped_column(String(64))

    # 动作码，如 doc.upload / doc.delete / search / acl.grant
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(32))
    resource_id: Mapped[int | None] = mapped_column(BigInteger)
    resource_name: Mapped[str | None] = mapped_column(String(256))

    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    result: Mapped[str] = mapped_column(String(16), nullable=False, default="success")
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(String(256))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_audit_actor", "actor_type", "actor_id", "created_at"),
        Index("idx_audit_action", "action", "created_at"),
        Index("idx_audit_res", "resource_type", "resource_id"),
        Index("idx_audit_time", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<AuditLog {self.action} by {self.actor_type}:{self.actor_id}>"
