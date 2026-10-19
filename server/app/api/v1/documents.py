"""文档接口：上传、管理、标签、版本、原文件下载。

⚠️ 上传接口返回 task_id，**执行体当前是同步内联**的。
   将来把执行体换成 Celery / 线程池时，接口契约不用变，调用方无感知。
"""

from __future__ import annotations

import json
import logging
import mimetypes
import uuid
from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse
from app.tasks import enqueue_ingest
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import CurrentActor, get_current_actor, require_permission
from app.models import (
    ConflictRecord,
    Document,
    DocumentChunk,
    KnowledgeBase,
)
from app.schemas.common import OkResponse, Page
from app.schemas.document import (
    DocumentChunkOut,
    DocumentOut,
    DocumentTagSetRequest,
    DocumentVersionOut,
    DocumentVisibilityRequest,
    UploadAcceptedResponse,
)
from app.schemas.kb import ReindexResponse
from app.services import audit, platform as platform_service
from app.services.conflict import detect_conflicts
from app.services.document_lifecycle import rollback_superseded, stale_chunk_count
from app.services.ingestion import (
    DuplicateDocumentError,
    IngestError,
    ingest_document,
    parsers,
    reindex_document,
)
from app.services.permission import (
    SCOPE_MANAGE,
    SCOPE_SEARCH,
    can_edit_document,
    is_document_visible,
    visible_document_filter,
)
from app.services.tag_governance import (
    normalize_tags,
    release_tag_usage,
    sync_tag_vocabulary,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["文档"])


# ══════════════════════════════════════════════════════════════
#  工具
# ══════════════════════════════════════════════════════════════


def _require_kb(db: Session, kb_id: int) -> KnowledgeBase:
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise HTTPException(
            status_code=404, detail={"code": "KB_NOT_FOUND", "message": "知识库不存在"}
        )
    return kb


def _require_visible_doc(
    db: Session, actor: CurrentActor, doc_id: int, scope: str = SCOPE_SEARCH
) -> Document:
    """获取文档并校验可见性。

    - `scope="manage"`：管理视图（管理员可见全部）—— 详情 / 改 / 删用
    - `scope="search"`：检索视图（人人只见公开 + 自己）—— 默认

    ⚠️ 不存在与无权限统一返回 404，避免通过状态码差异探测文档是否存在。
    """
    doc = db.get(Document, doc_id)
    if doc is None or not is_document_visible(db, actor, doc_id=doc_id, scope=scope):
        raise HTTPException(
            status_code=404,
            detail={"code": "DOC_NOT_FOUND", "message": "文档不存在或你没有访问权限"},
        )
    return doc


def _doc_out(db: Session, doc: Document) -> DocumentOut:
    out = DocumentOut.model_validate(doc)
    # 标签已直接存放在 document.tags（字符串数组），无需 JOIN 关联表
    out.tags = list(doc.tags or [])
    out.conflict_count = int(
        db.execute(
            select(func.count(ConflictRecord.id)).where(ConflictRecord.new_doc_id == doc.id)
        ).scalar_one()
    )
    return out


# ══════════════════════════════════════════════════════════════
#  上传
# ══════════════════════════════════════════════════════════════


# ⚠️ 以下后台执行辅助函数（_snapshot_actor / _mark_task_failed / _run_ingest_task）
#    已整体迁移至 ``app/tasks/__init__.py`` 的 ``ingest_document_task``（Celery 执行体）。
#    上传接口改为 ``enqueue_ingest(...)`` 派发，接口契约（202 + task_id）不变。



