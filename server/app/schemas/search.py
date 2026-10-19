"""检索与问答 Schema。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class SearchRequest(BaseModel):
    """检索请求。

    ⚠️ 权限条件**不在此处**：由后端强制注入，绝不接受调用方指定。
    """

    query: str = Field(min_length=1, max_length=1024)
    kb_ids: list[int] = Field(min_length=1)
    tag_filters: list[str] = Field(
        default_factory=list,
        description="标签过滤（全部命中），如 ['行政', '预算']",
    )
    top_k: int = Field(default=5, ge=1, le=50, description="返回条数（默认 5）")
    recall_k: int = Field(default=60, ge=10, le=500, description="向量召回候选数")
    rerank: bool = True
    # 是否包含已标记冲突的文档
    include_conflict: bool = False


class SearchResultItem(BaseModel):
    """检索结果条目。"""

    chunk_id: int
    doc_id: int
    doc_name: str
    kb_id: int
    kb_name: str | None = None
    version_no: int
    effective_date: str | None = None
    heading_path: str | None = None
    content: str
    # ⭐ parent-child：命中小块所属的父块上下文（同章节聚合），用于问答时给 LLM 更完整上下文。
    #    **检索时动态聚合**：只取同 (doc_id, heading_path) 且 is_retrievable=True 的兄弟切片，
    #    被冲突隔离/被覆盖的旧切片自动排除，不会经父块「后门」泄漏。
    #    None 表示无更大上下文（单切片章节或正文超长），此时应与 content 等价使用。
    parent_content: str | None = None
    source: str = "vector"           # vector | keyword | hybrid
    vector_score: float | None = None
    keyword_score: float | None = None
    fused_score: float | None = None
    rerank_score: float | None = None
    has_conflict: bool = False
    tags: list[str] = []


class FilterNotice(BaseModel):
    """过滤后无结果时的兜底提示。

    不做这个提示，用户会以为系统坏了 —— 大量「明明库里有却搜不到」的误报来源。
    """

    message: str
    suggestion: str | None = None
    relaxable_filters: list[str] = []
    baseline_total: int = 0


class SearchResponse(BaseModel):
    query: str
    total: int
    results: list[SearchResultItem] = []
    took_ms: int = 0
    recall_count: int = 0
    filter_notice: FilterNotice | None = None
    applied_filters: list[str] = []
    degraded: bool = False
    degraded_reason: str | None = None


class ChatRequest(BaseModel):
    """问答请求（RAG）。"""

    query: str = Field(min_length=1, max_length=1024)
    kb_ids: list[int] = Field(min_length=1)
    tag_filters: list[str] = []
    top_k: int = Field(default=6, ge=1, le=20)
    use_rerank: bool = True
    stream: bool = False
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)


class Citation(BaseModel):
    """引用来源。"""

    index: int
    chunk_id: int
    doc_id: int
    doc_name: str
    version_no: int
    effective_date: str | None = None
    heading_path: str | None = None
    snippet: str
    # ⭐ parent-child：父块上下文（问答拼装资料时优先使用，更完整）。
    #    同 SearchResultItem.parent_content，检索时动态聚合，已隔离切片自动排除。
    parent_content: str | None = None
    has_conflict: bool = False


class ChatResponse(BaseModel):
    answer: str
    citations: list[Citation] = []
    retrieval_took_ms: int = 0
    generation_took_ms: int = 0
    model: str = ""
    filter_notice: FilterNotice | None = None


class TagFilterOption(BaseModel):
    """前端标签过滤选择器的可选项。"""

    name: str
    usage_count: int = 0


class TagFilterOptionsResponse(BaseModel):
    kb_ids: list[int]
    options: list[TagFilterOption] = []


class ChunkPreviewResponse(ORMModel):
    doc_id: int
    doc_name: str
    total_chunks: int
    items: list[dict[str, Any]] = []
