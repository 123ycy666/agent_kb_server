"""文档入库任务（解析 → 切片 → 向量化 → 冲突检测 → 写回任务状态）。

2026-10-15 单进程合并：由 Celery 改为 FastAPI BackgroundTasks 派发
（去 Redis/Celery 依赖，进程重启会丢未完成任务，但任务表有状态可重跑）。

执行体从 ``documents.py`` 的原 ``_run_ingest_task`` 整体搬来，原因：
- 后台执行体里没有 FastAPI 依赖注入，拿不到 actor，必须在请求内把身份快照为
  **安全字典**（无 ``set`` / 无 ORM 对象，可 JSON 序列化）传来，这里再重建；
- 自开 DB session（请求作用域 session 在响应返回时已关闭）；
- 异常绝不外抛，所有错误写进 ``task.error_msg``，避免影响已返回的 202 响应；
- 冲突检测失败不能拖垮入库（文档已入库成功，只是少一次比对）。
"""
from __future__ import annotations

import logging
import threading

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.deps import CurrentActor
from app.models import AsyncTask, KnowledgeBase
from app.services import audit, platform as platform_service
from app.services.conflict import detect_conflicts
from app.services.ingestion import DuplicateDocumentError, IngestError, ingest_document

logger = logging.getLogger(__name__)


def _rebuild_actor(d: dict) -> CurrentActor:
    """把请求内快照的安全字典重建为 CurrentActor（不重新鉴权）。"""
    return CurrentActor(
        type=d.get("type", "user"),
        user_id=d.get("user_id"),
        username=d.get("username"),
        display_name=d.get("display_name"),
        role_ids=list(d.get("role_ids", []) or []),
        role_codes=list(d.get("role_codes", []) or []),
        permissions=set(d.get("permissions", []) or []),
    )


def _mark_task_failed(db, task_id: str, message: str) -> None:
    """把任务标记为失败。独立 session 内调用，故先回滚再取任务。"""
    db.rollback()
    task = db.get(AsyncTask, task_id)
    if task is not None:
        platform_service.update_task(
            db, task=task, status="failed", error_msg=message, finished=True
        )


def ingest_document_task(
    *,
    task_id: str,
    kb_id: int,
    filename: str,
    storage_path: str,
    is_public: bool,
    tag_list: list[str],
    actor_dict: dict,
) -> None:
    """后台执行体：解析 → 切片 → 向量化 → 入库 → 冲突检测 → 回写任务状态。"""
    db = SessionLocal()
    try:
        task = db.get(AsyncTask, task_id)
        if task is not None and task.status != "done":
            platform_service.update_task(db, task=task, status="running")
        kb = db.get(KnowledgeBase, kb_id)
        if kb is None:
            _mark_task_failed(db, task_id, "知识库不存在")
            return

        from pathlib import Path

        try:
            content = Path(storage_path).read_bytes()
        except OSError as exc:
            _mark_task_failed(db, task_id, f"读取落盘文件失败：{exc}")
            return

        actor = _rebuild_actor(actor_dict)
        result = ingest_document(
            db,
            kb=kb,
            filename=filename,
            content=content,
            actor=actor,
            is_public=is_public,
            tags=tag_list,
            storage_path=storage_path,
        )

        # ── 第一段：向量化入库完成 → 立即标 done，文档立刻可检索 ─────
        # ⚠️ 2026-10-15 两段式：冲突检测要真调 LLM（实测单次 8~20s），
        #    原先它挡在「上传完成」之前 —— 用户要等一两分钟才看到文档，
        #    体感就是「上传没有异步」。现在解析+向量化一结束就置 done，
        #    冲突结论随后写回**同一条任务**（状态仍为 done，前端刷新可见）。
        base_extra = {
            "version_no": result.version_no,
            "is_new_version": result.is_new_version,
            "chunk_stats": result.chunk_stats,
            "parse_meta": result.parse_meta,
        }
        notes = list(result.warnings)
        platform_service.update_task(
            db,
            task=task,
            status="done",
            progress=100,
            stage="ready",
            result=platform_service.build_task_result(
                doc_id=result.doc_id,
                chunk_count=result.chunk_count,
                note="；".join(notes) if notes else None,
                extra={
                    **base_extra,
                    "conflict_status": "checking",
                    "conflict_count": 0,
                    "conflict_docs": [],
                },
            ),
            finished=True,
        )

        if not settings.auto_conflict_check:
            return

        # ── 第二段（后台继续跑）：冲突检测 → 把结论写回同一条任务 ────
        conflict_count = 0
        conflict_docs: list[str] = []
        conflict_status = "done"
        try:
            det = detect_conflicts(
                db,
                actor=actor,
                doc_id=result.doc_id,
                max_llm_calls=settings.conflict_max_llm_calls,
            )
            conflict_count = len(det.conflicts)
            conflict_docs = sorted(
                {c.get("old_doc_name") or "" for c in det.conflicts} - {""}
            )
            notes += list(det.warnings)
        except Exception as exc:  # noqa: BLE001
            conflict_status = "failed"
            notes.append(f"冲突检测未完成：{exc}")
            logger.warning("上传后冲突检测失败 doc=%s：%s", result.doc_id, exc)

        platform_service.update_task(
            db,
            task=task,
            status="done",
            result=platform_service.build_task_result(
                doc_id=result.doc_id,
                chunk_count=result.chunk_count,
                note="；".join(notes) if notes else None,
                extra={
                    **base_extra,
                    "conflict_status": conflict_status,
                    "conflict_count": conflict_count,
                    "conflict_docs": conflict_docs,
                },
            ),
        )
    except (DuplicateDocumentError, IngestError) as exc:
        _mark_task_failed(db, task_id, str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("后台入库任务异常 task=%s：%s", task_id, exc)
        _mark_task_failed(db, task_id, f"入库异常：{exc}")
    finally:
        db.close()


def enqueue_ingest(background_tasks, *, task_id: str, kb_id: int, filename: str,
                   storage_path: str, is_public: bool, tag_list: list[str],
                   actor_dict: dict) -> None:
    """把入库任务派发给 FastAPI BackgroundTasks。

    与旧 Celery `.delay()` 的差别：任务跑在 API 进程的线程池里，
    不跨进程、不需要 Redis；进程重启则丢失（任务表停留 running，
    上传接口会提示重新上传）。
    """
    background_tasks.add_task(
        ingest_document_task,
        task_id=task_id, kb_id=kb_id, filename=filename, storage_path=storage_path,
        is_public=is_public, tag_list=tag_list, actor_dict=actor_dict,
    )
