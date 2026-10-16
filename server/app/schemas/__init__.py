"""Schema 层统一出口。

分层约定
--------
- xxxCreate / xxxUpdate  → 请求体
- xxxOut                 → 响应体（支持从 ORM 直接构造）
- xxxRequest / Response  → 复合操作
"""

from app.schemas import (
    auth,
    common,
    conflict,
    document,
    kb,
    platform,
    providers,
    rbac,
    search,
    tag,
)

__all__ = [
    "auth",
    "common",
    "conflict",
    "document",
    "kb",
    "platform",
    "providers",
    "rbac",
    "search",
    "tag",
]
