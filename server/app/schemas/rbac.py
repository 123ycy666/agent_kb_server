"""权限相关 Schema（角色与权限点）。

⚠️ 已大幅简化：

- 本系统只有 `admin` / `user` **两种固定角色**，不支持自定义，
  因此没有 `RoleCreate` / `RoleUpdate`。
- 文档级 ACL 已移除 —— 可见性只有 `public` / `private` 两种，
  直接存在 `Document.visibility` 上（见 app/models/document.py）。
"""

from __future__ import annotations

from datetime import datetime

from app.schemas.common import ORMModel


class PermissionOut(ORMModel):
    id: int
    code: str
    description: str | None = None
    group_name: str


class RoleOut(ORMModel):
    id: int
    name: str
    description: str | None = None
    is_builtin: bool
    # 2026-10-15 起角色来自代码常量（无角色表），created_at 可选
    created_at: datetime | None = None
    permission_codes: list[str] = []
    user_count: int = 0
