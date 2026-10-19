"""业务服务层。

分层
----
- permission      : 权限强制注入（唯一出口）
- audit           : 审计日志
- tag_governance  : 标签治理（规范化 / 同义检测 / 合并废弃）
- ingestion       : 文档解析、切片、入库
- retrieval       : 检索编排
- conflict        : 冲突检测
- knowledge_base  : 知识库业务
- platform        : 应用与任务管理
"""

from app.services import (
    audit,
    conflict,
    ingestion,
    knowledge_base,
    permission,
    platform,
    retrieval,
    tag_governance,
)

__all__ = [
    "audit",
    "conflict",
    "ingestion",
    "knowledge_base",
    "permission",
    "platform",
    "retrieval",
    "tag_governance",
]
