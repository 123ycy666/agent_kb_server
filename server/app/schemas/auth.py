"""认证与用户 Schema。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class SsoLoginRequest(BaseModel):
    """第三方系统免密换 token（2026-10-19 为 sga 前端直连新增）。

    只传 login_name（HR 工号登录名），服务端确认员工在职后签发本平台 JWT。
    """

    username: str = Field(min_length=1, max_length=64)


class UserBrief(ORMModel):
    id: int
    username: str
    display_name: str | None = None


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserBrief
    roles: list[str] = []
    permissions: list[str] = []


class MeResponse(BaseModel):
    id: int
    username: str
    display_name: str | None = None
    actor_type: str
    roles: list[str] = []
    permissions: list[str] = []
    app_id: str | None = None


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=6, max_length=128)
    display_name: str | None = None
    email: str | None = None
    role_ids: list[int] = []


class UserUpdate(BaseModel):
    display_name: str | None = None
    email: str | None = None
    status: str | None = None
    role_ids: list[int] | None = None


class UserOut(ORMModel):
    """用户视图。

    2026-10-14 统一用户表后 = **员工视图**：权威数据来自 MySQL t_employee，
    投影信息（id / status / roles）来自 PG app_user。projected=False 表示
    该员工尚未登录过（没有投影行）。
    """

    id: int = 0
    username: str
    display_name: str | None = None
    email: str | None = None
    status: str = "active"
    external_id: str | None = None
    synced_at: datetime | None = None
    created_at: datetime | None = None
    role_ids: list[int] = []
    role_names: list[str] = []
    # ── t_employee 员工字段（HR 权威，只读展示）──────────────
    employee_id: int | None = None
    actual_name: str | None = None
    phone: str | None = None
    department_id: int | None = None
    administrator_flag: bool = False
    disabled_flag: bool = False
    deleted_flag: bool = False
    projected: bool = False


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(min_length=6, max_length=128)
