"""ORM 模型统一导出。

Alembic 依赖此模块发现全部表定义，新增模型务必在此登记。

2026-11-06：预警与派单那 10 张表的模型已随旧链路删除；工作流表以 `wf_` 前缀在
`app/workflow/models.py` 里，随 workflow 包导入即登记。
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

# 工作流框架（2026-11-06）
from app.workflow import models as _wf_models  # noqa: F401

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
