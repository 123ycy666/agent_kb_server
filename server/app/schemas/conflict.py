"""冲突 Schema。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class ConflictSnippet(BaseModel):
    """冲突片段对照，供前端左右并排展示。"""

    new_text: str
    old_text: str


class ConflictOut(ORMModel):
    id: int
    kb_id: int
    new_doc_id: int
    old_doc_id: int
    new_chunk_id: int | None = None
    old_chunk_id: int | None = None
    conflict_type: str
    relation: str
    similarity_score: float | None = None
    rerank_score: float | None = None
    reason: str | None = None
    new_snippet: str | None = None
    old_snippet: str | None = None
    resolution: str | None = None
    resolved_by: int | None = None
    resolved_at: datetime | None = None
    status: str
    created_at: datetime

    # 展示用文件名
    new_doc_name: str | None = None
    old_doc_name: str | None = None
    old_doc_version: int | None = None


class ConflictResolveRequest(BaseModel):
    """人工决策冲突处理方式。

    四个选项对应设计方案中的决策弹窗：
    - overwrite : 用新文档覆盖旧文档（旧文档置为 superseded）
    - keep_old  : 保留旧文档，丢弃新文档
    - coexist   : 两者并存，仅记录冲突标记
    - cancel    : 取消本次上传（删除新文档）
    - ignore    : 忽略该冲突（保留双方，清除待处理标记）
    """

    resolution: str = Field(pattern="^(overwrite|keep_old|coexist|cancel|ignore)$")
    note: str | None = None


class ConflictStats(BaseModel):
    total: int
    pending: int
    resolved: int
    ignored: int
    by_type: dict[str, int] = {}
    by_relation: dict[str, int] = {}


class ConflictDetectRequest(BaseModel):
    """对指定文档重新执行冲突检测。"""

    doc_id: int


class FullScanRequest(BaseModel):
    """全库一致性扫描（仅管理员）。"""

    kb_id: int
    only_unscanned: bool = Field(
        default=True,
        description="仅扫描尚无冲突记录的文档，避免重复消耗模型调用",
    )


class FullScanResponse(BaseModel):
    kb_id: int
    scanned_documents: int
    skipped_documents: int
    total_conflicts: int
    llm_calls: int
    duration_ms: int
    documents_with_conflict: list[int] = []
    message: str
