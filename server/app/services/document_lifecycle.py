"""文档下架 / 复活生命周期（孤儿版本自愈）。

背景（2026-10-08 定位的缺陷）
------------------------------
下架旧版原先是**单向**操作：只置 is_active=False + 旧 chunk is_retrievable=False，
系统并不记录「谁取代了我」。于是取代者一旦被删除，被它取代的旧版就变成
**永久不可检索且无恢复入口**的孤儿：

    上传 v2（v1 下架）→ 删除 v2 → v1 永远搜不到

本模块通过 `superseded_by` 反查实现删除后自愈：
删除文档时，把「直接被它取代、且其版本链上已无其他活跃文档」的下架文档复活。

⚠️ 两个必须守住的判定
1. **版本链已有活跃兄弟 → 不复活**。
   例：v4 活跃、v3 被删，此时 v2（被 v3 取代）虽也是 v3 的继任对象，
   但它只是历史版本，保持下架才对，否则会冒出两个活跃版本。
2. **复活只做一层，不递归**。
   v3 删 → 复活 v2 即可；v1 是 v2 的历史，继续下架是正确的。
   递归会把整条历史链全部复活，反而破坏「仅最新版可检索」的约定。
"""

from __future__ import annotations

import logging

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Document, DocumentChunk
from app.providers import get_providers

logger = logging.getLogger(__name__)

# 单文档下最多触发多少个复活（防御异常数据导致的链式回滚）
MAX_RESTORE_PER_DELETE = 10


def _chain_has_active_sibling(
    db: Session, doc: Document, exclude_ids: set[int]
) -> bool:
    """该文档所属版本链上是否还存在活跃文档。"""
    root = doc.root_id or doc.id
    stmt = select(func.count(Document.id)).where(
        Document.kb_id == doc.kb_id,
        or_(Document.root_id == root, Document.id == root),
        Document.is_active.is_(True),
    )
    if exclude_ids:
        stmt = stmt.where(Document.id.notin_(exclude_ids))
    return (db.execute(stmt).scalar_one() or 0) > 0


def _stale_chunk_count(db: Session, doc_id: int, current_model: str) -> int:
    """向量模型与当前档位不一致的切片数（这些切片复活后仍检索不到）。"""
    return int(
        db.execute(
            select(func.count(DocumentChunk.id)).where(
                DocumentChunk.doc_id == doc_id,
                DocumentChunk.embedding_model != current_model,
            )
        ).scalar_one()
        or 0
    )


def stale_chunk_count(db: Session, doc_id: int) -> int:
    """公开入口：供 restore 端点提示「复活了但仍搜不到」的风险。"""
    bundle = get_providers()
    return _stale_chunk_count(db, doc_id, f"{bundle.mode}/{bundle.embedding.model_id}")


def rollback_superseded(
    db: Session,
    *,
    deleted_doc_id: int,
    kb_id: int | None = None,
) -> list[dict]:
    """删除文档后，回滚复活被它取代的下架文档。

    Returns
    -------
    list[dict]
        每项含 ``doc_id / filename / stale_chunks``。
        ``stale_chunks > 0`` 表示该文档向量模型已过期，复活后仍搜不到，
        调用方**必须**把它作为 warning 提示给用户，否则就是「以为修好了」。
    """
    # ⚠️ 切片级恢复：把被删除文档「覆盖」过的旧切片放回检索。
    #    文档级 superseded_by 只在「整篇被取代」时才打；**部分覆盖**（只取代几片）
    #    仅有切片级 superseded_by —— 漏了这一步，被覆盖的旧内容会永久失联
    #    （2026-10-09 用户复现的缺陷）。
    restored_chunks = int(
        db.query(DocumentChunk)
        .filter(DocumentChunk.superseded_by == deleted_doc_id)
        .update(
            {
                DocumentChunk.is_retrievable: True,
                DocumentChunk.retired_reason: None,
                DocumentChunk.superseded_by: None,
            },
            synchronize_session=False,
        )
    )
    if restored_chunks:
        logger.info("按切片级取代关系恢复了 %d 个切片", restored_chunks)

    bundle = get_providers()
    current_model = f"{bundle.mode}/{bundle.embedding.model_id}"

    candidates = (
        db.execute(
            select(Document)
            .where(
                Document.superseded_by == deleted_doc_id,
                Document.is_active.is_(False),
            )
            .order_by(Document.version_no.desc(), Document.id.desc())
            .limit(MAX_RESTORE_PER_DELETE)
        )
        .scalars()
        .all()
    )

    restored: list[dict] = []
    for doc in candidates:
        if kb_id is not None and doc.kb_id != kb_id:
            continue
        # 判定 1：链上已有活跃兄弟 → 它只是历史版本，保持下架
        if _chain_has_active_sibling(db, doc, exclude_ids={deleted_doc_id}):
            logger.info(
                "文档 #%s 的版本链仍有活跃版本，#%s 保持下架", deleted_doc_id, doc.id
            )
            continue

        doc.is_active = True
        doc.superseded_by = None
        doc.retired_reason = None
        # ⚠️ 只复活「被版本取代」的切片（含历史遗留的 retired_reason 为空）。
        #    若一并复活 retired_reason='conflict' 的切片，会把冲突隔离的内容
        #    重新放回检索 —— 隔离就白做了。
        db.query(DocumentChunk).filter(
            DocumentChunk.doc_id == doc.id,
            or_(
                DocumentChunk.retired_reason.is_(None),
                DocumentChunk.retired_reason == "version",
            ),
        ).update(
            {DocumentChunk.is_retrievable: True, DocumentChunk.retired_reason: None},
            synchronize_session=False,
        )
        stale = _stale_chunk_count(db, doc.id, current_model)
        logger.info(
            "已回滚复活文档 #%s v%s %s（%s，%d 个切片失效待重建）",
            doc.id, doc.version_no, doc.filename, current_model, stale,
        )
        restored.append(
            {
                "doc_id": doc.id,
                "filename": doc.filename,
                "version_no": doc.version_no,
                "stale_chunks": stale,
            }
        )

    return restored