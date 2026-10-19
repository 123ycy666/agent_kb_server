"""异步任务接口。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import CurrentActor, get_current_actor
from app.schemas.common import Page
from app.schemas.platform import TaskOut
from app.services import platform as platform_service

router = APIRouter(prefix="/tasks", tags=["任务"])


@router.get("", response_model=Page[TaskOut], summary="任务列表")
def list_tasks(
    status: str | None = Query(None, pattern="^(pending|running|done|failed|conflict_pending)$"),
    task_type: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    rows, total = platform_service.list_tasks(
        db, status=status, task_type=task_type, page=page, page_size=page_size
    )
    return Page[TaskOut](
        items=[TaskOut.model_validate(t) for t in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{task_id}", response_model=TaskOut, summary="任务详情（上传后轮询此接口）")
def get_task(
    task_id: uuid.UUID,
    actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    task = platform_service.get_task(db, task_id)
    if task is None:
        raise HTTPException(
            status_code=404, detail={"code": "TASK_NOT_FOUND", "message": "任务不存在"}
        )
    return TaskOut.model_validate(task)
