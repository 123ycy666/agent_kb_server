"""知识库业务逻辑。"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import CurrentActor
from app.models import Document, DocumentChunk, KnowledgeBase
from app.providers import current_embedding_model_name, get_providers
from app.schemas.kb import KnowledgeBaseCreate, KnowledgeBaseUpdate
from app.services.permission import count_visible_documents


logger = logging.getLogger(__name__)


class KnowledgeBaseError(RuntimeError):
    """知识库操作失败。"""


def create_knowledge_base(
    db: Session, *, actor: CurrentActor, payload: KnowledgeBaseCreate
) -> KnowledgeBase:
    """创建知识库。

    embedding_model 与 embedding_dim 在此**绑定**：
    这是后续判断「是否需要重建索引」的依据。
    """
    bundle = get_providers()
    model_name = payload.embedding_model or current_embedding_model_name()

    kb = KnowledgeBase(
        name=payload.name.strip(),
        description=payload.description,
        embedding_model=model_name,
        embedding_dim=bundle.embedding.dim,
        chunk_size=payload.chunk_size,
        chunk_overlap=payload.chunk_overlap,
        status="active",
        created_by=actor.user_id,
    )
    db.add(kb)
    db.commit()
    db.refresh(kb)
    return kb


def get_knowledge_base(db: Session, kb_id: int) -> KnowledgeBase | None:
    return db.get(KnowledgeBase, kb_id)


def list_knowledge_bases(
    db: Session, *, actor: CurrentActor, page: int = 1, page_size: int = 20
) -> tuple[list[dict], int]:
    """知识库列表（带统计）。

    ⚠️ 额外返回 visible_document_count：
       本期不做知识库级入口权限，用户可能进库后一篇文档都看不到，
       前端必须能区分「空库」与「无权限」，否则会误报系统故障。
    """
    total = int(db.execute(select(func.count(KnowledgeBase.id))).scalar_one())

    rows = (
        db.execute(
            select(KnowledgeBase)
            .order_by(KnowledgeBase.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )

    items: list[dict] = []
    for kb in rows:
        doc_count = int(
            db.execute(
                select(func.count(Document.id)).where(
                    Document.kb_id == kb.id, Document.is_active.is_(True)
                )
            ).scalar_one()
        )
        chunk_count = int(
            db.execute(
                select(func.count(DocumentChunk.id)).where(DocumentChunk.kb_id == kb.id)
            ).scalar_one()
        )
        items.append(
            {
                "id": kb.id,
                "name": kb.name,
                "description": kb.description,
                "embedding_model": kb.embedding_model,
                "embedding_dim": kb.embedding_dim,
                "chunk_size": kb.chunk_size,
                "chunk_overlap": kb.chunk_overlap,
                "status": kb.status,
                # ⚠️ 这里是手工拼装 dict，新增字段必须同步加进来，
                #    否则 Pydantic 会用 schema 默认值 —— 表现为「字段永远不变」。
                "qa_enabled": kb.qa_enabled,
                "created_at": kb.created_at,
                "updated_at": kb.updated_at,
                "document_count": doc_count,
                "chunk_count": chunk_count,
                "visible_document_count": count_visible_documents(db, actor, kb.id),
            }
        )

    return items, total


def update_knowledge_base(
    db: Session, *, kb: KnowledgeBase, payload: KnowledgeBaseUpdate
) -> KnowledgeBase:
    data = payload.model_dump(exclude_unset=True)

    # 切片参数变更会导致已有切片与新参数不一致，给出提示但不阻断
    if data.get("chunk_size") and data["chunk_size"] != kb.chunk_size:
        pass  # 由接口层返回提醒

    for key, value in data.items():
        if value is not None:
            setattr(kb, key, value)

    db.commit()
    db.refresh(kb)
    return kb


def delete_knowledge_base(db: Session, *, kb: KnowledgeBase) -> dict:
    """删除知识库（级联删除文档、切片、标签、冲突记录），并清理磁盘文件。

    ⚠️ 数据库的 ON DELETE CASCADE 只删**记录**，不会碰磁盘文件。
       若只删记录，上传目录会持续堆积孤儿文件 ——
       而用户以为数据已彻底删除。在合规场景下这是严重问题：
       「已删除」的机密文件仍然躺在磁盘上。
    """
    rows = list(
        db.execute(
            select(Document.id, Document.storage_path).where(Document.kb_id == kb.id)
        ).all()
    )
    doc_count = len(rows)
    paths = [Path(p) for _, p in rows if p]

    kb_id = kb.id
    db.delete(kb)
    db.commit()

    # 记录已删除后再清理文件；文件删不掉不影响业务结果，只记警告
    removed = 0
    for path in paths:
        try:
            path.unlink(missing_ok=True)
            removed += 1
        except OSError as exc:  # noqa: BLE001
            logger.warning("删除知识库时清理文件失败：%s（%s）", path, exc)

    return {
        "deleted_kb_id": kb_id,
        "deleted_documents": doc_count,
        "deleted_files": removed,
    }


def change_embedding_model(
    db: Session, *, kb: KnowledgeBase, new_model: str, new_dim: int, rebuild: bool
) -> dict:
    """切换知识库绑定的 embedding 模型。

    ⚠️ 核心风险：不同模型的向量**即使维度相同也不可混用**。
       因此切换时必须：
       1. 维度不同 → 直接拒绝（无法在不重建的前提下继续）
       2. 模型不同但维度相同 → 强制提示需重建，并要求显式确认
    """
    from app.models import DocumentChunk as Chunk

    same_model = kb.embedding_model == new_model
    same_dim = kb.embedding_dim == new_dim

    if same_model and same_dim:
        return {
            "changed": False,
            "need_rebuild": False,
            "affected_chunks": 0,
            "message": "模型与维度均未变化，无需操作",
        }

    affected = int(
        db.execute(
            select(func.count(Chunk.id)).where(Chunk.kb_id == kb.id)
        ).scalar_one()
    )

    if not same_dim:
        return {
            "changed": False,
            "need_rebuild": True,
            "affected_chunks": affected,
            "message": (
                f"维度不一致（{kb.embedding_dim} → {new_dim}），"
                "无法直接切换。必须先重建索引。"
                "请调用重建接口，或新建知识库后重新导入文档。"
            ),
        }

    if not rebuild:
        return {
            "changed": False,
            "need_rebuild": True,
            "affected_chunks": affected,
            "message": (
                f"⚠️ {kb.embedding_model} 与 {new_model} 虽然同为 {new_dim} 维，"
                "但属于**不同的向量空间，向量不可比较**。"
                f"该知识库现有 {affected} 条切片必须全部重新向量化，"
                "否则检索结果会静默出错。请确认后携带 rebuild=true 再次调用。"
            ),
        }

    # 确认重建：更新绑定，并**立即**用新模型重建全部向量
    kb.embedding_model = new_model
    kb.embedding_dim = new_dim
    db.commit()

    # ⚠️ 不要只把旧向量置空就收工。
    #    置空后该库会「什么都搜不到」，用户体感等同于系统故障。
    #    这里直接重建，保证切换动作结束时系统已恢复可用状态。
    from app.services.ingestion import reindex_knowledge_base

    try:
        res = reindex_knowledge_base(db, kb_id=kb.id, only_stale=False)
        rebuilt = (
            f"已重建 {res.document_count} 份文档、{res.chunk_count} 条切片"
            f"（耗时 {res.duration_ms} ms）。"
        )
    except Exception as exc:  # noqa: BLE001
        rebuilt = (
            f"⚠️ 自动重建失败：{exc}。请对相关文档调用重建接口重试 —— "
            "在重建完成前，该知识库的检索结果不可信。"
        )

    return {
        "changed": True,
        "need_rebuild": False,
        "affected_chunks": affected,
        "message": f"模型已切换为 {new_model}。{rebuilt}",
    }


def kb_stats(db: Session, *, kb: KnowledgeBase, actor: CurrentActor) -> dict:
    """知识库统计概览。"""
    total_docs = int(
        db.execute(
            select(func.count(Document.id)).where(
                Document.kb_id == kb.id, Document.is_active.is_(True)
            )
        ).scalar_one()
    )
    conflict_docs = int(
        db.execute(
            select(func.count(Document.id)).where(
                Document.kb_id == kb.id, Document.has_conflict.is_(True)
            )
        ).scalar_one()
    )
    by_status = {
        row[0]: row[1]
        for row in db.execute(
            select(Document.parse_status, func.count(Document.id))
            .where(Document.kb_id == kb.id)
            .group_by(Document.parse_status)
        ).all()
    }
    return {
        "kb_id": kb.id,
        "document_count": total_docs,
        "visible_document_count": count_visible_documents(db, actor, kb.id),
        "conflict_document_count": conflict_docs,
        "chunk_count": int(
            db.execute(
                select(func.count(DocumentChunk.id)).where(DocumentChunk.kb_id == kb.id)
            ).scalar_one()
        ),
        "document_status": by_status,
    }