@router.post(
    "/kb/{kb_id}/documents",
    response_model=UploadAcceptedResponse,
    status_code=202,
    summary="上传文档（异步受理）",
    dependencies=[Depends(require_permission("doc:upload"))],
)
def upload_document(
    background_tasks: BackgroundTasks,
    kb_id: int,
    request: Request,
    file: UploadFile = File(...),
    is_public: bool = Form(
        default=False,
        description="是否公开：true=所有人可见可检索；false（默认）=仅上传者本人可见。",
    ),
    tags: str = Form(
        default="[]",
        description='标签 JSON 数组，如 ["行政", "预算"]。可选、可自由填写。',
    ),
    actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    """上传文档：**受理即返回**（202），解析与向量化在后台完成。

    ⚠️ 端点是 `def` 而非 `async def`（2026-10-08 修复）：
       早前唯一的 async 端点把同步阻塞的解析/向量化/冲突检测放在事件循环上，
       导致上传大文件时**整个服务的请求全部排队**（实测 1500 段上传 56s，
       期间健康检查连续 11 次超时）。`def` 端点会被 FastAPI 丢进线程池，
       不再占用事件循环。
    """
    kb = _require_kb(db, kb_id)

    filename = file.filename or "unnamed"
    if not parsers.is_supported(filename):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "UNSUPPORTED_FORMAT",
                "message": f"不支持的文件格式。支持：{', '.join(sorted(parsers.SUPPORTED_EXTENSIONS))}",
            },
        )

    # 标签：JSON 数组字符串 → list[str]
    # ⚠️ 未显式 public 即 private（最小权限）；不做「默认公开」，
    #    否则未勾选的文档会因漏选而被所有人搜到。
    try:
        tag_list = json.loads(tags) if tags else []
        if not isinstance(tag_list, list):
            raise ValueError("tags 必须是数组")
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "BAD_TAGS", "message": f"标签格式错误：{exc}"},
        ) from exc

    # ⚠️ 同步读取文件内容：端点已改为 def，FastAPI 会把它放进线程池执行，
    #    不占用事件循环，因此可以直接读底层句柄（不能再用 await）。
    #    按上限 +1 读取做熔断，避免超大文件白白占用内存。
    max_bytes = settings.max_upload_mb * 1024 * 1024
    content = file.file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "FILE_TOO_LARGE",
                "message": f"文件超过大小上限（{settings.max_upload_mb} MB）",
            },
        )

    # ⚠️ 先落盘：文件内容写入磁盘后再把路径交给后台任务（而非把字节塞进消息里），
    #    既避免大文件撑爆 Redis，又保证后端重启后 worker 仍能从磁盘取回任务（不丢任务）。
    from app.services.ingestion import save_upload
    storage_path = save_upload(content, kb_id, filename)

    task = platform_service.create_task(
        db,
        task_type="doc_ingest",
        kb_id=kb_id,
        actor=actor,
        status="pending",
    )
    db.commit()  # 先落库：后台任务用**独立 session**，必须能读到这条任务

    # 2026-10-15 单进程合并：Celery → FastAPI BackgroundTasks（无 Redis/worker 进程）。
    #    接口契约不变（202 + task_id，轮询 AsyncTask）；进程重启会丢未完成任务
    #    （任务表停留 running，重新上传即可）。
    actor_dict = {
        "type": actor.type,
        "user_id": actor.user_id,
        "username": actor.username,
        "display_name": actor.display_name,
        "role_ids": actor.role_ids,
        "role_codes": actor.role_codes,
        "permissions": list(actor.permissions),
    }
    enqueue_ingest(
        background_tasks,
        task_id=str(task.id),
        kb_id=kb_id,
        filename=filename,
        storage_path=str(storage_path),
        is_public=is_public,
        tag_list=tag_list,
        actor_dict=actor_dict,
    )

    # 审计在**受理时**就落库：此时还没有 doc_id，记录 task_id 与文件大小。
    # 真正的入库结果由后台任务写进 task.result，不再重复记一条上传审计。
    audit.log_action(
        db,
        actor=actor,
        action=audit.AuditAction.DOC_UPLOAD,
        resource_type="doc",
        resource_id=None,
        resource_name=filename,
        detail={
            "kb_id": kb_id,
            "task_id": str(task.id),
            "accepted": True,
            "file_size": len(content),
            "is_public": is_public,
            "tags": tag_list,
        },
        request=request,
        commit=True,
    )

    # ⚠️ 202 只表示「已受理」，不代表入库完成。冲突数、doc_id 都在后台任务
    #    完成时写进 task.result，调用方需轮询任务状态获取最终结果。
    return UploadAcceptedResponse(
        task_id=str(task.id),
        status="accepted",
        message="已受理，正在后台解析与向量化；可轮询任务状态获取最终结果",
    )


