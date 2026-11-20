# -*- coding: utf-8 -*-
"""每日预警接口（`/api/alerts/*`，系统账号鉴权）。

外部定时任务（上游业务系统 / 推送服务）拿不到用户 JWT，用 `X-Internal-Token` 调用。
路径与载荷保持原样（IF-API-001）：POST /api/alerts/daily {account, today?, when?, limit?}。
"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

import hmac

from app.core import system_auth
from app.core.system_auth import require_system_token

router = APIRouter(prefix="/alerts", tags=["预警（系统账号）"])


def _jr(data, status_code: int = 200):
    return JSONResponse(content=data, status_code=status_code)


@router.get("/health", summary="预警接口探活（系统账号）")
def alerts_health(source: str = Depends(require_system_token)):
    return {"ok": True, "auth": source, "checked_at": time.strftime("%Y-%m-%d %H:%M:%S")}


@router.post("/daily", summary="每日预警：按账号返回今日该办的事（系统账号）")
async def alerts_daily(request: Request):
    # ⚠️ 这一条的鉴权**不用依赖**：原来的对外契约是「令牌不对 → 401 + {"status":"unauthorized"}」，
    #    外部定时任务按 status 判，改成 FastAPI 的 detail 结构会破坏对接（IF-API-001）。
    want = system_auth.token()
    got = request.headers.get("X-Internal-Token") or ""
    if want and not hmac.compare_digest(got, want):
        return _jr({"status": "unauthorized", "error": "内部令牌不正确"}, 401)
    source = system_auth.SOURCE
    try:
        raw = await request.body()
        data = json.loads(raw) if raw else {}
    except Exception:                                 # noqa: BLE001
        data = {}
    data = data if isinstance(data, dict) else {}
    acct = (data.get("account") or data.get("uid") or "").strip()
    try:
        from app.secretary import alerts as alerts_mod

        r = await run_in_threadpool(
            alerts_mod.daily, acct,
            (data.get("today") or data.get("business_date") or "").strip() or None,
            (data.get("when") or data.get("triggered_at") or "").strip() or None,
            int(data.get("limit") or 10),
        )
    except Exception as exc:                          # noqa: BLE001
        r = {"status": "unknown", "account": acct, "alerts": [],
             "error": "%s: %s" % (type(exc).__name__, str(exc)[:200]),
             "checked_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    r["auth"] = source
    return _jr(r)
