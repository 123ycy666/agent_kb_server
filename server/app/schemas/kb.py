"""知识库 Schema。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None
    # 不传则使用当前 PROVIDER_MODE 下的默认 embedding 模型
    embedding_model: str | None = None
    chunk_size: int = Field(default=512, ge=64, le=4096)
    chunk_overlap: int = Field(default=64, ge=0, le=1024)


class KnowledgeBaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    chunk_size: int | None = Field(default=None, ge=64, le=4096)
    chunk_overlap: int | None = Field(default=None, ge=0, le=1024)
    status: str | None = Field(default=None, pattern="^(active|archived)$")
    # 是否参与「智能问答」的默认检索范围（2026-10-15 新增）
    qa_enabled: bool | None = None


class KnowledgeBaseOut(ORMModel):
    id: int
    name: str
    description: str | None = None
    embedding_model: str
    embedding_dim: int
    chunk_size: int
    chunk_overlap: int
    status: str
    # 是否参与「智能问答」的默认检索范围（问答页据此决定默认勾选哪些库）
    qa_enabled: bool = False
    created_at: datetime
    updated_at: datetime

    # 统计（列表页展示）
    document_count: int = 0
    chunk_count: int = 0
    # ⚠️ 当前主体在该库中可见的文档数。
    #    为 0 时前端需明确提示「你暂时没有权限查看该库的文档」，
    #    而不是显示空白 —— 本期不做知识库级入口权限，必须避免空库困惑。
    visible_document_count: int = 0


class EmbeddingModelChangeRequest(BaseModel):
    """切换 embedding 模型。

    ⚠️ 维度或模型变化时必须重建索引，否则检索结果会静默出错。
    """

    embedding_model: str
    embedding_dim: int = Field(ge=1)
    rebuild: bool = False


class EmbeddingModelChangeResponse(BaseModel):
    changed: bool
    need_rebuild: bool
    affected_chunks: int
    message: str


class ReindexResponse(BaseModel):
    """重建索引结果。"""

    kb_id: int | None = None
    doc_id: int | None = None
    document_count: int
    chunk_count: int
    from_model: str
    to_model: str
    duration_ms: int
    message: str
