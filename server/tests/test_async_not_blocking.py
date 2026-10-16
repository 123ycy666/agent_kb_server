# -*- coding: utf-8 -*-
"""回归测试：慢端点不得阻塞其他请求（真实单进程 uvicorn + 两个客户端线程）。

背景（2026-10-16 修的真实故障）
--------------------------------
`app/api/v1/agent.py` 的问数相关端点是 `async def`，但函数体里直接调**同步阻塞**
函数（`agent_mod.ask` 走 urllib 同步等模型，单次几十秒）。启动是**单进程单 worker**
（`uvicorn app.main:app --host 0.0.0.0 --port 8200`，无 `--workers`），
⇒ 一个人提问期间**所有请求**都要排队（连同步 def 的知识库端点也发不出响应），
表现为「一个人提问、全站转圈」。最长的 `/api/asr` 会占住事件循环约 180 秒。

修法：保留 `async def`（`_read_json` / `request.body()` 是异步的，不能改成 def），
把重调用包成 `await run_in_threadpool(...)`。

为什么必须起真实服务器
----------------------
用 httpx.ASGITransport 在同一条事件循环里测不出问题：事件循环被阻塞时**测试自己也被卡住**，
第二个请求会在慢请求结束后才发出 ⇒ 两个请求没重叠，测试假通过。
所以这里起真实 uvicorn + 两个客户端线程，按真实并发打。

谁把 `run_in_threadpool` 去掉，本测试立刻红（已验证：回退后受害者端点耗时约 1.5 秒）。

运行： python server/tests/test_async_not_blocking.py
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
VICTIM = "/api/skills"            # 同步 def 端点，等价于知识库那批端点
SLOW_SECONDS = 2.0
VICTIM_SEND_DELAY = 0.5           # 慢请求开跑 0.5 秒后再打受害者端点
VICTIM_MAX_OK = 0.8               # 被拖住的话会接近 SLOW_SECONDS - VICTIM_SEND_DELAY


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _get(url: str, timeout: float = 20.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, r.read()


def _post_json(url: str, payload: dict, timeout: float = 30.0):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def _wait_ready(base: str, proc: subprocess.Popen, seconds: float = 40.0) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            _get(base + VICTIM, timeout=2.0)
            return True
        except Exception:                      # noqa: BLE001
            time.sleep(0.3)
    return False


def main() -> int:
    port = _free_port()
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ, PYTHONUNBUFFERED="1", PROBE_SLOW_SECONDS=str(SLOW_SECONDS))
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "_blocking_probe_server:app",
         "--app-dir", "tests", "--host", "127.0.0.1", "--port", str(port),
         "--log-level", "warning"],
        cwd=str(SERVER_DIR), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    print("阻塞防护回归测试（真实单进程 uvicorn，端口 %d）" % port)
    try:
        if not _wait_ready(base, proc):
            print("  ❌ 探针服务未能在 40 秒内就绪（可能导入失败）")
            return 1

        out: dict = {}

        def hit_slow():
            t0 = time.time()
            try:
                out["slow_status"], _ = _post_json(base + "/api/ask", {"question": "探针"})
            except Exception as exc:               # noqa: BLE001
                out["slow_status"] = "ERR:%s" % type(exc).__name__
            out["slow"] = time.time() - t0

        def hit_victim():
            time.sleep(VICTIM_SEND_DELAY)
            t0 = time.time()
            try:
                out["victim_status"], _ = _get(base + VICTIM)
            except Exception as exc:               # noqa: BLE001
                out["victim_status"] = "ERR:%s" % type(exc).__name__
            out["victim"] = time.time() - t0

        ts = threading.Thread(target=hit_slow)
        tv = threading.Thread(target=hit_victim)
        ts.start()
        tv.start()
        ts.join(timeout=60)
        tv.join(timeout=60)

        slow_ms = out.get("slow", -1)
        victim_ms = out.get("victim", -1)
        ok_slow = out.get("slow_status") == 200 and slow_ms >= SLOW_SECONDS * 0.8
        ok_victim = out.get("victim_status") == 200 and 0 <= victim_ms < VICTIM_MAX_OK

        print("  · 慢提问：耗时 %.3f 秒，status=%s" % (slow_ms, out.get("slow_status")))
        print("  · 慢请求进行中，%s：耗时 %.4f 秒，status=%s（阈值 < %.1f）"
              % (VICTIM, victim_ms, out.get("victim_status"), VICTIM_MAX_OK))
        if not ok_slow:
            print("  ❌ 慢请求没有真正跑起来，测试前提不成立")
        if not ok_victim:
            print("  ❌ %s 被慢请求拖住了 —— 说明有端点没走线程池" % VICTIM)
        passed = ok_slow and ok_victim
        print("  %s %s" % ("✅ 通过" if passed else "❌ 失败",
                           "慢端点未阻塞其他请求" if passed else "存在阻塞"))
        print("\n%d/1 通过" % (1 if passed else 0))
        return 0 if passed else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
