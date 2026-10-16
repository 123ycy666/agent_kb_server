"""冲突管理接口。

Web 上传 → 人工决策；API 上传 → 入库挂起 + 通知管理员。
两种场景的决策都收敛到这里。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import CurrentActor, get_current_actor, require_permission
from app.models import ConflictRecord, Document, DocumentChunk
from app.schemas.common import OkResponse, Page
from app.schemas.conflict import (
    ConflictDetectRequest,
    ConflictOut,
    ConflictResolveRequest,
    ConflictStats,
    FullScanRequest,
    FullScanResponse,
)
from app.services import audit
from app.services.conflict import detect_conflicts
from app.services.document_lifecycle import rollback_superseded
from app.services.permission import SCOPE_MANAGE, can_edit_document, visible_document_filter

router = APIRouter(prefix="/conflicts", tags=["冲突"])


def _unblock_conflict_chunks(db: Session, doc_id: int) -> int:
    """解除该文档中「因冲突被隔离」的切片（不含被新版取代的切片）。

    ⚠️ 只解 retired_reason == 'conflict' 的切片 —— 若把版本下架的切片一并解除，
    已被取代的旧内容会重新进入检索。
    """
    return int(
        db.query(DocumentChunk)
        .filter(
            DocumentChunk.doc_id == doc_id,
            DocumentChunk.retired_reason == "conflict",
        )
        .update(
            {
                DocumentChunk.is_retrievable: True,
                DocumentChunk.retired_reason: None,
            },
            synchronize_session=False,
        )
    )


def _visible_conflict_filter(actor: CurrentActor):
    """冲突列表只看「新文档对我可见」的记录。

    ⚠️ 漏掉这个过滤，普通用户就能看到他人私有文档的文件名与内容片段。
    """
    return ConflictRecord.new_doc_id.in_(
        select(Document.id).where(visible_document_filter(actor, SCOPE_MANAGE))
    )


def _to_out(db: Session, c: ConflictRecord) -> ConflictOut:
    out = ConflictOut.model_validate(c)
    new_doc = db.get(Document, c.new_doc_id)
    old_doc = db.get(Document, c.old_doc_id)
    out.new_doc_name = new_doc.filename if new_doc else None
    out.old_doc_name = old_doc.filename if old_doc else None
    out.old_doc_version = old_doc.version_no if old_doc else None
    return out


@router.get("", response_model=Page[ConflictOut], summary="冲突列表")
def list_conflicts(
    kb_id: int | None = Query(None),
    status: str | None = Query(None, pattern="^(pending|resolved|ignored)$"),
    doc_id: int | None = Query(None, description="只看某份文档相关的冲突"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    stmt = select(ConflictRecord)
    count_stmt = select(func.count(ConflictRecord.id))

    # 只返回「与我相关」的冲突（管理员看全部，普通用户看公开 + 自己上传的）
    cond_vis = _visible_conflict_filter(actor)
    stmt = stmt.where(cond_vis)
    count_stmt = count_stmt.where(cond_vis)

    if kb_id is not None:
        stmt = stmt.where(ConflictRecord.kb_id == kb_id)
        count_stmt = count_stmt.where(ConflictRecord.kb_id == kb_id)
    if status:
        stmt = stmt.where(ConflictRecord.status == status)
        count_stmt = count_stmt.where(ConflictRecord.status == status)
    if doc_id is not None:
        cond = (ConflictRecord.new_doc_id == doc_id) | (ConflictRecord.old_doc_id == doc_id)
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)

    total = int(db.execute(count_stmt).scalar_one())
    rows = (
        db.execute(
            stmt.order_by(ConflictRecord.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return Page[ConflictOut](
        items=[_to_out(db, c) for c in rows], total=total, page=page, page_size=page_size
    )


@router.get("/stats", response_model=ConflictStats, summary="冲突统计")
def conflict_stats(
    kb_id: int | None = Query(None),
    actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    # 统计口径必须与列表一致，否则会出现「统计说有 3 条、列表却是空的」
    vis = _visible_conflict_filter(actor)

    def _count(extra=None) -> int:
        stmt = select(func.count(ConflictRecord.id)).where(vis)
        if kb_id is not None:
            stmt = stmt.where(ConflictRecord.kb_id == kb_id)
        if extra is not None:
            stmt = stmt.where(extra)
        return int(db.execute(stmt).scalar_one())

    by_type = {
        r[0]: r[1]
        for r in db.execute(
            select(ConflictRecord.conflict_type, func.count(ConflictRecord.id))
            .where(vis, ConflictRecord.kb_id == kb_id if kb_id else True)
            .group_by(ConflictRecord.conflict_type)
        ).all()
    }
    by_relation = {
        r[0]: r[1]
        for r in db.execute(
            select(ConflictRecord.relation, func.count(ConflictRecord.id))
            .where(vis, ConflictRecord.kb_id == kb_id if kb_id else True)
            .group_by(ConflictRecord.relation)
        ).all()
    }

    return ConflictStats(
        total=_count(),
        pending=_count(ConflictRecord.status == "pending"),
        resolved=_count(ConflictRecord.status == "resolved"),
        ignored=_count(ConflictRecord.status == "ignored"),
        by_type=by_type,
        by_relation=by_relation,
    )


@router.post(
    "/detect",
    summary="对指定文档执行冲突检测",
    dependencies=[Depends(require_permission("conflict:resolve"))],
)
def run_detect(
    payload: ConflictDetectRequest,
    request: Request,
    actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    """重新检测某文档的冲突。

    ⚠️ 候选集限定为「当前主体有权访问的文档」（严格权限方案），
       受限文档完全不参与，因此不存在信息侧信道。
    """
    doc = db.get(Document, payload.doc_id)
    if doc is None:
        raise HTTPException(
            status_code=404, detail={"code": "DOC_NOT_FOUND", "message": "文档不存在"}
        )

    try:
        result = detect_conflicts(db, actor=actor, doc_id=payload.doc_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=404, detail={"code": "DOC_NOT_FOUND", "message": str(exc)}
        ) from exc

    audit.log_action(
        db,
        actor=actor,
        action=audit.AuditAction.CONFLICT_SCAN,
        resource_type="doc",
        resource_id=payload.doc_id,
        resource_name=doc.filename,
        detail={
            "candidate_pairs": result.candidate_pairs,
            "conflicts_found": len(result.conflicts),
            "llm_calls": result.llm_calls,
        },
        request=request,
        commit=True,
    )

    return {
        "doc_id": result.doc_id,
        "candidate_pairs": result.candidate_pairs,
        "conflict_count": len(result.conflicts),
        "conflicts": result.conflicts,
        "warnings": result.warnings,
    }


@router.post(
    "/scan",
    response_model=FullScanResponse,
    summary="全库一致性扫描（管理员，覆盖全部文档）",
    dependencies=[Depends(require_permission("system:scan"))],
)
def full_scan(
    payload: FullScanRequest,
    request: Request,
    actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    """对知识库内**全部**文档做一次冲突巡检。

    ⚠️ 为什么需要这个「越权」接口

    日常上传的冲突检测只比对「上传者有权访问的文档」，这是刻意设计 ——
    避免有人靠反复上传探测出自己无权查看的内容（信息侧信道）。

    代价是：「受限文档 与 公开文档」之间的矛盾**永远不会被自动发现**，
    形成一个没人知道的死角。

    因此需要管理员定期执行全库扫描来补上。因为它无视可见性限制：
      1. 受 system:scan 权限点保护
      2. 全过程写入审计日志（含扫描了哪些文档、调用了多少次模型）
    """
    import time as _time

    from app.models import KnowledgeBase

    kb = db.get(KnowledgeBase, payload.kb_id)
    if kb is None:
        raise HTTPException(
            status_code=404, detail={"code": "KB_NOT_FOUND", "message": "知识库不存在"}
        )

    started = _time.perf_counter()

    doc_ids = list(
        db.execute(
            select(Document.id).where(
                Document.kb_id == payload.kb_id, Document.is_active.is_(True)
            )
        ).scalars()
    )

    scanned = 0
    skipped = 0
    total_conflicts = 0
    llm_calls = 0
    documents_with_conflict: list[int] = []

    for did in doc_ids:
        if payload.only_unscanned:
            existing = int(
                db.execute(
                    select(func.count(ConflictRecord.id)).where(
                        ConflictRecord.new_doc_id == did
                    )
                ).scalar_one()
            )
            if existing:
                skipped += 1
                continue

        try:
            res = detect_conflicts(db, actor=actor, doc_id=did, ignore_permission=True)
        except ValueError:
            skipped += 1
            continue

        scanned += 1
        llm_calls += res.llm_calls
        if res.conflicts:
            total_conflicts += len(res.conflicts)
            documents_with_conflict.append(did)

    duration = int((_time.perf_counter() - started) * 1000)

    audit.log_action(
        db,
        actor=actor,
        action=audit.AuditAction.FULL_CONSISTENCY_SCAN,
        resource_type="kb",
        resource_id=payload.kb_id,
        resource_name=kb.name,
        detail={
            "scanned": scanned,
            "skipped": skipped,
            "conflicts_found": total_conflicts,
            "llm_calls": llm_calls,
            "documents_with_conflict": documents_with_conflict,
            "note": "全库巡检不做可见性限制，属管理员操作",
        },
        request=request,
        commit=True,
    )

    return FullScanResponse(
        kb_id=payload.kb_id,
        scanned_documents=scanned,
        skipped_documents=skipped,
        total_conflicts=total_conflicts,
        llm_calls=llm_calls,
        duration_ms=duration,
        documents_with_conflict=documents_with_conflict,
        message=(
            f"扫描完成：检查 {scanned} 份文档，跳过 {skipped} 份，"
            f"发现 {total_conflicts} 处冲突（模型调用 {llm_calls} 次，"
            f"耗时 {duration} ms）"
        ),
    )


@router.post(
    "/{conflict_id}/resolve",
    response_model=OkResponse,
    summary="处理冲突",
    dependencies=[Depends(require_permission("conflict:resolve"))],
)
def resolve_conflict(
    conflict_id: int,
    payload: ConflictResolveRequest,
    request: Request,
    actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    conflict = db.get(ConflictRecord, conflict_id)
    if conflict is None:
        raise HTTPException(
            status_code=404, detail={"code": "CONFLICT_NOT_FOUND", "message": "冲突记录不存在"}
        )

    new_doc = db.get(Document, conflict.new_doc_id)
    old_doc = db.get(Document, conflict.old_doc_id)

    # ⚠️ 权限校验：只能处理「自己上传文档」的冲突，管理员不受限。
    #    少了这道校验，任何持有 conflict:resolve 的用户都能借
    #    resolution=keep_old 删除**别人**的新文档 —— 越权删除。
    if new_doc is None:
        raise HTTPException(
            status_code=404, detail={"code": "DOC_NOT_FOUND", "message": "新文档不存在"}
        )
    if not can_edit_document(actor, new_doc):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "NOT_OWNER",
                "message": "只能处理自己上传文档的冲突；如需处理他人文档请联系管理员",
            },
        )

    action_desc = payload.resolution
    # ⚠️ 文件名先快照：keep_old / cancel 会把 new_doc 从库与会话中删除，
    #    之后再读 new_doc.filename 会拿到已失效对象（2026-10-14 故障关联）。
    new_name = new_doc.filename if new_doc is not None else "?"
    old_name = old_doc.filename if old_doc is not None else "?"
    # 新文档是否已被本请求物理删除（仅 keep_old / cancel）——决定收尾逻辑是否跳过
    doc_dropped = False
    # 待「提交成功之后」才删的磁盘文件，避免出现「事务回滚但文件已删」的脏数据
    pending_unlink: list[str] = []

    if payload.resolution == "overwrite":
        # 新文档取代旧文档 —— **只隔离冲突涉及的那几片**（切片级）。
        #    旧实现把旧文档整篇下架：一条条款被取代，其余无关条款一并从检索消失，
        #    这正是「一小点替换一大段」的根源。
        if old_doc is not None:
            if conflict.old_chunk_id:
                db.query(DocumentChunk).filter(
                    DocumentChunk.id == conflict.old_chunk_id
                ).update(
                    {
                        DocumentChunk.is_retrievable: False,
                        DocumentChunk.retired_reason: "overwrite",
                        # ⚠️ 记住「被谁取代」：删除新文档时要精确恢复这一片，
                        #    否则被覆盖的旧内容永久失联（2026-10-09 缺陷）。
                        DocumentChunk.superseded_by: (
                            new_doc.id if new_doc is not None else None
                        ),
                    },
                    synchronize_session=False,
                )
            db.flush()
            alive = int(
                db.execute(
                    select(func.count(DocumentChunk.id)).where(
                        DocumentChunk.doc_id == old_doc.id,
                        DocumentChunk.is_retrievable.is_(True),
                    )
                ).scalar_one()
            )
            # 只有旧文档**全部**切片都被取代时，才整篇下架
            if alive == 0:
                old_doc.is_active = False
                # ⚠️ 记录取代关系：删除新文档时要把旧文档回滚复活。
                #    这条路径跨 root（不同文件名才走冲突处理），版本链复活救不了它。
                old_doc.superseded_by = new_doc.id if new_doc is not None else None
                old_doc.retired_reason = "conflict_overwrite"
        if new_doc is not None:
            new_doc.has_conflict = False
            # 用户已确认「用新版取代旧版」⇒ 新文档里对应的冲突切片恢复可检索
            if conflict.new_chunk_id:
                db.query(DocumentChunk).filter(
                    DocumentChunk.id == conflict.new_chunk_id
                ).update(
                    {
                        DocumentChunk.is_retrievable: True,
                        DocumentChunk.retired_reason: None,
                    },
                    synchronize_session=False,
                )

    elif payload.resolution in ("keep_old", "cancel"):
        # 丢弃新文档。
        #
        # ⚠️ 顺序不可颠倒 —— 这是线上 2026-10-14「keep_old / cancel 报 500」的根因：
        #    conflict_record.new_doc_id 是 `ON DELETE CASCADE` 外键，
        #    db.delete(new_doc) 会把**本条冲突记录**一并级联删掉；
        #    若沿用旧写法「先删文档 → 最后统一改 conflict」，
        #    收尾的 conflict.resolution = ... 会对一条已不存在的行发 UPDATE，
        #    SQLAlchemy 在 commit 时抛：
        #      StaleDataError: UPDATE statement on table 'conflict_record'
        #      expected to update 1 row(s); 0 were matched  → 500。
        #    因此：**先把决策写入冲突行并落库，再删除新文档**。
        conflict.resolution = payload.resolution
        conflict.status = "resolved"
        conflict.resolved_by = actor.user_id
        conflict.resolved_at = datetime.now(UTC)
        db.flush()  # 此刻冲突行仍在，UPDATE 必命中 1 行

        if new_doc is not None:
            # 文件不在此刻删：等 db.commit() 成功后再删（见函数末尾），
            # 否则一旦后续提交失败，就会留下「有记录、无文件」的脏文档。
            pending_unlink.append(new_doc.storage_path)
            dropped_doc_id = new_doc.id
            dropped_kb_id = new_doc.kb_id
            db.delete(new_doc)
            # ⚠️ 新文档若曾取代过别的版本，丢弃它时同样要回滚复活，
            #    否则「保留旧版」会把旧版的下架状态固化下来（2026-10-08 缺陷）。
            db.flush()
            rollback_superseded(db, deleted_doc_id=dropped_doc_id, kb_id=dropped_kb_id)
            doc_dropped = True

        if doc_dropped:
            # 本条冲突记录已随新文档被级联删除：把对象移出会话，
            # 避免 commit 后 expire 触发对已删行的重载（ObjectDeletedError）。
            db.expunge(conflict)

    elif payload.resolution == "coexist":
        # 两者并存 ⇒ 解除新文档冲突切片的隔离
        if new_doc is not None:
            new_doc.has_conflict = False
            _unblock_conflict_chunks(db, new_doc.id)

    elif payload.resolution == "ignore":
        if new_doc is not None:
            new_doc.has_conflict = False
            _unblock_conflict_chunks(db, new_doc.id)

    # 收尾：写入冲突决策 + 解除检索排除。
    # ⚠️ keep_old / cancel 已在上面写过冲突状态、且冲突行已随文档级联消失，
    #    这里必须整体跳过，否则又会对一条已删的行发 UPDATE（原 500 根因）。
    if not doc_dropped:
        conflict.resolution = payload.resolution
        conflict.status = "ignored" if payload.resolution == "ignore" else "resolved"
        conflict.resolved_by = actor.user_id
        conflict.resolved_at = datetime.now(UTC)

        # 该文档若已无待处理冲突，解除检索排除
        if new_doc is not None:
            remaining = int(
                db.execute(
                    select(func.count(ConflictRecord.id)).where(
                        ConflictRecord.new_doc_id == new_doc.id,
                        ConflictRecord.status == "pending",
                        ConflictRecord.id != conflict_id,
                    )
                ).scalar_one()
            )
            if remaining == 0:
                new_doc.has_conflict = False
                # 已无待处理冲突 ⇒ 把该文档因冲突隔离的切片放回检索
                _unblock_conflict_chunks(db, new_doc.id)

    db.commit()

    # 事务已落地，再回收磁盘文件。
    # 顺序放在 commit 之后：避免「事务回滚、文件却已删」造成有记录无文件的脏数据。
    for path in pending_unlink:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass

    audit.log_action(
        db,
        actor=actor,
        action=audit.AuditAction.CONFLICT_RESOLVE,
        resource_type="conflict",
        resource_id=conflict_id,
        resource_name=f"{new_name} vs {old_name}",
        detail={
            "resolution": action_desc,
            "note": payload.note,
            # 该决策下冲突记录会随新文档被级联删除，决策留痕仅存于本审计日志
            "conflict_row_cascaded": doc_dropped,
        },
        request=request,
        commit=True,
    )

    labels = {
        "overwrite": "已用新文档覆盖旧版本",
        "keep_old": "已保留旧文档并丢弃新上传",
        "coexist": "已保留双方，冲突标记解除",
        "cancel": "已取消本次上传",
        "ignore": "已忽略该冲突",
    }
    return OkResponse(message=labels.get(action_desc, "已处理"))
