"""审计日志接口。"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import CurrentActor, get_current_actor, require_permission
from app.models import AuditLog
from app.schemas.common import Page
from app.schemas.platform import AuditActionOut, AuditLogOut, AuditStats
from app.services import audit

router = APIRouter(prefix="/audit-logs", tags=["审计"])


@router.get(
    "",
    response_model=Page[AuditLogOut],
    summary="审计日志查询",
    dependencies=[Depends(require_permission("audit:read"))],
)
def list_audit_logs(
    actor_type: str | None = Query(None, pattern="^(user|app|system)$"),
    actor_id: int | None = Query(None),
    action: str | None = Query(None),
    resource_type: str | None = Query(None),
    resource_id: int | None = Query(None),
    keyword: str | None = Query(None, description="在 detail 中做模糊匹配"),
    start: datetime | None = Query(None, description="起始时间（含）"),
    end: datetime | None = Query(None, description="结束时间（含）"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    _: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    from sqlalchemy import TEXT, cast, func, or_

    conditions = []
    if actor_type:
        conditions.append(AuditLog.actor_type == actor_type)
    if actor_id is not None:
        conditions.append(AuditLog.actor_id == actor_id)
    if action:
        conditions.append(AuditLog.action == action)
    if resource_type:
        conditions.append(AuditLog.resource_type == resource_type)
    if resource_id is not None:
        conditions.append(AuditLog.resource_id == resource_id)
    if start is not None:
        conditions.append(AuditLog.created_at >= start)
    if end is not None:
        conditions.append(AuditLog.created_at <= end)
    if keyword:
        conditions.append(
            or_(
                AuditLog.resource_name.ilike(f"%{keyword}%"),
                AuditLog.action.ilike(f"%{keyword}%"),
                AuditLog.actor_name.ilike(f"%{keyword}%"),
                cast(AuditLog.detail, TEXT).ilike(f"%{keyword}%"),
            )
        )

    stmt = select(AuditLog)
    count_stmt = select(func.count(AuditLog.id))
    for cond in conditions:
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)

    total = int(db.execute(count_stmt).scalar_one())
    rows = (
        db.execute(
            stmt.order_by(AuditLog.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )

    return Page[AuditLogOut](
        items=[AuditLogOut.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/stats",
    response_model=AuditStats,
    summary="审计概览统计",
    dependencies=[Depends(require_permission("audit:read"))],
)
def audit_stats(
    _: CurrentActor = Depends(get_current_actor), db: Session = Depends(get_db)
):
    return AuditStats(**audit.get_audit_stats(db))


@router.get(
    "/actions",
    response_model=list[AuditActionOut],
    summary="可筛选的动作码目录",
)
def audit_actions(_: CurrentActor = Depends(get_current_actor)):
    return [AuditActionOut(**a) for a in audit.get_action_catalog()]