# ══════════════════════════════════════════════════════════════
#  列表与详情
# ══════════════════════════════════════════════════════════════


@router.get(
    "/kb/{kb_id}/documents",
    response_model=Page[DocumentOut],
    summary="文档列表（受权限过滤）",
)
def list_documents(
    kb_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    parse_status: str | None = Query(None),
    has_conflict: bool | None = Query(None),
    tags: list[str] = Query(default_factory=list, description="按标签筛选（全部命中）"),
    actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    _require_kb(db, kb_id)

    # ⚠️ 列表属「管理视图」：管理员可见全部，普通用户可见「公开 + 自己上传」。
    #    检索视图（SCOPE_SEARCH）口径更窄，管理员在那里同样只见公开 + 自己的。
    vis = visible_document_filter(actor, SCOPE_MANAGE)
    base = select(Document).where(Document.kb_id == kb_id, vis)
    count_stmt = select(func.count(Document.id)).where(Document.kb_id == kb_id, vis)

    if parse_status:
        base = base.where(Document.parse_status == parse_status)
        count_stmt = count_stmt.where(Document.parse_status == parse_status)
    if has_conflict is not None:
        base = base.where(Document.has_conflict.is_(has_conflict))
        count_stmt = count_stmt.where(Document.has_conflict.is_(has_conflict))

    wanted_tags = [t for t in tags if t and t.strip()]
    if wanted_tags:
        # PostgreSQL 数组「包含」：文档须同时带这些标签
        tag_cond = Document.tags.contains(wanted_tags)
        base = base.where(tag_cond)
        count_stmt = count_stmt.where(tag_cond)

    total = int(db.execute(count_stmt).scalar_one())
    rows = (
        db.execute(
            base.order_by(Document.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )

    return Page[DocumentOut](
        items=[_doc_out(db, d) for d in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/documents/{doc_id}", response_model=DocumentOut, summary="文档详情")
def get_document(
    doc_id: int,
    actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    doc = _require_visible_doc(db, actor, doc_id)
    return _doc_out(db, doc)


@router.get("/documents/{doc_id}/download", summary="下载原文件")
def download_document(
    doc_id: int,
    request: Request,
    actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    """流式返回上传的原始文件。

    ⚠️ 权限口径与「改 / 删」一致：**上传者本人 或 管理员**。
       刻意**不**用「能看见就能下载」——
       可见性代表「可读」，下载是「拿走原件」，两者风险等级不同。
    """
    doc = _require_visible_doc(db, actor, doc_id, scope=SCOPE_MANAGE)
    if not can_edit_document(actor, doc):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "NOT_OWNER",
                "message": "只有上传者本人或管理员可以下载原文件",
            },
        )

    path = Path(doc.storage_path)
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail={
                "code": "FILE_MISSING",
                "message": "原始文件已丢失（可能被人为从磁盘删除），请重新上传",
            },
        )

    audit.log_action(
        db,
        actor=actor,
        action=audit.AuditAction.DOC_DOWNLOAD,
        resource_type="doc",
        resource_id=doc_id,
        resource_name=doc.filename,
        detail={"storage_path": doc.storage_path, "file_size": doc.file_size},
        request=request,
        commit=True,
    )

    media_type = mimetypes.guess_type(doc.filename)[0] or "application/octet-stream"
    # ⚠️ 中文文件名必须靠 Starlette 的 RFC 5987 filename* 编码，
    #    否则响应头里的中文会乱码，浏览器存成乱码文件名。
    return FileResponse(path, filename=doc.filename, media_type=media_type)


@router.get(
    "/documents/{doc_id}/content",
    summary="内联预览原文件（浏览器可直接打开）",
)
def preview_document(
    doc_id: int,
    actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    """以 `Content-Disposition: inline` 返回原文件，供前端内联预览。

    与 `download` 的区别：
    - **权限更宽松**：可见即可预览（下载仍限「上传者本人或管理员」）——
      预览是"看一眼"，风险等级低于"拿走原件"。
    - 响应头 `inline` + 正确 `Content-Type`，浏览器 / `<iframe>` 可原地渲染，**不触发下载**。

    ⚠️ 该接口仍需 `Authorization` 头，因此**不能**把 URL 直接塞进 `<iframe src>`；
      前端应带 token 拉成 blob，再 `URL.createObjectURL(blob)`。
    """
    doc = _require_visible_doc(db, actor, doc_id)

    path = Path(doc.storage_path)
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail={
                "code": "FILE_MISSING",
                "message": "原始文件已丢失（可能被人为从磁盘删除），请重新上传",
            },
        )

    media_type = mimetypes.guess_type(doc.filename)[0] or "application/octet-stream"
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Content-Disposition": "inline"},
    )


@router.get(
    "/documents/{doc_id}/chunks",
    response_model=list[DocumentChunkOut],
    summary="文档切片预览",
)
def list_chunks(
    doc_id: int,
    limit: int = Query(50, ge=1, le=500),
    actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    _require_visible_doc(db, actor, doc_id)
    rows = (
        db.execute(
            select(DocumentChunk)
            .where(DocumentChunk.doc_id == doc_id)
            .order_by(DocumentChunk.chunk_index)
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return [DocumentChunkOut.model_validate(c) for c in rows]


@router.get(
    "/documents/{doc_id}/versions",
    response_model=list[DocumentVersionOut],
    summary="版本历史",
)
def list_versions(
    doc_id: int,
    actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    doc = _require_visible_doc(db, actor, doc_id)
    root = doc.root_id or doc.id
    rows = (
        db.execute(
            select(Document)
            .where(Document.root_id == root)
            .order_by(Document.version_no.desc())
        )
        .scalars()
        .all()
    )
    return [
        DocumentVersionOut(
            id=d.id,
            version_no=d.version_no,
            is_active=d.is_active,
            filename=d.filename,
            content_hash=d.content_hash,
            chunk_count=d.chunk_count,
            created_at=d.created_at,
        )
        for d in rows
    ]


@router.delete(
    "/documents/{doc_id}",
    response_model=OkResponse,
    summary="删除文档",
    dependencies=[Depends(require_permission("doc:delete"))],
)
def delete_document(
    doc_id: int,
    request: Request,
    actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    doc = _require_visible_doc(db, actor, doc_id, scope=SCOPE_MANAGE)

    # ⚠️ 只有「上传者本人 或 管理员」能删除（公开文档也不许他人删）
    if not can_edit_document(actor, doc):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "NOT_OWNER",
                "message": "只能删除自己上传的文档；如需删除他人文档请联系管理员",
            },
        )

    name = doc.filename
    kb_id = doc.kb_id
    doc_tags = list(doc.tags or [])

    # 删除物理文件。⚠️ 清理动作绝不抛异常 ——
    #    否则删除失败会掩盖真正的错误，且某些运行环境不允许删文件。
    from pathlib import Path

    try:
        Path(doc.storage_path).unlink(missing_ok=True)
    except OSError:
        pass

    # 回收标签使用计数
    release_tag_usage(db, doc_tags)

    db.delete(doc)
    db.flush()  # 先让删除落库，回滚判定才能正确看到「继任者已消失」

    # ⚠️ 版本链自愈：把被本文档取代的下架文档复活回来。
    #    少了这一步，「上传新版 → 删新版」会让旧版永久不可检索。
    restored = rollback_superseded(db, deleted_doc_id=doc_id, kb_id=kb_id)
    db.commit()

    warnings: list[str] = []
    for r in restored:
        msg = f"已自动恢复被其取代的旧版本《{r['filename']}》(v{r['version_no']})"
        if r["stale_chunks"]:
            msg += f"；但其中 {r['stale_chunks']} 个切片向量模型已过期，需重建索引后才能检索"
            warnings.append(msg)
        else:
            warnings.append(msg + "，已重新可检索")

    audit.log_action(
        db,
        actor=actor,
        action=audit.AuditAction.DOC_DELETE,
        resource_type="doc",
        resource_id=doc_id,
        resource_name=name,
        detail={"kb_id": kb_id, "restored": restored},
        request=request,
        commit=True,
    )
    return OkResponse(message=f"文档《{name}》已删除", warnings=warnings)


@router.post(
    "/documents/{doc_id}/restore",
    response_model=OkResponse,
    summary="恢复某个历史版本为当前活跃版本",
    dependencies=[Depends(require_permission("doc:update"))],
)
def restore_document(
    doc_id: int,
    actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    """把一个已下架的历史版本重新设为活跃。

    ⚠️ 为什么必须有这个接口：版本历史此前**只能看不能恢复**（只读 list_versions），
       一旦文档被误下架，用户只能删库重传。现在删除会自动回滚，
       本接口则用于「手动切回某个旧版本」这种主动操作。
    """
    doc = _require_visible_doc(db, actor, doc_id, scope=SCOPE_MANAGE)
    if not can_edit_document(actor, doc):
        raise HTTPException(
            status_code=403,
            detail={"code": "NOT_OWNER", "message": "只能恢复自己上传的文档"},
        )

    root = doc.root_id or doc.id
    # 同链其他活跃版本一并下架，保证「仅最新版可检索」的约定不被破坏
    db.query(Document).filter(
        Document.kb_id == doc.kb_id,
        or_(Document.root_id == root, Document.id == root),
        Document.id != doc.id,
        Document.is_active.is_(True),
    ).update({Document.is_active: False}, synchronize_session=False)

    doc.is_active = True
    doc.superseded_by = None
    doc.retired_reason = None
    db.query(DocumentChunk).filter(DocumentChunk.doc_id == doc.id).update(
        {DocumentChunk.is_retrievable: True}, synchronize_session=False
    )
    db.commit()

    warnings: list[str] = []
    stale = stale_chunk_count(db, doc.id)
    if stale:
        warnings.append(
            f"已恢复为活跃版本，但其中 {stale} 个切片的向量模型与当前档位不符，"
            "需执行重建索引后才能被检索命中"
        )
    return OkResponse(message=f"已恢复文档《{doc.filename}》为活跃版本", warnings=warnings)


# ══════════════════════════════════════════════════════════════
#  标签
# ══════════════════════════════════════════════════════════════


@router.post(
    "/documents/{doc_id}/tags",
    response_model=DocumentOut,
    summary="设置文档标签（覆盖式）",
    dependencies=[Depends(require_permission("doc:tag"))],
)
def set_document_tags(
    doc_id: int,
    payload: DocumentTagSetRequest,
    request: Request,
    actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    """覆盖式设置标签。传空数组表示清空标签。"""
    doc = _require_visible_doc(db, actor, doc_id)
    if not can_edit_document(actor, doc):
        raise HTTPException(
            status_code=403,
            detail={"code": "NOT_OWNER", "message": "只能修改自己上传的文档的标签"},
        )

    # 规范化 + 去重（非法标签跳过并记警告，不整体失败）
    new_tags, warnings = normalize_tags(payload.tags)
    old_tags = list(doc.tags or [])

    # 词表计数按「差量」调整，避免重复计数
    release_tag_usage(db, [t for t in old_tags if t not in new_tags])
    sync_tag_vocabulary(
        db, [t for t in new_tags if t not in old_tags], created_by=actor.user_id
    )

    doc.tags = new_tags
    # ⚠️ 标签必须冗余下沉到切片，否则检索时无法做标签过滤（要么 JOIN，要么失效）
    db.query(DocumentChunk).filter(DocumentChunk.doc_id == doc_id).update(
        {DocumentChunk.tags: new_tags}, synchronize_session=False
    )
    db.commit()

    audit.log_action(
        db,
        actor=actor,
        action=audit.AuditAction.DOC_TAG_UPDATE,
        resource_type="doc",
        resource_id=doc_id,
        resource_name=doc.filename,
        detail={"tags": new_tags, "warnings": warnings},
        request=request,
        commit=True,
    )

    db.refresh(doc)
    return _doc_out(db, doc)


@router.delete(
    "/documents/{doc_id}/tags/{tag_name}",
    response_model=DocumentOut,
    summary="移除文档标签",
    dependencies=[Depends(require_permission("doc:tag"))],
)
def remove_document_tag(
    doc_id: int,
    tag_name: str,
    actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    doc = _require_visible_doc(db, actor, doc_id)
    if not can_edit_document(actor, doc):
        raise HTTPException(
            status_code=403,
            detail={"code": "NOT_OWNER", "message": "只能修改自己上传的文档的标签"},
        )

    current = list(doc.tags or [])
    remaining = [t for t in current if t != tag_name]
    if len(remaining) == len(current):
        raise HTTPException(
            status_code=404,
            detail={"code": "TAG_NOT_FOUND", "message": "该文档没有这个标签"},
        )

    release_tag_usage(db, [tag_name])
    doc.tags = remaining
    db.query(DocumentChunk).filter(DocumentChunk.doc_id == doc_id).update(
        {DocumentChunk.tags: remaining}, synchronize_session=False
    )
    db.commit()
    db.refresh(doc)
    return _doc_out(db, doc)


# ══════════════════════════════════════════════════════════════
#  可见性（公开 / 私有）
# ══════════════════════════════════════════════════════════════


@router.patch(
    "/documents/{doc_id}/visibility",
    response_model=DocumentOut,
    summary="设置文档可见性（公开 / 私有）",
    dependencies=[Depends(require_permission("doc:update"))],
)
def set_document_visibility(
    doc_id: int,
    payload: DocumentVisibilityRequest,
    request: Request,
    actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    """切换文档可见性。

    - `public`  → 所有人可看、可检索
    - `private` → 仅上传者本人可看、可检索（管理员在管理视图可见）

    ⚠️ 仅「上传者本人 或 管理员」可切换。
    """
    doc = _require_visible_doc(db, actor, doc_id, scope=SCOPE_MANAGE)
    if not can_edit_document(actor, doc):
        raise HTTPException(
            status_code=403,
            detail={"code": "NOT_OWNER", "message": "只能修改自己上传的文档"},
        )

    old = doc.visibility
    doc.visibility = payload.visibility
    db.commit()

    audit.log_action(
        db,
        actor=actor,
        action=audit.AuditAction.DOC_UPDATE,
        resource_type="doc",
        resource_id=doc_id,
        resource_name=doc.filename,
        detail={"visibility": {"from": old, "to": payload.visibility}},
        request=request,
        commit=True,
    )

    db.refresh(doc)
    return _doc_out(db, doc)


@router.post(
    "/documents/{doc_id}/reindex",
    response_model=ReindexResponse,
    summary="重建单个文档的索引",
    dependencies=[Depends(require_permission("doc:upload"))],
)
def reindex_one_document(
    doc_id: int,
    request: Request,
    actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    """用当前档位的 embedding 模型重建该文档的向量与关键词索引。"""
    doc = _require_visible_doc(db, actor, doc_id)

    try:
        res = reindex_document(db, doc_id=doc_id)
    except IngestError as exc:
        raise HTTPException(
            status_code=400, detail={"code": "REINDEX_FAILED", "message": str(exc)}
        ) from exc

    audit.log_action(
        db,
        actor=actor,
        action=audit.AuditAction.DOC_REINDEX,
        resource_type="doc",
        resource_id=doc_id,
        resource_name=doc.filename,
        detail={
            "chunk_count": res.chunk_count,
            "from_model": res.from_model,
            "to_model": res.to_model,
        },
        request=request,
        commit=True,
    )

    return ReindexResponse(
        doc_id=doc_id,
        document_count=1,
        chunk_count=res.chunk_count,
        from_model=res.from_model,
        to_model=res.to_model,
        duration_ms=res.duration_ms,
        message=f"已重建 {res.chunk_count} 条切片（{res.from_model} → {res.to_model}）",
    )
