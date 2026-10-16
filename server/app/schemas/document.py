"""文档与切片 Schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class DocumentOut(ORMModel):
    id: int
    kb_id: int
    root_id: int | None = None
    version_no: int
    is_active: bool
    filename: str
    file_ext: str
    file_size: int
    content_hash: str
    parse_status: str
    parse_error: str | None = None
    has_conflict: bool
    chunk_count: int
    # 服务端落盘的绝对路径（排障用：文件到底存哪了、是否被人为删掉）
    storage_path: str = ""
    # public = 所有人可见可检索；private = 仅上传者本人（管理员管理视图可见）
    visibility: str = "private"
    owner_id: int | None = None
    uploaded_by_app: str | None = None
    created_at: datetime
    updated_at: datetime

    # 附加展示字段
    tags: list[str] = []
    conflict_count: int = 0


class DocumentBrief(ORMModel):
    id: int
    kb_id: int
    filename: str
    version_no: int
    is_active: bool
    parse_status: str
    has_conflict: bool
    chunk_count: int
    created_at: datetime


class DocumentChunkOut(ORMModel):
    id: int
    doc_id: int
    chunk_index: int
    content: str
    heading_path: str | None = None
    embedding_model: str
    tags: list[str] = []
    is_retrievable: bool


class DocumentVersionOut(BaseModel):
    id: int
    version_no: int
    is_active: bool
    filename: str
    content_hash: str
    chunk_count: int
    created_at: datetime


class DocumentTagSetRequest(BaseModel):
    """给文档批量设置标签（覆盖式）。

    传空数组表示清空标签。标签名自由填写，系统会自动登记进词表。
    """

    tags: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="标签名列表，如 ['行政', '预算']",
    )


class DocumentVisibilityRequest(BaseModel):
    """修改文档可见性。

    - `public`  → 所有人可见、可检索
    - `private` → 仅上传者本人可见、可检索（管理员在管理视图可见）
    """

    visibility: Literal["public", "private"]


class UploadAcceptedResponse(BaseModel):
    """上传响应（含自动冲突检测结果）。"""

    task_id: str
    status: str = "pending"
    message: str = "文档已接收，正在解析与向量化"

    doc_id: int | None = None
    # 自动冲突检测：>0 表示该文档已被挂起（不参与检索），等待人工处理
    conflict_count: int = 0
    conflict_docs: list[str] = []
    warnings: list[str] = []


class ReindexRequest(BaseModel):
    """重建索引请求。"""

    kb_id: int | None = None
    doc_id: int | None = None
