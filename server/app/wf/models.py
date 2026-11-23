"""新工作流框架的 ORM：一份文档 = 一个工作流，定时器只是触发器。

与旧的 `app.workflow` 完全分开：旧表（wf_doc / wf_item / wf_notify ...）原样保留不动，
新框架只认下面这 5 张表。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger, DateTime, Index, Integer, SmallInteger, String, Text, UniqueConstraint,
    func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import WFBase as Base


def _ts():
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Flow(Base):
    """工作流文档：spec 是那份定死的五段结构（取数 / 计算 / 判断 / 回填 / 备注）。

    名字唯一只在「没被删」的行上成立：删掉一份文档后，同名可以再建。
    """

    __tablename__ = 'wf_flow'
    __table_args__ = (
        Index('uk_wf_flow_name', 'name', unique=True,
              postgresql_where=text('deleted_flag = 0')),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    enabled: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    spec: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict,
                                       server_default=text("'{}'::jsonb"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(), onupdate=func.now())
    deleted_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)


class FlowVersion(Base):
    """文档历史版本：每次保存正文留一份。"""

    __tablename__ = 'wf_flow_version'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    flow_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    spec: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict,
                                       server_default=text("'{}'::jsonb"))
    updated_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _ts()


class Trigger(Base):
    """定时任务：只存时间规则，跑哪些工作流挂在 wf_trigger_flow 上。

    `rule` 存周期：{"unit":"day|week|month","dow":[1..7],"dom":[1..28],"times":["08:00"]}
    `times` 保留成 rule.times 的副本，给列表直接显示。
    """

    __tablename__ = 'wf_trigger'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    times: Mapped[list] = mapped_column(JSONB, nullable=False, default=list,
                                        server_default=text("'[]'::jsonb"))
    rule: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict,
                                       server_default=text("'{}'::jsonb"))
    enabled: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(), onupdate=func.now())


class TriggerFlow(Base):
    """定时任务 ↔ 工作流（多对多，seq 决定执行顺序）。"""

    __tablename__ = 'wf_trigger_flow'
    __table_args__ = (UniqueConstraint('trigger_id', 'flow_id', name='uk_wf_trigger_flow'),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    trigger_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    flow_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class FlowRun(Base):
    """一轮运行：编译出的 SQL、取到几行、命中几行、写了几行、报错。"""

    __tablename__ = 'wf_flow_run'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    flow_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    flow_name: Mapped[str] = mapped_column(String(120), nullable=False, default='')
    flow_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False, default='manual')
    status: Mapped[str] = mapped_column(String(16), nullable=False, default='running')
    dry_run: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    started_at: Mapped[datetime] = _ts()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    elapsed_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    selected_n: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    hit_n: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    written_n: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sql_text: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)


TABLES = [Flow, FlowVersion, Trigger, TriggerFlow, FlowRun]


def ensure_tables() -> None:
    from app.core.database import wf_engine
    Base.metadata.create_all(bind=wf_engine, tables=[t.__table__ for t in TABLES])
