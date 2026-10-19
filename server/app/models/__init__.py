"""ORM 模型统一导出。

Alembic 依赖此模块发现全部表定义，新增模型务必在此登记。
"""

from app.models.conflict import (
    CONFLICT_TYPES,
    RELATIONS,
    RESOLUTIONS,
    ConflictRecord,
)
from app.models.dispatch import (
    ALERT_STATUSES,
    CATEGORIES,
    EVENT_ACTIONS,
    NOTICE_TYPES,
    PARTS,
    STATUSES,
    AlertChatLink,
    AlertItem,
    AlertScanRun,
    DispatchAttachment,
    DispatchCheck,
    DispatchNotice,
    DispatchTask,
    DispatchTaskAssignee,
    DispatchTaskEvent,
    DispatchTaskPlan,
)
from app.models.document import Document, DocumentChunk
from app.models.knowledge_base import KnowledgeBase
from app.models.mixins import TimestampMixin
from app.models.platform import TASK_STATUSES, AsyncTask, AuditLog
from app.models.rbac import AppUser
from app.models.tag import Tag

__all__ = [
    # 知识库
    "KnowledgeBase",
    # 文档
    "Document",
    "DocumentChunk",
    # 标签
    "Tag",
    # 用户（投影，角色见 app/core/rbac_config.py）
    "AppUser",
    # 冲突
    "ConflictRecord",
    "CONFLICT_TYPES",
    "RELATIONS",
    "RESOLUTIONS",
    # 平台
    "AsyncTask",
    "AuditLog",
    "TASK_STATUSES",
    # 预警与任务流转
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
    "CATEGORIES",
    "STATUSES",
    "ALERT_STATUSES",
    "EVENT_ACTIONS",
    "NOTICE_TYPES",
    "PARTS",
    # 公共
    "TimestampMixin",
]
