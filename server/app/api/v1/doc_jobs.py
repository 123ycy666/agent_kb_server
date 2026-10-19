"""文档任务接口（`/api/docs/jobs`）。

三条约定（与 `docs/03-设计/办公文档能力-接口设计.md` 一致）：
1. **建任务立即返回 202**，跑在 BackgroundTasks 线程里，前端 1–2 秒轮询一次；
2. 同一个 job 只能被**创建它的用户**读（服务端校验，不信入参）；
3. 成果文件走 `GET /docs/jobs/{id}/download?name=…`，只允许取本任务目录下的文件。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse

from app.core.deps import CurrentActor, get_current_actor
from app.secretary import doc_jobs

router = APIRouter(tags=["办公文档"])

TASKS = {
    "digest": "长文速读：一页纸要点 + 待办清单 + 出处",
    "proofread": "公文审校：错别字/标点/数字用法/层次序号 + 病句与前后矛盾",
    "table_audit": "表格审查：空值/重复行/合计不平/单位混写/文本数字/日期混用",
}


def _http(exc: doc_jobs.JobError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message})


@router.post("/docs/jobs", status_code=202, summary="建文档任务（异步，返回 job_id 轮询）")
async def create_job(
    request: Request,
    background: BackgroundTasks,
    actor: CurrentActor = Depends(get_current_actor),
):
    try:
        raw = await request.body()
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        raise HTTPException(400, detail={"code": "BAD_JSON", "message": "请求体不是合法 JSON"})

    task = (data.get("task") or "").strip()
    if task not in TASKS:
        raise HTTPException(400, detail={
            "code": "UNSUPPORTED_TASK",
            "message": f"暂不支持的任务类型：{task or '(为空)'}。可用：{', '.join(TASKS)}",
        })
    file_ids = data.get("file_ids") or []
    if isinstance(file_ids, str):
        file_ids = [f.strip() for f in file_ids.replace("，", ",").split(",") if f.strip()]

    try:
        job = doc_jobs.create(actor.user_id, task, file_ids, data.get("options") or {})
    except doc_jobs.JobError as exc:
        raise _http(exc) from exc

    background.add_task(doc_jobs.run, job["job_id"], actor.user_id)
    return job


@router.get("/docs/jobs", summary="我的文档任务列表")
def list_jobs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    actor: CurrentActor = Depends(get_current_actor),
):
    return doc_jobs.list_jobs(actor.user_id, page=page, page_size=page_size)


@router.get("/docs/jobs/{job_id}", summary="任务详情（含进度、结果、成果文件）")
def get_job(job_id: str, actor: CurrentActor = Depends(get_current_actor)):
    try:
        return doc_jobs.public(doc_jobs.load(job_id, actor.user_id))
    except doc_jobs.JobError as exc:
        raise _http(exc) from exc


@router.get("/docs/jobs/{job_id}/download", summary="下载成果文件")
def download(job_id: str, name: str = Query(...), actor: CurrentActor = Depends(get_current_actor)):
    try:
        meta = doc_jobs.load(job_id, actor.user_id)
        path = doc_jobs.output_path(meta, name)
    except doc_jobs.JobError as exc:
        raise _http(exc) from exc
    return FileResponse(str(path), filename=path.name)


@router.post("/docs/jobs/{job_id}/cancel", summary="取消任务（正在跑的那一步跑完就停）")
def cancel(job_id: str, actor: CurrentActor = Depends(get_current_actor)):
    try:
        return doc_jobs.cancel(job_id, actor.user_id)
    except doc_jobs.JobError as exc:
        raise _http(exc) from exc
