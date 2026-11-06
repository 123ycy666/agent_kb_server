"""工作流框架 ORM（纯机制：没有一处业务字段）。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Float, Index, Integer, SmallInteger, String, Text,
    UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _ts(nullable: bool = False):
    return mapped_column(DateTime(timezone=True), server_default=func.now(),
                         nullable=nullable)


class RuleDoc(Base):
    """规则文档：一行 = 一份，正文里是分节文本。"""

    __tablename__ = 'wf_doc'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    work_type: Mapped[str] = mapped_column(String(16), nullable=False, default='detect')
    alert_type: Mapped[str] = mapped_column(String(64), nullable=False, default='')
    enabled: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    disabled_reason: Mapped[str | None] = mapped_column(Text)
    scope_level: Mapped[str] = mapped_column(String(16), nullable=False, default='city')
    schedule_id: Mapped[int | None] = mapped_column(BigInteger)
    doc: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict,
                                      server_default=text("'{}'::jsonb"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(), onupdate=func.now())
    deleted_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)


class DocVersion(Base):
    """文档历史版本（每次保存一份）。"""

    __tablename__ = 'wf_doc_version'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    doc_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    doc: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    updated_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _ts()


class Schedule(Base):
    """调度方案：一组运行时间点 + 挂在上面的文档。"""

    __tablename__ = 'wf_schedule'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    cron: Mapped[str] = mapped_column(String(64), nullable=False)
    crons: Mapped[list] = mapped_column(JSONB, nullable=False, default=list,
                                        server_default=text("'[]'::jsonb"))
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default='Asia/Shanghai')
    enabled: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(), onupdate=func.now())


class DocSchedule(Base):
    """文档 ↔ 定时方案（多对多）：一份文档可挂多个方案，例如 8 点一个、9 点一个。"""

    __tablename__ = 'wf_doc_schedule'
    __table_args__ = (UniqueConstraint('doc_id', 'schedule_id', name='uk_wf_doc_sched'),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    doc_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    schedule_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)


class Run(Base):
    """一轮运行（过程）。没命中也有这一行。"""

    __tablename__ = 'wf_run'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_key: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    doc_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    doc_name: Mapped[str] = mapped_column(String(120), nullable=False, default='')
    doc_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    work_type: Mapped[str] = mapped_column(String(16), nullable=False, default='detect')
    trigger: Mapped[str] = mapped_column(String(16), nullable=False, default='manual')
    started_at: Mapped[datetime] = _ts()
    finished_at: Mapped[datetime | None] = _ts(nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default='running')
    data_as_of: Mapped[str | None] = mapped_column(String(32))
    checked_n: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    hit_n: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new_n: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    closed_n: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sent_n: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    dry_run: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)


class RunLog(Base):
    """一轮里的步骤留痕（SQL、工具调用、错误）。"""

    __tablename__ = 'wf_run_log'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    at: Mapped[datetime] = _ts()
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default='tool')
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict,
                                          server_default=text("'{}'::jsonb"))


class CheckResult(Base):
    """每个对象每轮一行：命中与不命中都写。"""

    __tablename__ = 'wf_result'
    __table_args__ = (UniqueConstraint('run_id', 'doc_id', 'object_key', name='uk_wf_result'),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    doc_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    object_key: Mapped[str] = mapped_column(String(160), nullable=False)
    object_name: Mapped[str] = mapped_column(String(200), nullable=False, default='')
    scope_type: Mapped[str | None] = mapped_column(String(16))
    scope_key: Mapped[str | None] = mapped_column(String(64))
    value: Mapped[float | None] = mapped_column(Float)
    threshold: Mapped[float | None] = mapped_column(Float)
    op: Mapped[str | None] = mapped_column(String(8))
    tier: Mapped[str | None] = mapped_column(String(8))
    hit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict,
                                         server_default=text("'{}'::jsonb"))
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict,
                                           server_default=text("'{}'::jsonb"))
    reliable: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    created_at: Mapped[datetime] = _ts()


class Item(Base):
    """结果：一个问题一行，可关闭。"""

    __tablename__ = 'wf_item'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(240), unique=True, nullable=False)
    doc_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    alert_type: Mapped[str] = mapped_column(String(64), nullable=False, default='')
    object_key: Mapped[str] = mapped_column(String(160), nullable=False)
    object_name: Mapped[str] = mapped_column(String(200), nullable=False, default='')
    scope_type: Mapped[str | None] = mapped_column(String(16))
    scope_key: Mapped[str | None] = mapped_column(String(64))
    value: Mapped[float | None] = mapped_column(Float)
    threshold: Mapped[float | None] = mapped_column(Float)
    op: Mapped[str | None] = mapped_column(String(8))
    tier: Mapped[str | None] = mapped_column(String(8))
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict,
                                         server_default=text("'{}'::jsonb"))
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict,
                                           server_default=text("'{}'::jsonb"))
    reliable: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    title: Mapped[str] = mapped_column(String(300), nullable=False, default='')
    body: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default='open', index=True)
    close_reason: Mapped[str | None] = mapped_column(String(32))
    first_seen: Mapped[datetime] = _ts()
    last_seen: Mapped[datetime] = _ts()
    seen_times: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    run_id_last: Mapped[int | None] = mapped_column(BigInteger)
    sent_at: Mapped[datetime | None] = _ts(nullable=True)
    task_id: Mapped[int | None] = mapped_column(BigInteger)


class Notify(Base):
    """发送记录。"""

    __tablename__ = 'wf_notify'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    item_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    recipient_login: Mapped[str] = mapped_column(String(64), nullable=False, default='')
    recipient_name: Mapped[str | None] = mapped_column(String(64))
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default='station')
    message: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime] = _ts()
    ok: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    error: Mapped[str | None] = mapped_column(Text)
    run_id: Mapped[int | None] = mapped_column(BigInteger)
    doc_version: Mapped[int | None] = mapped_column(Integer)


class Param(Base):
    """运行参数（存取规则，不属业务文档）。"""

    __tablename__ = 'wf_param'

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)


TABLES = [RuleDoc, DocVersion, Schedule, DocSchedule, Run, RunLog, CheckResult, Item, Notify, Param]

DEFAULT_PARAMS = {
    'max_steps': ('24', '单轮最多几步工具调用'),
    'max_items_per_run': ('200', '单轮最多写几条事项'),
    'no_repeat_days': ('1', '同一事项同一收件人多少天内不重发'),
    'retention_months': ('12', '结果流水保留月数'),
    'send_channel_default': ('station', '默认通道'),
}
