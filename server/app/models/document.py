"""文档与切片。

设计要点
--------
1. 版本链：root_id 标识同一份文档的版本族，version_no 递增，仅最新版 is_active=true
2. 元数据下沉：document_chunk.tags 冗余保存文档级标签，使检索时单表即可过滤，避免 JOIN
3. 可见性二值化：document.visibility 只有 public / private；document_chunk.is_retrievable
   仅表示「切片在库中是否可用」（由版本取代决定），与权限无关
4. embedding_model：记录生成该向量的模型。不同模型的向量即使维度相同也不可混用，
   该字段是「切换模型必须重建索引」这一强制机制的判定依据。
"""

from __future__ import annotations

from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
# ⚠️ 必须用 **postgresql.ARRAY**，不能用 sqlalchemy.ARRAY ——
#    后者的 .contains() / .any() 会抛 NotImplementedError（「请使用方言专用类型」）。
#    这会让按标签过滤的接口直接 500，而调用方很容易把它误读成「没有匹配结果」。
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import settings
from app.core.database import Base
from app.models.mixins import TimestampMixin


class Document(Base, TimestampMixin):
    """文档。"""

    __tablename__ = "document"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("knowledge_base.id", ondelete="CASCADE"), nullable=False
    )

    # ── 版本链 ────────────────────────────────────────────────
    root_id: Mapped[int | None] = mapped_column(BigInteger)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # ── 下架回滚 ──────────────────────────────────────────────
    # ⚠️ 这两个字段解决「孤儿版本」缺陷（2026-10-08）。
    #    下架旧版原先是单向操作，系统答不出「谁取代了我」，
    #    于是一旦取代者被删除，旧版永久不可检索且无恢复入口。
    #    删除文档时按 superseded_by 反查并回滚复活，让版本链可自愈。
    superseded_by: Mapped[int | None] = mapped_column(BigInteger)
    # version_supersede（同文件名新版本取代） | conflict_overwrite（冲突处理时新文档覆盖）
    retired_reason: Mapped[str | None] = mapped_column(String(32))

    # ── 文件信息 ──────────────────────────────────────────────
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    file_ext: Mapped[str] = mapped_column(String(16), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    # ── 处理状态 ──────────────────────────────────────────────
    # pending | parsing | embedding | ready | failed
    parse_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    parse_error: Mapped[str | None] = mapped_column(Text)
    has_conflict: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ── 可见性 ────────────────────────────────────────────────
    # public  = 所有人可看、可检索
    # private = 仅上传者本人（+ 管理员在管理视图）可看、可检索
    # ⚠️ 已简化为二值，不再支持「授权给指定用户/角色」的细粒度 ACL。
    visibility: Mapped[str] = mapped_column(
        String(16), nullable=False, default="private", server_default="private"
    )

    # ── 文档类型（2026-10-30）──────────────────────────────────
    # rule = 规则/口径（每轮注入给模型，不参与向量检索）
    # doc  = 资料（走检索，按相关度召回）
    # 判定由服务端按文件名做（`services/ingestion/pipeline.py` 里的 `_doc_type_of`），
    # 前端不需要多传字段。
    doc_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="doc", server_default="doc"
    )

    # ── 标签（标签名数组）────────────────────────────────────
    # ⚠️ server_default 必须用 text() 包裹，且字面量带单引号：
    #    PostgreSQL 的 ARRAY 空数组字面量是 '{}'；
    #    若直接写字符串 "'{}'"，SQLAlchemy 会再加一层引号变成 '''{}'''，建表即失败。
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), nullable=False, default=list, server_default=text("'{}'")
    )

    # ── 归属 ──────────────────────────────────────────────────
    owner_id: Mapped[int | None] = mapped_column(BigInteger)
    uploaded_by_app: Mapped[str | None] = mapped_column(String(64))

    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        Index("idx_doc_kb", "kb_id"),
        Index("idx_doc_root", "root_id"),
        Index("idx_doc_superseded_by", "superseded_by"),
        Index("idx_doc_hash", "kb_id", "content_hash"),
        Index("idx_doc_conflict", "has_conflict", postgresql_where="has_conflict = true"),
        Index("idx_doc_status", "kb_id", "parse_status"),
    )

    def __repr__(self) -> str:
        return f"<Document id={self.id} name={self.filename!r} v{self.version_no}>"


class DocumentChunk(Base):
    """文档切片（含向量与冗余元数据）。"""

    __tablename__ = "document_chunk"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    doc_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("document.id", ondelete="CASCADE"), nullable=False
    )
    kb_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    content: Mapped[str] = mapped_column(Text, nullable=False)
    # 标题路径，如「第三章 > 3.2 报销标准」——拼入检索上下文可显著提升命中率
    heading_path: Mapped[str | None] = mapped_column(String(512))

    # ⚠️ 记录生成该向量的模型，用于识别需要重建的切片
    embedding_model: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(settings.embedding_dim))

    # ⚠️ 关键词检索路的全文索引列。
    #    写入方式：应用层分词 → 空格分隔的 token 串 → to_tsvector('simple', ...)
    #    为什么不用 PG 原生中文分词：pgvector 官方镜像不含 pg_bigm，
    #    而内置 tsvector 没有中文分词器（整句会被当成一个 token，关键词检索等于失效）。
    search_text: Mapped[str | None] = mapped_column(TSVECTOR)

    # ⚠️ 文档级标签的冗余副本 —— 检索时单表过滤，避免 JOIN
    #    用数组而非 JSONB：标签过滤变成 `tags && ARRAY['行政','预算']`，
    #    比 JSONB 的 `@>` 语义更直观，索引也更小。
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), nullable=False, default=list, server_default=text("'{}'")
    )
    # ⚠️ 权限下推缓存
    is_retrievable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # 下架原因：version(被新版取代) | conflict(自身冲突隔离) | overwrite(被覆盖)
    # ⚠️ 必须区分原因 —— 否则「删除新版后复活旧版」时，会把自身冲突隔离的切片一并复活。
    retired_reason: Mapped[str | None] = mapped_column(String(16))
    # ⚠️ 取代者文档 id（**切片级**）：「覆盖」冲突时，被取代的旧切片要记住被谁取代，
    #    删除那个新文档时才能精确恢复它 —— 否则被覆盖的旧内容永久失联（2026-10-09 缺陷）。
    superseded_by: Mapped[int | None] = mapped_column(BigInteger)

    # ⚠️ 父块上下文（**parent-child 检索**）：小块命中后，返回它所属的「父块」文本
    #    （同章节 heading_path 下的多条切片聚合），让 LLM 拿到更完整的上下文，
    #    避免「召回一句、答案片面」。父块**不另占向量索引**，仅作为返回上下文；
    #    NULL 表示无更大上下文（如该章节只有单片），检索时退化成返回小块本身。
    parent_content: Mapped[str | None] = mapped_column(Text)

    document: Mapped[Document] = relationship(back_populates="chunks")

    __table_args__ = (
        Index("idx_chunk_doc", "doc_id"),
        Index("idx_chunk_kb", "kb_id"),
        Index("idx_chunk_tags", "tags", postgresql_using="gin"),
        Index(
            "idx_chunk_vec",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with={"m": 16, "ef_construction": 64},
        ),
        Index("idx_chunk_retrievable", "kb_id", "is_retrievable"),
        # 关键词检索路的 GIN 索引
        Index("idx_chunk_fts", "search_text", postgresql_using="gin"),
    )

    def __repr__(self) -> str:
        return f"<DocumentChunk id={self.id} doc_id={self.doc_id} idx={self.chunk_index}>"
