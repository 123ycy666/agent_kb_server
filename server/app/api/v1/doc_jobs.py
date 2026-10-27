"""文档任务接口（`/api/docs/jobs`）。

三条约定（与 `docs/03-设计/办公文档能力-接口设计.md` 一致）：
1. **建任务立即返回 202**，跑在 BackgroundTasks 线程里，前端 1–2 秒轮询一次；
2. 同一个 job 只能被**创建它的用户**读（服务端校验，不信入参）；
3. 成果文件走 `GET /docs/jobs/{id}/download?name=…`，只允许取本任务目录下的文件。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from app.core.deps import CurrentActor, get_current_actor
from app.secretary import doc_jobs

router = APIRouter(tags=["办公文档"])

TASKS = {
    "digest": "长文速读：一页纸要点 + 待办清单 + 出处",
    "proofread": "公文审校：错别字/标点/数字用法/层次序号 + 病句与前后矛盾",
    "table_audit": "表格审查：空值/重复行/合计不平/单位混写/文本数字/日期混用",
    "deck": "做 PPT：按标题分页排版，交付前过自检（不通过不交付）",
    "doc_write": "写公文：按公文版式重排（三号仿宋/行距/页码），交付前过自检",
    "compare": "文件比对：两份材料逐段差异（需 options.other_file_id）",
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


# ── 纪要 → 拟办（2026-10-21，F3-1）──────────────────────────────────
# 两步：preview 只出拟办清单（零写入，人看一眼）；dispatch 才真建任务。
# 为什么不自动派：派错人、派错时限要担责（见 dispatch_from_digest 的注释）。
@router.post("/docs/jobs/{job_id}/to-dispatch", summary="速读结果 → 拟办清单（预览，不落库）")
def preview_dispatch(job_id: str, actor: CurrentActor = Depends(get_current_actor)):
    from app.secretary import dispatch_from_digest as D
    try:
        meta = doc_jobs.load(job_id, actor.user_id)
    except doc_jobs.JobError as exc:
        raise _http(exc) from exc
    if meta.get("task") != "digest":
        return {"error": "只有速读任务（digest）能转派单", "task": meta.get("task")}
    items = ((meta.get("result") or {}).get("items")) or []
    plan = D.plan_tasks(items)
    return {"job_id": job_id, "file_name": (meta.get("result") or {}).get("file_name"),
            **plan}


@router.post("/docs/jobs/{job_id}/dispatch", summary="确认下达：按拟办清单建任务（会真派单）")
async def apply_dispatch(job_id: str, request: Request,
                         actor: CurrentActor = Depends(get_current_actor)):
    """按拟办清单建任务。默认**只建拟办**，`confirm=true` 才真下发。

    真下发走现有派单通道（`services.dispatch_service`），本接口只做"把清单交过去"。
    """
    from app.secretary import dispatch_from_digest as D
    data = await _read_json(request) if request.headers.get("content-length") not in (None, "0") \
        else {}
    try:
        meta = doc_jobs.load(job_id, actor.user_id)
    except doc_jobs.JobError as exc:
        raise _http(exc) from exc
    if meta.get("task") != "digest":
        return {"error": "只有速读任务（digest）能转派单", "task": meta.get("task")}

    plan = D.plan_tasks(((meta.get("result") or {}).get("items")) or [])
    payloads = [D.to_payload(t, session_id=job_id) for t in plan["tasks"]]
    if not data.get("confirm"):
        return {"job_id": job_id, "need_confirm": True,
                "message": "这是拟办清单；确认后传 {\"confirm\": true} 才真建任务",
                "payloads": payloads, "warnings": plan["warnings"]}

    try:
        from app.services import dispatch_service as SVC
    except Exception as exc:                       # noqa: BLE001
        return {"error": "派单模块不可用：%s" % str(exc)[:160], "payloads": payloads}

    created, failed = [], []
    for p in payloads:
        try:
            fn = getattr(SVC, "create_task_from_payload", None) or getattr(SVC, "create_task", None)
            if fn is None:
                return {"error": "派单模块没有可用的建任务入口", "payloads": payloads}
            created.append(fn(p))
        except Exception as exc:                   # noqa: BLE001
            failed.append({"payload": p, "error": str(exc)[:200]})
    return {"job_id": job_id, "created": created, "failed": failed,
            "warnings": plan["warnings"]}

@router.post("/docs/jobs/batch", status_code=202, summary="批量建文档任务（一次多份文件）")
async def create_batch(request: Request, background: BackgroundTasks,
                       actor: CurrentActor = Depends(get_current_actor)):
    data = await _read_json(request)
    task = (data.get("task") or "").strip()
    if task not in TASKS:
        return JSONResponse(status_code=400, content={
            "code": "UNSUPPORTED_TASK",
            "message": f"暂不支持的任务类型：{task or '(为空)'}。可用：{', '.join(TASKS)}"})
    fids = [str(x) for x in (data.get("file_ids") or []) if str(x).strip()]
    if not fids:
        return JSONResponse(status_code=400, content={"code": "NO_FILE", "message": "没有文件"})
    res = doc_jobs.batch_create(actor.user_id, task, fids, data.get("options") or {})
    for job in res["created"]:
        background.add_task(doc_jobs.run, job["job_id"], actor.user_id)
    return res
