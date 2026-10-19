"""问答 / 文档任务用的临时文件接口（`/api/files/*`）。

设计见 `docs/办公文档能力-接口设计.md`。要点：
1. **两段式为主**：先 `POST /api/files` 拿 `file_id`，再由 `/api/ask` 的 `file_ids`
   或 `/api/docs/jobs` 的 `file_ids` 引用 —— 主流开源实现（Dify / Open WebUI / RAGFlow）
   都是这个形态，因为只有它支持「同一份文件多轮复用、解析失败不浪费一次提问」。
2. 端点一律用 `def` 而不是 `async def`（与 documents.py 同一个理由）：
   解析是同步阻塞的 CPU 活，写成 async 会占住事件循环，导致**全站转圈**。
   用 `def` FastAPI 会自动丢线程池。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from app.core.config import settings
from app.core.deps import CurrentActor, get_current_actor
from app.secretary import doc_files

router = APIRouter(tags=["办公文档"])


def _http(exc: doc_files.DocFileError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message})


@router.post("/files", summary="上传文件（问答 / 文档任务用，临时文件不进入知识库）")
def upload_file(
    file: UploadFile = File(...),
    purpose: str = Form(default="chat", description="chat（问答用）/ task（任务用）"),
    actor: CurrentActor = Depends(get_current_actor),
):
    """上传即解析。**解析失败也返回 file_id**（`parsed.ok=false` + `error`）。

    这样用户能「先问起来 + 知道为什么解析不了」，而不是被一个 500 挡住。
    """
    max_bytes = settings.max_upload_mb * 1024 * 1024
    # 按上限 +1 读取做熔断，避免超大文件占内存（与 documents.py 同一手法）
    content = file.file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "FILE_TOO_LARGE",
                "message": f"文件超过大小上限（{settings.max_upload_mb} MB）",
            },
        )
    if not content:
        raise HTTPException(
            status_code=400,
            detail={"code": "EMPTY_FILE", "message": "文件是空的"},
        )

    try:
        return doc_files.save(
            actor.user_id,
            file.filename or "unnamed",
            content,
            user_code=actor.username,
            purpose=purpose or "chat",
        )
    except doc_files.DocFileError as exc:
        raise _http(exc) from exc


@router.get("/files", summary="我的文件列表（只返回自己的）")
def list_files(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    actor: CurrentActor = Depends(get_current_actor),
):
    return doc_files.list_files(actor.user_id, page=page, page_size=page_size)


@router.get("/files/{file_id}", summary="文件元信息")
def get_file(file_id: str, actor: CurrentActor = Depends(get_current_actor)):
    try:
        return doc_files.public(doc_files.load(file_id, actor.user_id))
    except doc_files.DocFileError as exc:
        raise _http(exc) from exc


@router.get("/files/{file_id}/blocks", summary="解析结果（段落/表格，带章节路径与段号，用于跳到原文）")
def get_blocks(
    file_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
    actor: CurrentActor = Depends(get_current_actor),
):
    try:
        return doc_files.blocks(file_id, actor.user_id, offset=offset, limit=limit)
    except doc_files.DocFileError as exc:
        raise _http(exc) from exc


@router.get("/files/{file_id}/text", summary="整篇纯文本（调试与外部系统取文本用）")
def get_text(file_id: str, actor: CurrentActor = Depends(get_current_actor)):
    try:
        return {"file_id": file_id, "text": doc_files.plain_text(file_id, actor.user_id)}
    except doc_files.DocFileError as exc:
        raise _http(exc) from exc


@router.get("/files/{file_id}/download", summary="下载原件")
def download_file(file_id: str, actor: CurrentActor = Depends(get_current_actor)):
    try:
        meta = doc_files.load(file_id, actor.user_id)
        path = doc_files.orig_path(meta)
    except doc_files.DocFileError as exc:
        raise _http(exc) from exc
    return FileResponse(str(path), filename=meta["name"])


@router.delete("/files/{file_id}", summary="删除文件（含解析结果）")
def delete_file(file_id: str, actor: CurrentActor = Depends(get_current_actor)):
    try:
        doc_files.delete(file_id, actor.user_id)
    except doc_files.DocFileError as exc:
        raise _http(exc) from exc
    return {"ok": True, "file_id": file_id}
