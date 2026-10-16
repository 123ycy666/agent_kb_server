"""知识库接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import CurrentActor, get_current_actor, require_permission
from app.schemas.common import OkResponse, Page
from app.schemas.kb import (
    EmbeddingModelChangeRequest,
    EmbeddingModelChangeResponse,
    KnowledgeBaseCreate,
    KnowledgeBaseOut,
    KnowledgeBaseUpdate,
    ReindexResponse,
)
from app.services import audit, knowledge_base as kb_service
from app.services.ingestion import IngestError, reindex_knowledge_base

router = APIRouter(prefix="/kb", tags=["知识库"])


@router.post(
    "",
    response_model=KnowledgeBaseOut,
    summary="创建知识库",
    dependencies=[Depends(require_permission("kb:create"))],
)
def create_kb(
    payload: KnowledgeBaseCreate,
    request: Request,
    actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    kb = kb_service.create_knowledge_base(db, actor=actor, payload=payload)
    audit.log_action(
        db,
        actor=actor,
        action=audit.AuditAction.KB_CREATE,
        resource_type="kb",
        resource_id=kb.id,
        resource_name=kb.name,
        detail={"embedding_model": kb.embedding_model, "chunk_size": kb.chunk_size},
        request=request,
        commit=True,
    )
    return KnowledgeBaseOut.model_validate(kb)


@router.get("", response_model=Page[KnowledgeBaseOut], summary="知识库列表")
def list_kb(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    items, total = kb_service.list_knowledge_bases(
        db, actor=actor, page=page, page_size=page_size
    )
    return Page[KnowledgeBaseOut](
        items=[KnowledgeBaseOut(**i) for i in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{kb_id}", response_model=KnowledgeBaseOut, summary="知识库详情")
def get_kb(
    kb_id: int,
    actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    kb = kb_service.get_knowledge_base(db, kb_id)
    if kb is None:
        raise HTTPException(
            status_code=404, detail={"code": "KB_NOT_FOUND", "message": "知识库不存在"}
        )

    stats = kb_service.kb_stats(db, kb=kb, actor=actor)
    out = KnowledgeBaseOut.model_validate(kb)
    out.document_count = stats["document_count"]
    out.chunk_count = stats["chunk_count"]
    out.visible_document_count = stats["visible_document_count"]
    return out


@router.patch(
    "/{kb_id}",
    response_model=KnowledgeBaseOut,
    summary="更新知识库",
    dependencies=[Depends(require_permission("kb:update"))],
)
def update_kb(
    kb_id: int,
    payload: KnowledgeBaseUpdate,
    request: Request,
    actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    kb = kb_service.get_knowledge_base(db, kb_id)
    if kb is None:
        raise HTTPException(
            status_code=404, detail={"code": "KB_NOT_FOUND", "message": "知识库不存在"}
        )

    old_chunk = (kb.chunk_size, kb.chunk_overlap)
    kb = kb_service.update_knowledge_base(db, kb=kb, payload=payload)

    audit.log_action(
        db,
        actor=actor,
        action=audit.AuditAction.KB_UPDATE,
        resource_type="kb",
        resource_id=kb.id,
        resource_name=kb.name,
        detail={"changes": payload.model_dump(exclude_unset=True)},
        request=request,
        commit=True,
    )

    out = KnowledgeBaseOut.model_validate(kb)
    if (kb.chunk_size, kb.chunk_overlap) != old_chunk:
        # 切片参数变更需提醒：已入库切片不会自动重切
        out.description = (out.description or "") + (
            "\n⚠️ 切片参数已变更，已入库的文档不会自动重新切片，"
            "如需生效请对相关文档执行重建索引。"
        )
    return out


@router.delete(
    "/{kb_id}",
    response_model=OkResponse,
    summary="删除知识库（级联删除文档与切片）",
    dependencies=[Depends(require_permission("kb:delete"))],
)
def delete_kb(
    kb_id: int,
    request: Request,
    actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    kb = kb_service.get_knowledge_base(db, kb_id)
    if kb is None:
        raise HTTPException(
            status_code=404, detail={"code": "KB_NOT_FOUND", "message": "知识库不存在"}
        )

    name = kb.name
    result = kb_service.delete_knowledge_base(db, kb=kb)
    audit.log_action(
        db,
        actor=actor,
        action=audit.AuditAction.KB_DELETE,
        resource_type="kb",
        resource_id=kb_id,
        resource_name=name,
        detail=result,
        request=request,
        commit=True,
    )
    return OkResponse(message=f"知识库《{name}》已删除，同时移除 {result['deleted_documents']} 份文档")


@router.get("/{kb_id}/stats", summary="知识库统计")
def kb_stats(
    kb_id: int,
    actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    kb = kb_service.get_knowledge_base(db, kb_id)
    if kb is None:
        raise HTTPException(
            status_code=404, detail={"code": "KB_NOT_FOUND", "message": "知识库不存在"}
        )
    return kb_service.kb_stats(db, kb=kb, actor=actor)


@router.post(
    "/{kb_id}/embedding-model",
    response_model=EmbeddingModelChangeResponse,
    summary="切换向量模型（需重建索引）",
    dependencies=[Depends(require_permission("kb:update"))],
)
def change_embedding_model(
    kb_id: int,
    payload: EmbeddingModelChangeRequest,
    request: Request,
    actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    kb = kb_service.get_knowledge_base(db, kb_id)
    if kb is None:
        raise HTTPException(
            status_code=404, detail={"code": "KB_NOT_FOUND", "message": "知识库不存在"}
        )

    result = kb_service.change_embedding_model(
        db,
        kb=kb,
        new_model=payload.embedding_model,
        new_dim=payload.embedding_dim,
        rebuild=payload.rebuild,
    )

    if result["changed"]:
        audit.log_action(
            db,
            actor=actor,
            action=audit.AuditAction.INDEX_REBUILD,
            resource_type="kb",
            resource_id=kb_id,
            resource_name=kb.name,
            detail=result,
            request=request,
            commit=True,
        )

    return EmbeddingModelChangeResponse(**result)


@router.post(
    "/{kb_id}/reindex",
    response_model=ReindexResponse,
    summary="重建知识库索引（切换向量模型后必做）",
    dependencies=[Depends(require_permission("kb:update"))],
)
def reindex_kb(
    kb_id: int,
    request: Request,
    only_stale: bool = Query(
        True, description="仅重建「向量模型与当前档位不一致」的文档，避免全量重算"
    ),
    actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    """重新向量化知识库内的切片。

    ⚠️ 为什么必须提供这个接口

    不同 embedding 模型（即使维度相同）的向量处于**不同的向量空间**，
    彼此不可比较。切换模型后若不重建，检索会**静默失效** ——
    不报错、不告警，只是永远召回不到内容。这是最难排查的一类故障。
    """
    kb = kb_service.get_knowledge_base(db, kb_id)
    if kb is None:
        raise HTTPException(
            status_code=404, detail={"code": "KB_NOT_FOUND", "message": "知识库不存在"}
        )

    try:
        res = reindex_knowledge_base(db, kb_id=kb_id, only_stale=only_stale)
    except IngestError as exc:
        raise HTTPException(
            status_code=400, detail={"code": "REINDEX_FAILED", "message": str(exc)}
        ) from exc

    audit.log_action(
        db,
        actor=actor,
        action=audit.AuditAction.INDEX_REBUILD,
        resource_type="kb",
        resource_id=kb_id,
        resource_name=kb.name,
        detail={
            "only_stale": only_stale,
            "document_count": res.document_count,
            "chunk_count": res.chunk_count,
            "from_model": res.from_model,
            "to_model": res.to_model,
            "duration_ms": res.duration_ms,
        },
        request=request,
        commit=True,
    )

    msg = (
        f"已重建 {res.document_count} 份文档、{res.chunk_count} 条切片"
        f"（{res.from_model} → {res.to_model}，耗时 {res.duration_ms} ms）"
        if res.document_count
        else "所有文档的向量模型均与当前档位一致，无需重建"
    )
    return ReindexResponse(
        kb_id=kb_id,
        document_count=res.document_count,
        chunk_count=res.chunk_count,
        from_model=res.from_model,
        to_model=res.to_model,
        duration_ms=res.duration_ms,
        message=msg,
    )
