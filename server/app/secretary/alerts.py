# -*- coding: utf-8 -*-
"""每日预警（系统账号调用）：按账号返回「这个人今天该办的事」。

用途没变（原来 `/api/alerts/daily` 就是这个语义），但数据源换成了**工作流库**：
预警事项与通知在 `wf_item` / `wf_notify`，旧引擎在 2026-11-06 换框架时删掉了。
工作流库不可达时返回 `status=unknown` + 原因，**不许 500** —— 调用方是定时任务，
一次失败可以下次再来，抛 500 只会让对方以为接口挂了。
"""
from __future__ import annotations

import time


def daily(account: str, today: str | None = None, when: str | None = None,
          limit: int = 10) -> dict:
    acct = (account or "").strip()
    out = {"status": "ok", "account": acct, "alerts": [], "checked_at": _now(today, when)}
    if not acct:
        out.update({"status": "bad_request", "error": "缺少 account"})
        return out
    try:
        from app.core.database import SessionLocalWF
        from app.workflow import store

        db = SessionLocalWF()
        try:
            res = store.list_notifies(db, login=acct, unread=True, limit=limit)
        finally:
            db.close()
    except Exception as exc:                          # noqa: BLE001 - 工作流库可能没起
        out.update({"status": "unknown",
                    "error": "%s: %s" % (type(exc).__name__, str(exc)[:160])})
        return out

    items = res.get("items") or []
    out["alerts"] = [{"id": n.get("id"), "type": n.get("alert_type"),
                      "level": n.get("level"), "title": n.get("scope_name"),
                      "message": n.get("message"), "count": n.get("alert_count"),
                      "sent_at": n.get("sent_at")} for n in items]
    out["unread"] = res.get("unread", len(items))
    out["total"] = res.get("total", len(items))
    if not items:
        out["status"] = "empty"
    return out


def _now(today: str | None, when: str | None) -> str:
    if when:
        return when
    return (today + " 00:00:00") if today else time.strftime("%Y-%m-%d %H:%M:%S")
