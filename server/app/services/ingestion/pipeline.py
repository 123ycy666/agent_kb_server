"""文档入库流水线。

流程
----
上传 → 计算 hash → 判重 / 版本判定 → 落盘 → 解析 → 切片 → 向量化 → 入库

⚠️ 两个关键点
1. **版本判定**（对应设计方案 11.1）
   - 同文件名 + 同 hash  → 重复，拒绝上传
   - 同文件名 + 不同 hash → 版本升级，旧版 is_active=False
   - 不同文件名 + 内容冲突 → 交给冲突检测流程（不自动覆盖）
2. **embedding_model 落库**
   每条切片记录生成它的模型。切换 embedding 模型后，
   旧切片因模型不一致会被排除在检索之外，并提示重建 ——
   否则会出现「检索结果莫名其妙全错」的隐蔽 bug。
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import CurrentActor
from app.core.text import to_search_text
from app.models import Document, DocumentChunk, KnowledgeBase
from app.providers import get_providers
from app.services.ingestion import parsers
from app.services.ingestion.chunker import chunk_document, chunk_summary
from app.services.tag_governance import normalize_tags, sync_tag_vocabulary

logger = logging.getLogger(__name__)

# ── 文档类型判定（2026-10-30）────────────────────────────────
# rule = 规则/口径：每轮注入给模型（不进向量检索）
# doc  = 资料：走检索，命中后标出处
# 判定按**文件名**做，前端不用多传字段（少一个前后端约定就少一处不一致）。
#
# ⚠️ 只认"规则/口径/规范/标准/要求"这几类**结构性**文档。
#    不要把「制度/流程/办法」也算进来：实测《财务报销管理制度.pdf》被判成 rule 之后，
#    它按"第 1 页 / 第 2 页"切片，规则目录里冒出「第 1 页 表格」这种主题（线上 2026-10-30）。
#    长制度原文属于**资料**，该走检索。
_RULE_NAME_HINTS = ('规则', '口径', '规范', '标准', '要求')


def _doc_type_of(filename: str) -> str:
    name = str(filename or '')
    return 'rule' if any(h in name for h in _RULE_NAME_HINTS) else 'doc'


def _safe_unlink(path: Path) -> None:
    """尽力删除落盘文件，**绝不抛异常**。

    ⚠️ 清理动作绝不能掩盖原始错误 —— 这是清理代码的铁律。

    实测在受限环境下 `unlink` 会抛 OSError（例如回收站不可用时被
    safe-delete 机制拦截）。若不保护，用户看到的错误会变成「删除失败」，
    而真正的原因（解析失败 / 向量化失败）被完全掩盖，排查方向被带偏。
    """
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:  # noqa: BLE001
        logger.warning("清理落盘文件失败（不影响主流程）：%s（%s）", path, exc)


class IngestError(RuntimeError):
    """入库失败。"""


class DuplicateDocumentError(IngestError):
    """内容完全相同的文档已存在。"""

    def __init__(self, message: str, existing_doc_id: int):
        super().__init__(message)
        self.existing_doc_id = existing_doc_id


@dataclass
class IngestResult:
    doc_id: int
    filename: str
    version_no: int
    is_new_version: bool
    chunk_count: int
    chunk_stats: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    parse_meta: dict = field(default_factory=dict)


def compute_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _safe_filename(filename: str) -> str:
    """防止路径穿越。"""
    name = Path(filename).name
    name = name.replace("\\", "_").replace("/", "_")
    return name or "unnamed"


def save_upload(content: bytes, kb_id: int, filename: str) -> Path:
    """把上传内容落盘，返回存储路径。"""
    safe = _safe_filename(filename)
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    target_dir = settings.upload_path / f"kb_{kb_id}"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{stamp}_{safe}"
    target.write_bytes(content)
    return target


def _detect_version(db: Session, kb_id: int, filename: str, content_hash: str) -> dict:
    """版本判定。"""
    # 同 hash 的活跃文档 → 重复
    same_hash = db.execute(
        select(Document).where(
            Document.kb_id == kb_id,
            Document.content_hash == content_hash,
            Document.is_active.is_(True),
        )
    ).scalar_one_or_none()

    if same_hash is not None:
        raise DuplicateDocumentError(
            f"该文件内容与已有文档《{same_hash.filename}》完全相同（SHA256 一致），"
            "无需重复上传。",
            existing_doc_id=same_hash.id,
        )

    # 同文件名的最新活跃版本
    same_name = db.execute(
        select(Document)
        .where(
            Document.kb_id == kb_id,
            Document.filename == filename,
            Document.is_active.is_(True),
        )
        .order_by(Document.version_no.desc())
    ).scalars().first()

    if same_name is not None:
        return {
            "is_new_version": True,
            "version_no": same_name.version_no + 1,
            "root_id": same_name.root_id or same_name.id,
            "supersede": same_name,
        }

    return {"is_new_version": False, "version_no": 1, "root_id": None, "supersede": None}


def ingest_document(
    db: Session,
    *,
    kb: KnowledgeBase,
    filename: str,
    content: bytes,
    actor: CurrentActor,
    is_public: bool = False,
    tags: list[str] | None = None,
    storage_path: str | None = None,
) -> IngestResult:
    """执行完整入库流程。

    参数
    ----
    is_public : bool
        是否公开。公开 = 所有人可见、可检索；
        否则仅上传者本人（管理员另在管理视图可见）。
    tags : list[str] | None
        自由标签，如「行政」「预算」。自动规范化、去重、最多 10 个。
    """
    safe_name = _safe_filename(filename)

    if not parsers.is_supported(safe_name):
        raise IngestError(
            f"不支持的文件格式：{Path(safe_name).suffix or '(无扩展名)'}。"
            f"支持：{', '.join(sorted(parsers.SUPPORTED_EXTENSIONS))}"
        )

    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise IngestError(f"文件超过大小上限（{settings.max_upload_mb} MB）")

    content_hash = compute_hash(content)
    version_info = _detect_version(db, kb.id, safe_name, content_hash)
    # 标签：规范化 + 去重（非法标签跳过并记警告，不阻断上传）
    tags_list, warnings = normalize_tags(tags)

    # ── 落盘 ─────────────────────────────────────────────
    # ⚠️ storage_path 由调用方传入时（如 Celery 任务已先行落盘）直接复用，
    #    不再重复写入，避免同一文件落两次盘。
    if storage_path is None:
        storage_path = save_upload(content, kb.id, safe_name)
    else:
        storage_path = Path(storage_path)

    # ── 解析 ─────────────────────────────────────────────
    try:
        parsed = parsers.parse_file(storage_path)
    except parsers.ParserError as exc:
        _safe_unlink(storage_path)
        raise IngestError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        _safe_unlink(storage_path)
        raise IngestError(f"文档解析异常：{exc}") from exc

    if parsed.is_empty:
        _safe_unlink(storage_path)
        raise IngestError("文档解析后内容为空，未入库")

    if parsed.meta.get("scan_suspect"):
        warnings.append(
            "该 PDF 每页文字量极少，可能是扫描件。本期不支持 OCR，"
            "请确认解析结果是否符合预期。"
        )

    # ── 切片 ─────────────────────────────────────────────
    drafts = chunk_document(
        parsed, chunk_size=kb.chunk_size, chunk_overlap=kb.chunk_overlap
    )
    if not drafts:
        _safe_unlink(storage_path)
        raise IngestError("切片结果为空，未入库")

    # ── 向量化 ───────────────────────────────────────────
    bundle = get_providers()
    embedding_model_name = f"{bundle.mode}/{bundle.embedding.model_id}"

    if kb.embedding_model and kb.embedding_model != embedding_model_name:
        warnings.append(
            f"该知识库绑定的向量模型为 {kb.embedding_model}，"
            f"当前档位的模型为 {embedding_model_name}。"
            "两者向量空间不同，检索时将无法命中新入库内容。"
            "建议统一模型后重建索引。"
        )

    texts = [d.content for d in drafts]
    try:
        vectors = bundle.embedding.embed_batch(texts)
    except Exception as exc:  # noqa: BLE001
        _safe_unlink(storage_path)
        raise IngestError(f"向量化失败：{exc}") from exc

    # ── 落库 ─────────────────────────────────────────────
    if version_info["is_new_version"]:
        superseded: Document = version_info["supersede"]
        superseded.is_active = False
        # 旧版切片一并下架，确保检索只命中最新版。
        # ⚠️ 必须打 retired_reason 标记：区分「被新版取代」与「被冲突隔离」，
        #    删除新版复活旧版时才不会把冲突隔离的切片一并复活。
        db.query(DocumentChunk).filter(DocumentChunk.doc_id == superseded.id).update(
            {
                DocumentChunk.is_retrievable: False,
                DocumentChunk.retired_reason: "version",
            },
            synchronize_session=False,
        )

    doc = Document(
        kb_id=kb.id,
        root_id=version_info["root_id"],
        version_no=version_info["version_no"],
        is_active=True,
        filename=safe_name,
        file_ext=Path(safe_name).suffix.lower(),
        file_size=len(content),
        storage_path=str(storage_path),
        content_hash=content_hash,
        parse_status="ready",
        has_conflict=False,
        chunk_count=len(drafts),
        # ⚠️ 不显式 public 一律 private —— 不存在「默认公开」
        visibility="public" if is_public else "private",
        # 2026-10-30：按文件名判"规则 / 资料"，决定后续是注入还是检索（前端不用多传字段）
        doc_type=_doc_type_of(safe_name),
        tags=tags_list,
        owner_id=actor.user_id,
        uploaded_by_app=None,
    )
    db.add(doc)
    db.flush()  # 取得 doc.id

    if doc.root_id is None:
        doc.root_id = doc.id

    # ⚠️ 必须等 flush 拿到新文档 id 后才能打标：
    #    记录「旧版被谁取代」，删除新版时才能把旧版回滚复活。
    #    缺了这一步，新版一旦被删除，旧版就成了永久不可检索的孤儿（2026-10-08 缺陷）。
    if version_info["is_new_version"]:
        superseded.superseded_by = doc.id
        superseded.retired_reason = "version_supersede"

    # 切片
    for draft, vec in zip(drafts, vectors, strict=False):
        db.add(
            DocumentChunk(
                doc_id=doc.id,
                kb_id=kb.id,
                chunk_index=draft.index,
                content=draft.content,
                heading_path=draft.heading_path,
                parent_content=draft.parent_content,
                embedding_model=embedding_model_name,
                embedding=vec,
                tags=tags_list,
                is_retrievable=True,
                # ⚠️ 关键词检索路的全文索引。
                #    应用层分词后交给 to_tsvector('simple') ——
                #    不用 PG 原生中文分词（pgvector 镜像不含 pg_bigm，
                #    内置 tsvector 无中文分词器，整句会被当成一个 token）。
                search_text=func.to_tsvector("simple", to_search_text(draft.content)),
            )
        )

    # 标签词表同步（与文档同一事务提交，避免词表被写脏）
    sync_tag_vocabulary(db, tags_list, created_by=actor.user_id)

    db.commit()
    db.refresh(doc)

    return IngestResult(
        doc_id=doc.id,
        filename=safe_name,
        version_no=doc.version_no,
        is_new_version=version_info["is_new_version"],
        chunk_count=len(drafts),
        chunk_stats=chunk_summary(drafts),
        warnings=warnings,
        parse_meta=parsed.meta,
    )


# ══════════════════════════════════════════════════════════════
#  重建索引
# ══════════════════════════════════════════════════════════════


@dataclass
class ReindexResult:
    """重建索引结果。"""

    doc_id: int | None
    kb_id: int | None
    document_count: int
    chunk_count: int
    from_model: str
    to_model: str
    duration_ms: int


def reindex_document(db: Session, *, doc_id: int) -> ReindexResult:
    """用**当前档位**的 embedding 模型重建该文档的全部切片向量。

    为什么必须提供这个能力
    ----------------------
    不同 embedding 模型（即便维度相同）的向量位于**不同的向量空间**。
    切换模型后，旧向量与新的查询向量无法比较，检索会**静默失效**
    （不报错，只是永远召回不到）。

    因此切换模型后必须重建；本函数同时重建 `search_text`
    （分词规则若有调整，索引也需同步刷新）。
    """
    import time as _time

    doc = db.get(Document, doc_id)
    if doc is None:
        raise IngestError(f"文档不存在：{doc_id}")

    chunks = (
        db.execute(
            select(DocumentChunk)
            .where(DocumentChunk.doc_id == doc_id)
            .order_by(DocumentChunk.chunk_index)
        )
        .scalars()
        .all()
    )
    if not chunks:
        raise IngestError("该文档没有切片，无法重建索引")

    bundle = get_providers()
    model_name = f"{bundle.mode}/{bundle.embedding.model_id}"
    old_model = chunks[0].embedding_model

    started = _time.perf_counter()
    try:
        vectors = bundle.embedding.embed_batch([c.content for c in chunks])
    except Exception as exc:  # noqa: BLE001
        raise IngestError(f"重建索引时向量化失败：{exc}") from exc

    for chunk, vec in zip(chunks, vectors, strict=False):
        chunk.embedding = vec
        chunk.embedding_model = model_name
        chunk.search_text = func.to_tsvector("simple", to_search_text(chunk.content))

    db.commit()
    duration = int((_time.perf_counter() - started) * 1000)

    return ReindexResult(
        doc_id=doc_id,
        kb_id=doc.kb_id,
        document_count=1,
        chunk_count=len(chunks),
        from_model=old_model,
        to_model=model_name,
        duration_ms=duration,
    )


def reindex_knowledge_base(db: Session, *, kb_id: int, only_stale: bool = True) -> ReindexResult:
    """重建整个知识库的索引。

    Parameters
    ----------
    only_stale
        为 True 时只重建「模型与当前档位不一致」的文档，
        避免动辄全量重建（大库重建很耗时）。默认 True。
    """
    import time as _time

    kb = db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise IngestError(f"知识库不存在：{kb_id}")

    bundle = get_providers()
    model_name = f"{bundle.mode}/{bundle.embedding.model_id}"

    stmt = select(Document.id).where(Document.kb_id == kb_id, Document.is_active.is_(True))
    if only_stale:
        stale_subq = (
            select(DocumentChunk.doc_id)
            .where(DocumentChunk.embedding_model != model_name)
            .distinct()
        )
        stmt = stmt.where(Document.id.in_(stale_subq))

    doc_ids = list(db.execute(stmt).scalars())
    if not doc_ids:
        return ReindexResult(
            doc_id=None,
            kb_id=kb_id,
            document_count=0,
            chunk_count=0,
            from_model=model_name,
            to_model=model_name,
            duration_ms=0,
        )

    started = _time.perf_counter()
    total_chunks = 0
    from_model = model_name
    for did in doc_ids:
        res = reindex_document(db, doc_id=did)
        total_chunks += res.chunk_count
        from_model = res.from_model

    return ReindexResult(
        doc_id=None,
        kb_id=kb_id,
        document_count=len(doc_ids),
        chunk_count=total_chunks,
        from_model=from_model,
        to_model=model_name,
        duration_ms=int((_time.perf_counter() - started) * 1000),
    )
