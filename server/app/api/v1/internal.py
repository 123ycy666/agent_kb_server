# -*- coding: utf-8 -*-
"""内部接口（`/api/internal/*`，系统账号鉴权）。

系统账号能看到的东西：这个实例现在是哪个现场、库和模型通不通、系统令牌配没配。
**不含任何密钥、口令** —— 它给运维和对接方排障用，不是登录接口。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core import site
from app.core.system_auth import require_system_token

router = APIRouter(prefix="/internal", tags=["内部（系统账号）"])


@router.get("/status", summary="本实例的现场与依赖状态（系统账号）")
def internal_status(source: str = Depends(require_system_token)):
    st = site.status()
    from app.secretary import runtime_cfg

    model = {}
    db = {}
    if st.get("ok"):
        try:
            vals = runtime_cfg.all()
            model = {"channel": vals.get("model_channel") or "",
                     "base_url": vals.get("llm_base_url") or "",
                     "model": vals.get("llm_model") or "",
                     "api_key_configured": bool(vals.get("llm_api_key"))}
            db = {"host": vals.get("db_host") or "", "port": vals.get("db_port") or "",
                  "db": vals.get("db_name") or "", "user": vals.get("db_user") or "",
                  "password_configured": bool(vals.get("db_password"))}
        except Exception as exc:                      # noqa: BLE001
            model = {"error": "%s: %s" % (type(exc).__name__, str(exc)[:120])}
    return {"ok": st.get("ok"), "auth": source, "site": st.get("site"),
            "site_id": st.get("site_id"), "business_empty": st.get("business_empty"),
            "error": st.get("error") or "", "model": model, "business_db": db,
            "system_token_configured": st.get("system_token_configured")}
