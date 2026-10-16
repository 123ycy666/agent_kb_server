"""平台业务：异步任务。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import CurrentActor
from app.models import AsyncTask


class PlatformError(RuntimeError):
    """平台操作失败。"""


# ══════════════════════════════════════════════════════════════
#  异步任务
# ══════════════════════════════════════════════════════════════


def create_task(
    db: Session,
    *,
    task_type: str,
    ref_id: int | None = None,
    kb_id: int | None = None,
    actor: CurrentActor | None = None,
    status: str = "pending",
) -> AsyncTask:
    task = AsyncTask(
        id=uuid.uuid4(),
        task_type=task_type,
        ref_id=ref_id,
        kb_id=kb_id,
        status=status,
        progress=0,
        created_by=actor.user_id if actor else None,
        created_by_app=None,
        created_at=datetime.now(UTC),
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def update_task(
    db: Session,
    *,
    task: AsyncTask,
    status: str | None = None,
    progress: int | None = None,
    stage: str | None = None,
    error_msg: str | None = None,
    result: dict | None = None,
    finished: bool = False,
) -> AsyncTask:
    if status is not None:
        task.status = status
    if progress is not None:
        task.progress = progress
    if stage is not None:
        task.stage = stage
    if error_msg is not None:
        task.error_msg = error_msg
    if result is not None:
        task.result = result
    if status == "running" and task.started_at is None:
        task.started_at = datetime.now(UTC)
    if finished:
        task.finished_at = datetime.now(UTC)
        if task.progress < 100 and task.status in ("done", "failed"):
            task.progress = 100
    db.commit()
    db.refresh(task)
    return task


def get_task(db: Session, task_id: uuid.UUID) -> AsyncTask | None:
    return db.get(AsyncTask, task_id)


def list_tasks(
    db: Session,
    *,
    status: str | None = None,
    task_type: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[AsyncTask], int]:
    stmt = select(AsyncTask)
    count_stmt = select(func.count(AsyncTask.id))

    if status:
        stmt = stmt.where(AsyncTask.status == status)
        count_stmt = count_stmt.where(AsyncTask.status == status)
    if task_type:
        stmt = stmt.where(AsyncTask.task_type == task_type)
        count_stmt = count_stmt.where(AsyncTask.task_type == task_type)

    total = int(db.execute(count_stmt).scalar_one())
    rows = (
        db.execute(
            stmt.order_by(AsyncTask.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return list(rows), total


def build_task_result(
    *,
    doc_id: int,
    chunk_count: int,
    conflicts: list[dict] | None = None,
    note: str | None = None,
    extra: dict | None = None,
) -> dict:
    """构造任务结果体（上传任务的返回结构）。"""
    payload: dict = {"doc_id": doc_id, "chunk_count": chunk_count}
    if conflicts:
        payload["conflicts"] = conflicts
    if note:
        payload["note"] = note
    if extra:
        payload.update(extra)
    return payload
