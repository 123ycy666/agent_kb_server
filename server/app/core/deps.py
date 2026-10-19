"""认证与权限依赖。

认证方式（单通道）
------------------
`Authorization: Bearer <JWT>` → 用户（统一员工账号登录）。

2026-10-15 单进程合并后，问数智能体与知识库同进程，检索直接以
**当前登录用户身份**进程内调用（kb_adapter），不再需要内部服务令牌。

⚠️ 2026-10-12：已移除「应用身份 API Key」通道。对外对接改为使用专用账号
   走正常登录流程拿 JWT，权限判定与审计身份完全统一到用户体系。

权限校验
--------
- `require_permission("doc:upload")` 作为依赖注入到路由，
  未授权直接 403，避免每个接口手写权限判断。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_access_token
from app.models import AppUser


@dataclass
class CurrentActor:
    """请求身份（当前登录用户）。

    2026-10-15 单进程合并后只剩用户一种身份（内部服务令牌已删除，
    问数智能体与知识库同进程，检索直接以登录用户身份进程内调用）。
    """

    type: str = "user"                 # user（service 类型已移除，兼容保留）
    user_id: int | None = None
    username: str = ""
    display_name: str | None = None
    role_ids: list[int] = field(default_factory=list)
    role_codes: list[str] = field(default_factory=list)
    permissions: set[str] = field(default_factory=set)

    @property
    def is_admin(self) -> bool:
        return "admin" in self.role_codes

    @property
    def is_service(self) -> bool:
        return self.type == "service"


def _load_user_permissions(db: Session, user_id: int) -> tuple[list[int], list[str], set[str]]:
    """按 app_user.role 字段取角色与权限码（2026-10-15 RBAC 收敛）。

    角色只有 admin / user 两种，权限码集合来自 `app/core/rbac_config.py`
    的常量矩阵 —— 不再查 role/permission/user_role 三张表（已删）。
    """
    from app.core.rbac_config import permissions_for

    user = db.get(AppUser, user_id)
    role = (user.role if user is not None else None) or "user"
    # role_ids 恒为 []（无角色表了）；保留返回值形状供 CurrentActor 使用
    return [], [role], permissions_for(role)


def _authenticate_by_jwt(db: Session, token: str) -> CurrentActor:
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_TOKEN", "message": "登录凭证无效或已过期"},
        )

    try:
        user_id = int(payload.get("sub", ""))
    except (TypeError, ValueError):
        user_id = 0

    user = db.get(AppUser, user_id)
    if user is None or user.status != "active":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "USER_NOT_FOUND", "message": "用户不存在或已停用"},
        )

    role_ids, role_codes, codes = _load_user_permissions(db, user.id)
    return CurrentActor(
        type="user",
        user_id=user.id,
        username=user.username,
        display_name=user.display_name or user.username,
        role_ids=role_ids,
        role_codes=role_codes,
        permissions=codes,
    )


def get_current_actor(
    request: Request,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> CurrentActor:
    """解析请求身份（JWT）。"""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "NOT_AUTHENTICATED", "message": "缺少访问凭证"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization[7:].strip()
    actor = _authenticate_by_jwt(db, token)

    request.state.actor = actor
    return actor


def require_permission(*codes: str):
    """权限点校验依赖工厂。

    用法：``Depends(require_permission("kb:create"))``（满足任一即可）
    """

    def _checker(actor: CurrentActor = Depends(get_current_actor)) -> CurrentActor:
        if not actor.permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "NO_PERMISSION", "message": "当前账号未分配任何权限"},
            )
        if not any(c in actor.permissions for c in codes):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "NO_PERMISSION",
                    "message": f"缺少所需权限：{' 或 '.join(codes)}",
                    "required": list(codes),
                },
            )
        return actor

    return _checker


def get_client_ip(request: Request) -> str | None:
    """取客户端 IP，兼容反向代理。"""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None
