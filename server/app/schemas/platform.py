"""平台侧 Schema：异步任务、审计日志。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, field_validator

from app.schemas.common import ORMModel


# ══════════════════════════════════════════════════════════════
#  异步任务
# ══════════════════════════════════════════════════════════════


class TaskOut(ORMModel):
    id: uuid.UUID
    task_type: str
    ref_id: int | None = None
    kb_id: int | None = None
    status: str
    progress: int
    stage: str | None = None
    error_msg: str | None = None
    result: dict[str, Any] | None = None
    created_by_app: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


# ══════════════════════════════════════════════════════════════
#  审计日志
# ══════════════════════════════════════════════════════════════


class AuditLogOut(ORMModel):
    id: int
    actor_type: str
    actor_id: int | None = None
    actor_name: str | None = None
    actor_app_id: str | None = None
    action: str
    resource_type: str | None = None
    resource_id: int | None = None
    resource_name: str | None = None
    detail: dict[str, Any] | None = None
    result: str
    ip: str | None = None
    user_agent: str | None = None
    created_at: datetime

    @field_validator("ip", mode="before")
    @classmethod
    def _coerce_ip(cls, v: Any) -> str | None:
        """PostgreSQL 的 INET 类型返回 ipaddress 对象，需转为字符串。

        不做这个转换，审计日志接口会因 Pydantic 校验失败而整体 500。
        """
        return str(v) if v is not None else None


class AuditStats(BaseModel):
    total: int
    today: int
    by_action: dict[str, int] = {}
    by_actor_type: dict[str, int] = {}


class AuditActionOut(BaseModel):
    """可供筛选的动作码列表。"""

    action: str
    label: str
    group: str
