"""用户表（统一用户表的本地投影）。

2026-10-15 单进程合并 + RBAC 收敛：
- 账号权威源 = MySQL `dlj_data.t_employee`（HR 系统，只读），本表只是**投影**
  （登录时自动 upsert，external_id = t_employee.employee_id）
- 角色从 4 张 RBAC 表（role/permission/role_permission/user_role）收敛为
  **`role` 一个字段**（admin | user，按 HR 的 administrator_flag 同步）；
  权限码集合见 `app/core/rbac_config.py` 的常量矩阵
- 历史数据（文档 owner_id / 审计 user_id）通过 username 对齐复用旧行，不受影响

⚠️ 文档级 ACL 已移除：可见性只有 public / private，判定在
   `app/services/permission.py`（唯一出口）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.mixins import TimestampMixin


class AppUser(Base, TimestampMixin):
    """用户投影（登录时从 t_employee 同步）。"""

    __tablename__ = "app_user"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # 统一用户表主键（t_employee.employee_id）
    external_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    display_name: Mapped[str | None] = mapped_column(String(64))
    # 密码不在本系统校验（HR 的 argon2id），此列仅为兼容保留，恒为空
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    email: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    # 角色：admin | user（按 t_employee.administrator_flag 同步）
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<AppUser {self.username} role={self.role}>"
