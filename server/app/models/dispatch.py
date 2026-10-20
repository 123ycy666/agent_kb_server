"""预警与任务流转（dispatch）ORM 模型。

设计依据：`docs/03-设计/specs/2026-10-19-预警四类归并与任务流转-design.md`

十条纪律（写代码前先看）：
  1. 业务库（MySQL `dlj_data`）**只读**；这 10 张表全部在 PG `ad_new`。
  2. 不建外键（与本项目一致）：表之间靠 id 维护，便于独立清理/重建。
  3. 时间一律 `timestamptz`；业务日期由调用方传 `today`，便于回放测试。
  4. 逻辑删除统一 `deleted_flag smallint`（任务不自动删，审计要）。
  5. `alert_item.dedupe_key` 唯一：一条预警一行，每轮扫描 upsert。
  6. 通知 `dedupe_key` 唯一：同一任务同一类型同一轮只发一条。
  7. 状态只允许 6 个值（见 `STATUSES`），其余用标志位表达（升级/逾期/归档）。
  8. 事件表 append-only，是时间线的唯一来源。
  9. 金额一律「元」存、展示时转万元；数字不许由模型生成。
 10. 新增列必须同时登记到 `db_init.EXTRA_DDL`（create_all 不给已有表补列）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# 预警四类（对外契约：只允许这四个值）
CATEGORIES = ("budget", "project", "review", "metric")

# 任务 6 状态
STATUSES = ("draft", "open", "doing", "verify", "closed", "cancelled")

# 预警实例状态
ALERT_STATUSES = ("open", "dispatched", "closed", "ignored", "resolved")

# 事件动作（append-only）
EVENT_ACTIONS = (
    "create", "confirm", "assign", "accept", "decline", "plan_submit", "plan_accept",
    "plan_reject", "reply", "urge", "escalate", "supervise", "transfer", "subtask",
    "verify_pass", "verify_fail", "close", "reopen", "archive", "cancel",
    "reject_invalid", "comment", "auto_close", "tick",
)

# 通知类型
NOTICE_TYPES = (
    "assign", "urge", "escalate", "supervise", "plan", "reply", "verify",
    "close", "reopen", "comment",
)

PARTS = ("owner", "collaborator", "cc")


class AlertScanRun(Base):
    """一次扫描批次（运维可观测：今天扫没扫、命中多少）。"""

    __tablename__ = "alert_scan_run"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_key: Mapped[str] = mapped_column(String(64), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    cadence: Mapped[str] = mapped_column(String(8), nullable=False, default="daily")
    account: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    found_n: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new_n: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    resolved_n: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    __table_args__ = (Index("uk_scan_run_key", "run_key", unique=True),)


class AlertItem(Base):
    """一条预警实例（同一件事只留一行）。"""

    __tablename__ = "alert_item"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    alert_type: Mapped[str] = mapped_column(String(16), nullable=False)
    subtype: Mapped[str] = mapped_column(String(32), nullable=False)
    level: Mapped[str] = mapped_column(String(8), nullable=False, server_default=text("'mid'"))
    dedupe_key: Mapped[str] = mapped_column(String(220), nullable=False)

    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)   # station|county|city
    scope_key: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_name: Mapped[str] = mapped_column(String(128), nullable=False)

    object_type: Mapped[str] = mapped_column(String(16), nullable=False, default="station")
    object_key: Mapped[str] = mapped_column(String(120), nullable=False, default="*")

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    verify_hint: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    assignee_role: Mapped[str | None] = mapped_column(String(16))
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    seen_times: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('1'))
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'open'"))
    task_id: Mapped[int | None] = mapped_column(BigInteger)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    close_reason: Mapped[str | None] = mapped_column(String(32))
    create_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    update_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "alert_type IN ('budget','project','review','metric')", name="ck_alert_type"
        ),
        CheckConstraint("level IN ('high','mid','low')", name="ck_alert_level"),
        Index("uk_alert_dedupe", "dedupe_key", unique=True),
        Index("idx_alert_status", "status", "level", "last_seen"),
        Index("idx_alert_type", "alert_type", "subtype"),
        Index("idx_alert_scope", "scope_key", "object_key"),
    )


class DispatchTask(Base):
    """处置任务（一条问题一张单；继续下发形成父子树）。"""

    __tablename__ = "dispatch_task"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_no: Mapped[str] = mapped_column(String(32), nullable=False)
    task_type: Mapped[str] = mapped_column(String(16), nullable=False, default="alert")
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    requirement: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    level: Mapped[str] = mapped_column(String(8), nullable=False, default="mid")

    alert_id: Mapped[int | None] = mapped_column(BigInteger)
    root_task_id: Mapped[int | None] = mapped_column(BigInteger)
    parent_task_id: Mapped[int | None] = mapped_column(BigInteger)

    scope_type: Mapped[str | None] = mapped_column(String(16))
    scope_key: Mapped[str | None] = mapped_column(String(64))
    scope_name: Mapped[str | None] = mapped_column(String(128))
    biz_type: Mapped[str | None] = mapped_column(String(16))
    biz_keys: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    creator_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    creator_name: Mapped[str] = mapped_column(String(64), nullable=False)
    creator_login: Mapped[str] = mapped_column(String(64), nullable=False)
    creator_dept_id: Mapped[int | None] = mapped_column(BigInteger)
    creator_session_id: Mapped[str | None] = mapped_column(String(64))

    reply_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    require_plan: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)

    verifier_type: Mapped[str] = mapped_column(String(16), nullable=False, default="auto")
    verify_rule: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    verify_state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    escalate_level: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    overdue_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    escalated_to: Mapped[int | None] = mapped_column(BigInteger)
    archived_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)

    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    close_reason: Mapped[str | None] = mapped_column(String(32))
    create_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    update_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    deleted_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','open','doing','verify','closed','cancelled')",
            name="ck_task_status",
        ),
        CheckConstraint("verifier_type IN ('auto','human')", name="ck_task_verifier"),
        Index("uk_task_no", "task_no", unique=True),
        # ⚠️ 索引名不能叫 idx_task_status：PG 索引名在 schema 内唯一，async_task 上已有同名索引，
        #    `IF NOT EXISTS` 会让这条静默跳过（2026-10-20 实测）。统一叫 idx_dispatch_status_due。
        Index("idx_dispatch_status_due", "status", "reply_due_at"),
        Index("idx_task_root", "root_task_id"),
        Index("idx_task_creator", "creator_id", "create_time"),
        Index("idx_task_alert", "alert_id"),
        Index("idx_task_assignee_due", "reply_due_at"),
    )


class DispatchTaskAssignee(Base):
    """任务参与人：主责可多人，协办/抄送各可多人。"""

    __tablename__ = "dispatch_task_assignee"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    part: Mapped[str] = mapped_column(String(16), nullable=False, default="owner")
    user_id: Mapped[int | None] = mapped_column(BigInteger)
    employee_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    login_name: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str | None] = mapped_column(String(64))
    dept_id: Mapped[int | None] = mapped_column(BigInteger)
    dept_name: Mapped[str | None] = mapped_column(String(128))
    role_code: Mapped[str | None] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    session_id: Mapped[str | None] = mapped_column(String(64))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    declined_reason: Mapped[str | None] = mapped_column(String(500))
    create_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    update_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("part IN ('owner','collaborator','cc')", name="ck_assignee_part"),
        CheckConstraint(
            "state IN ('pending','accepted','replied','done','declined')",
            name="ck_assignee_state",
        ),
        Index("uk_assignee", "task_id", "employee_id", "part", unique=True),
        Index("idx_assignee_emp", "employee_id", "state"),
    )


class DispatchTaskEvent(Base):
    """任务事件流水（append-only；时间线只读这张表）。"""

    __tablename__ = "dispatch_task_event"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[int | None] = mapped_column(BigInteger)
    actor_name: Mapped[str | None] = mapped_column(String(64))
    actor_role: Mapped[str | None] = mapped_column(String(16))
    from_status: Mapped[str | None] = mapped_column(String(16))
    to_status: Mapped[str | None] = mapped_column(String(16))
    content: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    create_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("idx_event_task", "task_id", "create_time"),)


class DispatchTaskPlan(Base):
    """方案/建议/报告正文（带版本）。"""

    __tablename__ = "dispatch_task_plan"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    title: Mapped[str | None] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    items: Mapped[list[Any] | None] = mapped_column(JSONB)
    source: Mapped[str] = mapped_column(String(8), nullable=False, default="ai")
    author_id: Mapped[int | None] = mapped_column(BigInteger)
    author_name: Mapped[str | None] = mapped_column(String(64))
    accepted: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    accepted_by: Mapped[int | None] = mapped_column(BigInteger)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    create_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('ai_advice','decision','owner_plan','report')", name="ck_plan_kind"
        ),
        Index("uk_plan_ver", "task_id", "kind", "version", unique=True),
    )


class DispatchCheck(Base):
    """每次核销检查的实测值（自动核销的判据）。"""

    __tablename__ = "dispatch_check"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    check_kind: Mapped[str] = mapped_column(String(8), nullable=False, default="auto")
    rule: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    measured: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    passed: Mapped[bool | None] = mapped_column(Boolean)
    message: Mapped[str | None] = mapped_column(String(500))
    check_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("idx_check_task", "task_id", "check_time"),)


class DispatchNotice(Base):
    """站内通知（收件箱）。"""

    __tablename__ = "dispatch_notice"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int | None] = mapped_column(BigInteger)
    notice_type: Mapped[str] = mapped_column(String(16), nullable=False)
    receiver_id: Mapped[int | None] = mapped_column(BigInteger)
    receiver_login: Mapped[str | None] = mapped_column(String(64))
    receiver_name: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="inapp")
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    read_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dedupe_key: Mapped[str | None] = mapped_column(String(220))

    __table_args__ = (
        Index("uk_notice_dedupe", "dedupe_key", unique=True),
        Index("idx_notice_rcv", "receiver_login", "read_flag", "sent_at"),
        Index("idx_notice_task", "task_id"),
    )


class DispatchAttachment(Base):
    """任务附件（回执材料/报告/复盘）。"""

    __tablename__ = "dispatch_attachment"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_id: Mapped[int | None] = mapped_column(BigInteger)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    md5: Mapped[str | None] = mapped_column(String(32))
    uploader_id: Mapped[int | None] = mapped_column(BigInteger)
    uploader_name: Mapped[str | None] = mapped_column(String(64))
    create_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("idx_attach_task", "task_id"),)


class AlertChatLink(Base):
    """预警 ↔ 对话会话的绑定（同一个人重复点同一条预警复用同一会话）。"""

    __tablename__ = "alert_chat_link"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    alert_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    employee_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    create_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("uk_alert_chat", "alert_id", "employee_id", unique=True),
        Index("idx_alert_chat_session", "session_id"),
    )


__all__ = [
    "CATEGORIES",
    "STATUSES",
    "ALERT_STATUSES",
    "EVENT_ACTIONS",
    "NOTICE_TYPES",
    "PARTS",
    "AlertScanRun",
    "AlertItem",
    "DispatchTask",
    "DispatchTaskAssignee",
    "DispatchTaskEvent",
    "DispatchTaskPlan",
    "DispatchCheck",
    "DispatchNotice",
    "DispatchAttachment",
    "AlertChatLink",
]
