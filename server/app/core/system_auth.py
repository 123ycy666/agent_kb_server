# -*- coding: utf-8 -*-
"""系统账号令牌：`X-Internal-Token`（IF-SYS-001）。

令牌挂在**服务配置**上（`.env` 的 INTERNAL_SERVICE_TOKEN），不进库、不随现场切换：
外部系统（sga、定时预警任务）靠它调用 `/api/alerts/*`、`/api/internal/*`
以及换 token 的入口，全程不参与页面登录。
"""
from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, status

from app.core.config import settings

SOURCE = "service-env"


def token() -> str:
    """"当前生效的系统令牌（空 = 未配置，本地开发不校验）。"""
    return (settings.internal_service_token or "").strip()


def check(got: str | None) -> bool:
    """纯判断：给请求头里的值，回合不合法。未配置时恒 True。"""
    want = token()
    if not want:
        return True
    return hmac.compare_digest(str(got or ""), want)


def require_system_token(
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
) -> str:
    """FastAPI 依赖：给需要系统账号的接口用。

    ⚠️ 与 `get_current_actor` 是两条**互斥**的通道：系统账号没有页面身份，
       也不需要 `app_user` 里的行 —— 它代表「服务对服务」的调用方。
    """
    if not check(x_internal_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "BAD_INTERNAL_TOKEN", "message": "系统令牌不正确"},
        )
    return SOURCE
