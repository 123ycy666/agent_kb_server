"""工作流框架 ORM（纯机制：没有一处业务字段）。

2026-11-07 定死：只存两份数据 —— 结果（`wf_item`）与人看到的一句话（`wf_notify`）。
过程不建表（每轮每对象的流水、通知与结果的中间表都不建）。
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger, DateTime, Index, Integer, Numeric, SmallInteger, String,
    Text, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import WFBase as Base


def _ts(nullable: bool = False):
    return mapped_column(DateTime(timezone=True), server_default=func.now(),
                         nullable=nullable)


def _ts_null():
    """可空时间列：没写就是没写，不给默认值（read_at / sent_at 靠它判未读）。"""
    return mapped_column(DateTime(timezone=True), nullable=True)


class RuleDoc(Base):
    """规则文档：一行 = 一份，正文里是分节文本。发送文档用 pair_doc_id 指回取数文档。"""

    __tablename__ = 'wf_doc'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    work_type: Mapped[str] = mapped_column(String(16), nullable=False, default='detect')
    alert_type: Mapped[str] = mapped_column(String(64), nullable=False, default='')
    enabled: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    disabled_reason: Mapped[str | None] = mapped_column(Text)
    scope_level: Mapped[str] = mapped_column(String(16), nullable=False, default='city')
    schedule_id: Mapped[int | None] = mapped_column(BigInteger)
    pair_doc_id: Mapped[int | None] = mapped_column(BigInteger)
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
    """文档 ↔ 定时方案（多对多）。"""

    __tablename__ = 'wf_doc_schedule'
    __table_args__ = (UniqueConstraint('doc_id', 'schedule_id', name='uk_wf_doc_sched'),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    doc_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    schedule_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)


class Run(Base):
    """一轮运行（过程只留这里与 wf_run_log，不另建流水表）。"""

    __tablename__ = 'wf_run'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_key: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    doc_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    doc_name: Mapped[str] = mapped_column(String(120), nullable=False, default='')
    doc_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    work_type: Mapped[str] = mapped_column(String(16), nullable=False, default='detect')
    trigger: Mapped[str] = mapped_column(String(16), nullable=False, default='manual')
    started_at: Mapped[datetime] = _ts()
    finished_at: Mapped[datetime | None] = _ts_null()
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
    """一轮里的步骤留痕（SQL、工具调用、错误）—— 排错就看这里。"""

    __tablename__ = 'wf_run_log'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    at: Mapped[datetime] = _ts()
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default='tool')
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict,
                                          server_default=text("'{}'::jsonb"))


class Item(Base):
    """预警结果：一条 = 一个对象在一条判据下的当前结论。"""

    __tablename__ = 'wf_item'
    __table_args__ = (
        UniqueConstraint('doc_id', 'object_key', name='uk_wf_item'),
        Index('idx_wf_item_live', 'status', 'alert_type', 'tier'),
        Index('idx_wf_item_county', 'county_name', 'status'),
        Index('idx_wf_item_unit', 'unit_name', 'status'),
        Index('idx_wf_item_seen', 'last_seen'),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    doc_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    doc_name: Mapped[str] = mapped_column(String(120), nullable=False, default='')
    alert_type: Mapped[str] = mapped_column(String(16), nullable=False, default='')
    object_key: Mapped[str] = mapped_column(String(160), nullable=False)
    object_name: Mapped[str] = mapped_column(String(200), nullable=False, default='')
    unit_name: Mapped[str] = mapped_column(String(120), nullable=False, default='')
    county_name: Mapped[str] = mapped_column(String(120), nullable=False, default='')
    value: Mapped[Decimal | None] = mapped_column(Numeric(16, 4))
    value_unit: Mapped[str] = mapped_column(String(16), nullable=False, default='')
    threshold: Mapped[Decimal | None] = mapped_column(Numeric(16, 4))
    tier: Mapped[str | None] = mapped_column(String(8))
    reliable: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default='open', index=True)
    close_reason: Mapped[str | None] = mapped_column(String(32))
    first_seen: Mapped[datetime] = _ts()
    last_seen: Mapped[datetime] = _ts()
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict,
                                         server_default=text("'{}'::jsonb"))
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict,
                                           server_default=text("'{}'::jsonb"))
    sent_at: Mapped[datetime | None] = _ts_null()
    task_id: Mapped[int | None] = mapped_column(BigInteger)


class Notify(Base):
    """每人版本：一条 = 一个人收到的一句话，覆盖哪几条结果存在 alert_ids 里。"""

    __tablename__ = 'wf_notify'
    __table_args__ = (
        Index('idx_wf_notify_mine', 'recipient_login', 'read_at', 'sent_at'),
        Index('idx_wf_notify_scope', 'alert_type', 'level', 'sent_at'),
        Index('idx_wf_notify_alerts', 'alert_ids', postgresql_using='gin'),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    alert_type: Mapped[str] = mapped_column(String(16), nullable=False, default='')
    scope_name: Mapped[str] = mapped_column(String(120), nullable=False, default='')
    recipient_login: Mapped[str] = mapped_column(String(64), nullable=False)
    recipient_name: Mapped[str | None] = mapped_column(String(64))
    role_code: Mapped[str | None] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(String(200), nullable=False)
    alert_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=False)
    doc_id: Mapped[int | None] = mapped_column(BigInteger)
    sent_at: Mapped[datetime] = _ts()
    read_at: Mapped[datetime | None] = _ts_null()
    reason: Mapped[str | None] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default='station')


class Param(Base):
    """运行参数（存取规则，不属业务文档）。"""

    __tablename__ = 'wf_param'

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)


TABLES = [RuleDoc, DocVersion, Schedule, DocSchedule, Run, RunLog, Item, Notify, Param]

DEFAULT_PARAMS = {
    'max_steps': ('24', '单轮最多几步工具调用'),
    'max_items_per_run': ('200', '单轮最多写几条结果'),
    'no_repeat_days': ('1', '同一收件人同一层面同一类多少天内不重发'),
    'retention_months': ('12', '运行日志保留月数'),
    'send_channel_default': ('station', '默认通道'),
}


