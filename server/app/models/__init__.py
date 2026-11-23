"""ORM 模型统一导出。

Alembic 依赖此模块发现全部表定义，新增模型务必在此登记。

2026-11-23：旧工作流包 `app/workflow` 已删除，工作流表改在 `app/wf/models.py`
（独立库 ad_wf，自带 ensure_tables），不在这里登记。
"""

from app.models.conflict import (
    CONFLICT_TYPES,
    RELATIONS,
    RESOLUTIONS,
    ConflictRecord,
)
from app.models.document import Document, DocumentChunk
from app.models.knowledge_base import KnowledgeBase
from app.models.mixins import TimestampMixin
from app.models.platform import TASK_STATUSES, AsyncTask, AuditLog
from app.models.rbac import AppUser
from app.models.tag import Tag

__all__ = [
    "KnowledgeBase",
    "Document",
    "DocumentChunk",
    "Tag",
    "AppUser",
    "ConflictRecord",
    "CONFLICT_TYPES",
    "RELATIONS",
    "RESOLUTIONS",
    "AsyncTask",
    "AuditLog",
    "TASK_STATUSES",
    "TimestampMixin",
]
