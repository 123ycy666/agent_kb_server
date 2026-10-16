"""审计 IP 归一化的回归测试（纯内存，不连库）。

背景（2026-10-16 定位）
----------------------
`audit_log.ip` 是 PG 的 `inet` 类型，只接受合法 IP。而 `X-Forwarded-For`
是**客户端可控**的请求头 —— 修复前只要塞一个非 IP 字符串（如 `garbage`），
审计落库就抛 `DataError`，而审计在登录流程里是同步执行的 ⇒ **登录接口直接 500**。
等于任何匿名请求都能把登录口打崩。

运行： python server/tests/test_audit_ip_guard.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.audit import _safe_ip  # noqa: E402

ACCEPT = ["127.0.0.1", "10.0.0.1", "172.30.250.85", "::1", "2001:db8::1", "1.2.3.4:8080"]
REJECT = ["", None, "garbage-not-an-ip", "unknown", "-", "localhost", "1.2.3", "999.1.1.1"]


def test_valid_ips_pass_through():
    for ip in ACCEPT:
        got = _safe_ip(ip)
        assert got is not None, f"{ip!r} 应被接受"
    assert _safe_ip("1.2.3.4:8080") == "1.2.3.4"  # 带端口要剥掉端口
    assert _safe_ip("  10.0.0.1  ") == "10.0.0.1"  # 前后空白要清掉


def test_invalid_values_become_none():
    for ip in REJECT:
        assert _safe_ip(ip) is None, f"{ip!r} 应降级为 None，而不是原样写库"


def _run() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  ✅ {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  ❌ {fn.__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run())
