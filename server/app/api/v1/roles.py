"""角色与用户管理。

## 权限模型（刻意保持极简）

系统**只有两种角色**，不支持自定义：

- `admin`：管理员，可查看并处理全部文档与知识库（**检索仍受公开/私有约束**）
- `user` ：普通用户，可上传文档、检索公开文档与自己上传的文档

因此本模块**不提供**「创建角色 / 改角色权限」接口 —— 那是过度设计。
只提供两类能力：

1. 只读查看角色与权限点字典（返回内容来自 `app/core/rbac_config.py` 常量矩阵，
   2026-10-15 起不再查 role/permission/user_role 三张表——已删）
2. 员工列表（只读）：权威数据来自统一用户表 dlj_data.t_employee

## 2026-10-15 统一用户表改造

用户管理已**收敛为只读员工列表**：

- 员工新增 / 停用 / 删除 → 全部在 HR 系统（dlj_data.t_employee）操作
- 角色由 HR 的 `administrator_flag` 决定（1=管理员，0=普通用户），
  每次登录自动同步到 `app_user.role`；本地不提供改角色入口
- 本服务只展示：员工基本信息 + 是否已有本地投影 + 当前角色
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.database import get_db, get_mysql_db
from app.core.deps import CurrentActor, get_current_actor, require_permission
from app.core.rbac_config import (
    ALL_PERMISSIONS,
    ROLE_LABELS,
    ROLE_PERMISSIONS,
)
from app.models import AppUser
from app.schemas.auth import UserOut
from app.schemas.rbac import PermissionOut, RoleOut

router = APIRouter(tags=["权限管理"])


# ══════════════════════════════════════════════════════════════
#  权限点与角色（只读，内容来自代码常量）
# ══════════════════════════════════════════════════════════════

@router.get("/permissions", response_model=list[PermissionOut], summary="权限点字典（代码常量）")
def list_permissions(
    _: CurrentActor = Depends(get_current_actor), db: Session = Depends(get_db)
):
    """权限点字典。2026-10-15 起来自 rbac_config.ALL_PERMISSIONS 常量（原 permission 表已删）。"""
    out = []
    for i, (code, desc) in enumerate(ALL_PERMISSIONS.items(), start=1):
        out.append(PermissionOut(id=i, code=code, description=desc,
                                 group_name=code.split(":", 1)[0]))
    return out


def _role_out(db: Session, name: str) -> RoleOut:
    codes = ROLE_PERMISSIONS.get(name, set())
    user_count = int(
        db.execute(select(func.count(AppUser.id)).where(AppUser.role == name)).scalar_one()
    )
    out = RoleOut(id=1 if name == "admin" else 2, name=name,
                  description=ROLE_LABELS.get(name, name), is_builtin=True)
    out.permission_codes = sorted(codes) if codes != {"*"} else sorted(ALL_PERMISSIONS)
    out.user_count = user_count
    return out


@router.get("/roles", response_model=list[RoleOut], summary="角色列表（固定两种）")
def list_roles(
    _: CurrentActor = Depends(get_current_actor), db: Session = Depends(get_db)
):
    return [_role_out(db, "admin"), _role_out(db, "user")]


# ══════════════════════════════════════════════════════════════
#  员工列表（统一用户表，只读）
# ══════════════════════════════════════════════════════════════

@router.get(
    "/users",
    response_model=list[UserOut],
    summary="员工列表（统一用户表 t_employee，只读）",
    dependencies=[Depends(require_permission("user:read"))],
)
def list_users(
    _: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
    mysql_db: Session = Depends(get_mysql_db),
):
    """员工列表 = MySQL t_employee（权威）+ PG app_user 投影（登录后生成）。

    投影行按 username 关联；未登录过的员工 projected=False、id=0。
    """
    emp_rows = mysql_db.execute(
        text(
            "SELECT employee_id, login_name, actual_name, email, phone, department_id, "
            "administrator_flag, disabled_flag, deleted_flag "
            "FROM t_employee ORDER BY employee_id"
        )
    ).mappings().all()

    # 投影行一次查出来，避免 N+1
    projections = {u.username: u for u in db.execute(select(AppUser)).scalars()}

    out: list[UserOut] = []
    for row in emp_rows:
        proj = projections.get(row["login_name"])
        if proj is not None:
            out.append(
                UserOut(
                    id=proj.id,
                    username=proj.username,
                    display_name=proj.display_name,
                    email=proj.email,
                    status=proj.status,
                    external_id=proj.external_id,
                    synced_at=proj.synced_at,
                    created_at=proj.created_at,
                    role_ids=[],
                    role_names=[proj.role],
                    employee_id=int(row["employee_id"]),
                    actual_name=row["actual_name"],
                    phone=row["phone"],
                    department_id=row["department_id"],
                    administrator_flag=bool(row["administrator_flag"]),
                    disabled_flag=bool(row["disabled_flag"]),
                    deleted_flag=bool(row["deleted_flag"]),
                    projected=True,
                )
            )
        else:
            out.append(
                UserOut(
                    id=0,
                    username=row["login_name"],
                    display_name=row["actual_name"],
                    email=row["email"],
                    status="not_projected",
                    employee_id=int(row["employee_id"]),
                    actual_name=row["actual_name"],
                    phone=row["phone"],
                    department_id=row["department_id"],
                    administrator_flag=bool(row["administrator_flag"]),
                    disabled_flag=bool(row["disabled_flag"]),
                    deleted_flag=bool(row["deleted_flag"]),
                    projected=False,
                )
            )
    return out
