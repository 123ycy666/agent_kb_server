"""认证接口：登录、当前身份。

2026-10-14 统一用户表改造：登录认证走 MySQL `dlj_data.t_employee`
（argon2id 密码，HR 系统维护），通过后同步进 PG app_user 投影表并挂角色。
本系统不存员工密码，也不提供改密码（见 change-password）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db, get_mysql_db
from app.core.deps import CurrentActor, get_current_actor
from app.core.security import create_access_token
from app.models import AppUser
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    MeResponse,
    UserBrief,
)
from app.schemas.common import OkResponse
from app.services import audit, employee

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/login", response_model=LoginResponse, summary="登录（统一员工账号）")
def login(
    payload: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
    mysql_db: Session = Depends(get_mysql_db),
):
    # ① MySQL 员工认证（argon2id；用户不存在/密码错/已停用统一报错，防账号枚举）
    emp = employee.authenticate(mysql_db, payload.username, payload.password)
    if emp is None:
        audit.log_action(
            db,
            actor=None,
            action=audit.AuditAction.LOGIN_FAILED,
            resource_type="user",
            detail={"username": payload.username},
            result="failed",
            request=request,
            commit=True,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "BAD_CREDENTIALS", "message": "用户名或密码错误"},
        )

    # ② 同步投影 + 角色对齐（administrator_flag=1 → admin，否则 user）
    user = employee.sync_projection(db, emp)

    # 角色与权限码：按 app_user.role 取常量矩阵（2026-10-15 RBAC 收敛，去 4 张表）
    from app.core.rbac_config import permissions_for

    token = create_access_token(user.id, extra={"username": user.username})

    audit.log_action(
        db,
        actor=CurrentActor(type="user", user_id=user.id, username=user.username),
        action=audit.AuditAction.LOGIN,
        resource_type="user",
        resource_id=user.id,
        request=request,
        commit=True,
    )

    return LoginResponse(
        access_token=token,
        expires_in=settings.access_token_expire_minutes * 60,
        user=UserBrief.model_validate(user),
        roles=[user.role],
        permissions=sorted(permissions_for(user.role)),
    )


@router.get("/me", response_model=MeResponse, summary="当前身份与权限")
def me(actor: CurrentActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    role_names = list(actor.role_codes)

    return MeResponse(
        id=actor.user_id or 0,
        username=actor.username or "unknown",
        display_name=actor.display_name,
        actor_type=actor.type,
        roles=role_names,
        permissions=sorted(actor.permissions),
        # 兼容保留：应用身份已移除，恒为 None
        app_id=None,
    )


@router.post("/change-password", response_model=OkResponse, summary="修改密码（已停用）")
def change_password(
    payload: ChangePasswordRequest,
    actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    """统一用户表后本系统不再改密码。

    密码权威在 HR 系统（dlj_data.t_employee，argon2id，只读），
    本系统改不了也不该改 —— 改了会造成两边不一致。
    """
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "code": "PASSWORD_MANAGED_BY_HR",
            "message": "密码由人力资源系统统一管理，请通过 HR 系统修改",
        },
    )
