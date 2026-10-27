"""文档任务（`/api/docs/jobs`）：把"要跑几十秒到几分钟"的活变成可轮询的任务。

为什么任务化而不是同步返回（设计稿 §1 的硬界线）：
20 页文件要切块、逐块调模型、生成 docx，几十秒到几分钟。同步请求会撞网关超时，
用户看到的是"转圈然后 502"——比慢更糟。

存储：`data/doc_jobs/u<uid>/<job_id>/job.json` + `out/`（成果文件）。
和 doc_files 一样用 JSON sidecar 不建表（单进程、量小）；区别是任务状态**写得很频繁**
（每次进度都写），所以只写小文件、先写临时文件再替换，避免读到半截 JSON。

并发：单进程 + `BackgroundTasks` 线程，同一用户默认最多 3 个在跑（`MAX_RUNNING_PER_USER`）。
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(os.environ.get("DOC_JOBS_DIR") or "./data/doc_jobs")
MAX_RUNNING_PER_USER = 3
TERMINAL = ("done", "failed", "cancelled")

_lock = threading.Lock()


class JobError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _dir(user_id: int, job_id: str) -> Path:
    return ROOT / f"u{int(user_id)}" / job_id


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def create(user_id: int, task: str, file_ids: list[str], options: dict | None = None) -> dict:
    if not file_ids:
        raise JobError("NO_FILE", "没有指定文件（file_ids 为空）")
    running = [j for j in list_jobs(user_id, page=1, page_size=100)["items"]
               if j["status"] in ("queued", "running")]
    if len(running) >= MAX_RUNNING_PER_USER:
        raise JobError("JOB_BUSY",
                       f"同一个账号同时最多 {MAX_RUNNING_PER_USER} 个任务在跑，请等前面的完成",
                       429)

    job_id = "j_" + uuid.uuid4().hex[:12]
    meta = {
        "job_id": job_id,
        "user_id": int(user_id),
        "task": task,
        "file_ids": list(file_ids),
        "options": options or {},
        "status": "queued",
        "progress": 0,
        "stage": "排队中",
        "created_at": _now(),
        "started_at": None,
        "finished_at": None,
        "result": None,
        "outputs": [],
        "error": None,
        "cancel": False,
    }
    _write(_dir(user_id, job_id) / "job.json", meta)
    return public(meta)


def public(meta: dict) -> dict:
    return {k: meta.get(k) for k in
            ("job_id", "task", "status", "progress", "stage", "created_at",
             "started_at", "finished_at", "outputs", "error", "result")}


def load(job_id: str, user_id: int) -> dict:
    path = _dir(user_id, job_id) / "job.json"
    if not path.exists():
        raise JobError("JOB_NOT_FOUND", "任务不存在或不属于当前用户", 404)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save(meta: dict) -> None:
    _write(_dir(meta["user_id"], meta["job_id"]) / "job.json", meta)


def update(job_id: str, user_id: int, **fields) -> dict:
    with _lock:
        meta = load(job_id, user_id)
        meta.update(fields)
        save(meta)
        return meta


def list_jobs(user_id: int, *, page: int = 1, page_size: int = 20) -> dict:
    metas = []
    for path in (ROOT / f"u{int(user_id)}").glob("*/job.json"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                metas.append(json.load(f))
        except (OSError, json.JSONDecodeError):
            continue
    metas.sort(key=lambda m: m.get("created_at", ""), reverse=True)
    start = (max(1, page) - 1) * page_size
    return {"items": [public(m) for m in metas[start:start + page_size]],
            "total": len(metas), "page": page, "page_size": page_size}


def cancel(job_id: str, user_id: int) -> dict:
    with _lock:
        meta = load(job_id, user_id)
        if meta["status"] in TERMINAL:
            return public(meta)
        meta["cancel"] = True
        meta["status"] = "cancelled"
        meta["finished_at"] = _now()
        meta["stage"] = "已取消"
        save(meta)
        return public(meta)


def out_dir(meta: dict) -> Path:
    d = _dir(meta["user_id"], meta["job_id"]) / "out"
    d.mkdir(parents=True, exist_ok=True)
    return d


def output_path(meta: dict, name: str) -> Path:
    """只允许取本任务目录下的文件（防路径穿越）。"""
    safe = Path(str(name)).name
    p = _dir(meta["user_id"], meta["job_id"]) / "out" / safe
    if not p.exists():
        raise JobError("OUTPUT_NOT_FOUND", "成果文件不存在", 404)
    return p


# ── 后台执行 ───────────────────────────────────────────────────

def run(job_id: str, user_id: int) -> None:
    """BackgroundTasks 入口（同步函数 ⇒ FastAPI 丢线程池，不占事件循环）。"""
    try:
        meta = load(job_id, user_id)
    except JobError:
        return
    if meta["status"] != "queued":
        return

    update(job_id, user_id, status="running", started_at=_now(), stage="开始")

    def progress(pct: int, stage: str) -> None:
        m = load(job_id, user_id)
        if m.get("cancel"):                      # 取消：抛出去，让下面的 except 收口
            raise RuntimeError("任务已取消")
        update(job_id, user_id, progress=int(pct), stage=stage)

    try:
        meta = load(job_id, user_id)
        task = meta["task"]
        opts = meta.get("options") or {}
        file_id = meta["file_ids"][0]
        if task == "digest":
            from app.secretary import pipeline_digest as pipe
        elif task == "proofread":
            from app.secretary import pipeline_proofread as pipe
        elif task == "table_audit":
            from app.secretary import pipeline_table as pipe
        elif task == "deck":
            from app.secretary import pipeline_deck as pipe
        elif task == "doc_write":
            from app.secretary import pipeline_doc_write as pipe
        elif task == "compare":
            from app.secretary import pipeline_compare as pipe
        else:
            raise JobError("UNSUPPORTED_TASK", f"暂不支持的任务类型：{task}", 400)

        result = pipe.run(file_id, user_id, opts, progress, out_dir(meta))
        outputs = result.get("outputs") or []
        update(job_id, user_id, status="done", progress=100, stage="完成",
               finished_at=_now(), result=result,
               outputs=[{"name": n,
                         "url": f"/api/docs/jobs/{job_id}/download?name={n}"} for n in outputs])
    except JobError as exc:
        update(job_id, user_id, status="failed", finished_at=_now(),
               error=f"{exc.message}（{exc.code}）", stage="失败")
    except Exception as exc:                     # noqa: BLE001
        msg = str(exc)
        if "已取消" in msg:
            update(job_id, user_id, status="cancelled", finished_at=_now(), stage="已取消")
        else:
            update(job_id, user_id, status="failed", finished_at=_now(),
                   error=f"{type(exc).__name__}: {msg}", stage="失败")


def cleanup(job_id: str, user_id: int) -> None:
    shutil.rmtree(_dir(user_id, job_id), ignore_errors=True)

# ── 批量作业（2026-10-21，F2-5）──────────────────────────────────────
# 一次给多份文件：每份建一个独立任务（同一个 task），返回建成的与没建成的。
# 为什么要逐个建而不是一个任务塞多文件：单文件失败不影响其它文件，进度也能分开看。
# 并发上限仍按 create() 的规则（同账号最多 MAX_RUNNING_PER_USER 个在跑）。
def batch_create(user_id: int, task: str, file_ids: list[str],
                 options: dict | None = None) -> dict:
    created, skipped = [], []
    for fid in file_ids or []:
        try:
            created.append(create(user_id, task, [fid], options))
        except JobError as exc:
            skipped.append({"file_id": fid, "code": exc.code, "message": exc.message})
    return {"task": task, "created": created, "skipped": skipped,
            "total": len(file_ids or []), "ok": len(created)}
