# -*- coding: utf-8 -*-
"""权限矩阵（代码常量，替代原 4 张 RBAC 表）。

2026-10-15 单进程合并：系统只有 admin / user 两种角色（来自 HR 的
`t_employee.administrator_flag`），原 role/permission/role_permission/user_role
四张表是"为不存在的需求预留的复杂度"（源码注释自己都写着"不支持自定义角色"），
且权限点其实硬编码在各路由的 `require_permission(...)` 上——表里的 19 行
只是那份清单的数据副本。收敛为一张常量矩阵：

- `app_user.role` 字段：admin | user（登录时按 administrator_flag 同步）
- 本文件：角色 → 权限码集合

**接口层零改动**：`require_permission("doc:upload")`、`/me` 返回的 permissions、
前端 `has('user:manage')` 全部照旧，只是权限码的来源从"查表"变成"查常量"。

将来真要加第三种角色（如"审核员"）：在 ROLE_PERMISSIONS 加一组即可（几分钟）。
"""

from __future__ import annotations

# 权限码全集（原 permission 表的 19 行，按组保留注释便于对照）
ALL_PERMISSIONS: dict[str, str] = {
    # kb
    "kb:create": "创建知识库", "kb:read": "查看知识库",
    "kb:update": "修改知识库", "kb:delete": "删除知识库",
    # doc
    "doc:upload": "上传文档", "doc:read": "查看文档",
    "doc:update": "修改文档（可见性等）", "doc:delete": "删除自己上传的文档",
    "doc:tag": "设置文档标签",
    # tag
    "tag:read": "查看标签", "tag:manage": "管理标签（重命名 / 删除）",
    # search / conflict
    "search:query": "检索与问答", "conflict:read": "查看冲突记录",
    "conflict:resolve": "处理冲突",
    # 管理类
    "user:read": "查看用户", "user:manage": "管理用户",
    "audit:read": "查看审计日志",
    "system:config": "系统配置（模型档位等）", "system:scan": "全库一致性扫描",
}

ROLE_PERMISSIONS: dict[str, set[str]] = {
    # admin 直接展开为全集（不用 "*" 通配符：require_permission 与前端 has()
    # 都按具体码判断，通配符会让它们静默失效）
    "admin": set(ALL_PERMISSIONS),
    "user": {
        "kb:read", "doc:upload", "doc:read", "doc:update", "doc:delete",
        "doc:tag", "tag:read", "search:query", "conflict:read", "conflict:resolve",
    },
}

ROLE_LABELS: dict[str, str] = {"admin": "管理员", "user": "普通用户"}


def permissions_for(role: str | None) -> set[str]:
    """角色 → 权限码集合。未知角色按普通用户处理（保守失败）。"""
    return set(ROLE_PERMISSIONS.get(role or "user", ROLE_PERMISSIONS["user"]))


def has_permission(role: str | None, code: str) -> bool:
    return code in permissions_for(role)
