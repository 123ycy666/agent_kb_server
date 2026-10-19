"""标签接口（极简）。

标签 = **自由字符串**，不是结构化元数据。这里只保留：

- 词表查询（前端下拉复用已用过的标签）
- 管理员手工增 / 改 / 删 / 重命名
- 检索页的筛选可选项

普通用户上传文档时**直接写标签名**即可，系统自动登记进词表 ——
不需要「先建标签维度、再选值」那套繁琐流程。

权限分工
--------
- 查看词表：所有登录用户
- 增 / 改 / 删：仅管理员（`tag:manage`）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import CurrentActor, get_current_actor, require_permission
from app.models import Tag
from app.schemas.common import OkResponse, Page
from app.schemas.tag import (
    TagCreate,
    TagOptionOut,
    TagOptionsResponse,
    TagOut,
    TagRenameRequest,
    TagUpdate,
)
from app.services import audit, tag_governance as gov

router = APIRouter(prefix="/tags", tags=["标签"])


@router.get("", response_model=Page[TagOut], summary="标签词表")
def list_tags(
    keyword: str | None = Query(None, description="按名称模糊搜索"),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    """列出用过的标签，按使用次数倒序（常用标签靠前）。"""
    stmt = select(Tag)
    count_stmt = select(func.count(Tag.id))
    if keyword:
        cond = Tag.name.ilike(f"%{keyword.strip()}%")
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)

    total = int(db.execute(count_stmt).scalar_one())
    rows = (
        db.execute(
            stmt.order_by(Tag.usage_count.desc(), Tag.name)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return Page[TagOut](
        items=[TagOut.model_validate(t) for t in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "",
    response_model=TagOut,
    summary="新增标签（管理员）",
    dependencies=[Depends(require_permission("tag:manage"))],
)
def create_tag(
    payload: TagCreate,
    request: Request,
    actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    try:
        name = gov.normalize_tag(payload.name)
    except gov.TagValidationError as exc:
        raise HTTPException(
            status_code=400, detail={"code": "INVALID_TAG", "message": str(exc)}
        ) from exc

    existing = db.execute(select(Tag).where(Tag.name == name)).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail={"code": "TAG_EXISTS", "message": f"标签「{name}」已存在"},
        )

    tag = Tag(
        name=name,
        description=payload.description,
        usage_count=0,
        created_by=actor.user_id,
    )
    db.add(tag)
    db.commit()
    db.refresh(tag)

    audit.log_action(
        db,
        actor=actor,
        action=audit.AuditAction.TAG_CREATE,
        resource_type="tag",
        resource_id=tag.id,
        resource_name=tag.name,
        request=request,
        commit=True,
    )
    return TagOut.model_validate(tag)


@router.patch(
    "/{tag_id}",
    response_model=TagOut,
    summary="修改标签说明（管理员）",
    dependencies=[Depends(require_permission("tag:manage"))],
)
def update_tag(
    tag_id: int,
    payload: TagUpdate,
    actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    tag = db.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(
            status_code=404, detail={"code": "TAG_NOT_FOUND", "message": "标签不存在"}
        )
    if payload.description is not None:
        tag.description = payload.description
    db.commit()
    db.refresh(tag)
    return TagOut.model_validate(tag)


@router.post(
    "/rename",
    response_model=OkResponse,
    summary="重命名标签（同步改写全部文档）",
    dependencies=[Depends(require_permission("tag:manage"))],
)
def rename_tag(
    payload: TagRenameRequest,
    request: Request,
    actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    """重命名会同步改写所有文档与切片上的标签值，避免出现两个近似标签。"""
    try:
        affected = gov.rename_tag(db, old_name=payload.old_name, new_name=payload.new_name)
    except gov.TagValidationError as exc:
        raise HTTPException(
            status_code=400, detail={"code": "INVALID_TAG", "message": str(exc)}
        ) from exc

    audit.log_action(
        db,
        actor=actor,
        action=audit.AuditAction.TAG_UPDATE,
        resource_type="tag",
        resource_name=payload.new_name,
        detail={
            "from": payload.old_name,
            "to": payload.new_name,
            "affected_docs": affected,
        },
        request=request,
        commit=True,
    )
    return OkResponse(
        message=(
            f"已将「{payload.old_name}」重命名为「{payload.new_name}」，"
            f"涉及 {affected} 份文档"
        )
    )


@router.delete(
    "/{name}",
    response_model=OkResponse,
    summary="删除标签（管理员，会从所有文档摘除）",
    dependencies=[Depends(require_permission("tag:manage"))],
)
def delete_tag(
    name: str,
    request: Request,
    actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    """删除词表条目，并把该标签从所有文档与切片上摘掉。

    ⚠️ 只删标签，不动文档内容本身。
    """
    try:
        affected = gov.delete_tag(db, name=name)
    except gov.TagValidationError as exc:
        raise HTTPException(
            status_code=400, detail={"code": "INVALID_TAG", "message": str(exc)}
        ) from exc

    audit.log_action(
        db,
        actor=actor,
        action=audit.AuditAction.TAG_DEPRECATE,
        resource_type="tag",
        resource_name=name,
        detail={"affected_docs": affected},
        request=request,
        commit=True,
    )
    return OkResponse(message=f"已删除标签「{name}」，涉及 {affected} 份文档")


@router.get(
    "/options",
    response_model=TagOptionsResponse,
    summary="检索页的标签筛选可选项",
)
def tag_options(
    actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    """返回词表中已用过的标签，供检索页渲染筛选器。"""
    rows = gov.list_vocabulary(db, limit=200)
    return TagOptionsResponse(
        options=[TagOptionOut(name=t.name, usage_count=t.usage_count) for t in rows]
    )

