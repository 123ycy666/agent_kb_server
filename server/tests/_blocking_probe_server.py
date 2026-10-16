# -*- coding: utf-8 -*-
"""测试夹具：一个「把模型调用换成 2 秒同步阻塞」的真实应用实例。

为什么需要它
------------
验证「慢端点阻塞全站」必须用**真实单进程 uvicorn + 真实 socket**：
用 httpx.ASGITransport 在同一条事件循环里做，事件循环被阻塞时**测试自己也会被卡住**，
第二个请求会在慢请求结束之后才发出 ⇒ 两个请求根本没重叠，测试假通过。

本文件只做两件事（都不碰生产代码）：
1. 覆盖登录依赖，免真实认证；
2. 把 agent_mod.ask 换成 2 秒的同步假实现，模拟同步等模型。

用法（由 test_async_not_blocking.py 拉起）：
    python -m uvicorn _blocking_probe_server:app --app-dir tests --port <port>
"""
from __future__ import annotations

import time

from app.core.deps import CurrentActor, get_current_actor
from app.main import app
from app.secretary import agent as agent_mod

SLOW_SECONDS = float(__import__("os").environ.get("PROBE_SLOW_SECONDS", "2.0"))


def _fake_actor() -> CurrentActor:
    return CurrentActor(
        type="user", user_id=1, username="probe_user", display_name="探针",
        role_ids=[], role_codes=["user"], permissions=set(),
    )


def _slow_ask(*args, **kwargs):
    """同步假实现 —— 与真实的 agent_mod.ask 一样是普通 def（内部同步等模型）。"""
    time.sleep(SLOW_SECONDS)
    return {
        "input_question": "probe", "question": "probe", "session_id": "s_probe",
        "answer": "ok", "trace": [], "no_db_query": True, "db_query_count": 0,
        "elapsed_ms": int(SLOW_SECONDS * 1000), "steps": 0,
    }


app.dependency_overrides[get_current_actor] = _fake_actor
agent_mod.ask = _slow_ask
