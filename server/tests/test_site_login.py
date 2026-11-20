# -*- coding: utf-8 -*-
"""登录随现场（FR-LOGIN-001）：JWT 密钥按现场派生，切库后旧 token 一律失效。

    python tests/test_site_login.py

不连库：只验密钥派生与 token 解码这一层（HTTP 级切换见 tests/ac/ac05_switch_token.py）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAILS = []
OKS = []


def check(name, ok, detail=""):
    (OKS if ok else FAILS).append(name)
    print(("  [ OK ] " if ok else "  [FAIL] ") + name + ((" | " + str(detail)) if detail else ""))


A = {"pg": {"host": "127.0.0.1", "port": 5433, "dbname": "ad_new", "user": "kb",
            "password": "x"}, "admin": {}}
B = {"pg": {"host": "127.0.0.1", "port": 5433, "dbname": "ad_empty", "user": "kb",
            "password": "x"}, "admin": {}}


def main() -> int:
    from app.core import security, site

    saved = dict(site._STATE)
    try:
        site._STATE.update({"cfg": site.normalize(A), "ok": True})
        key_a = site.jwt_key()
        tok = security.create_access_token(7, extra={"username": "kbadmin"})
        payload = security.decode_access_token(tok)
        check("同现场能解出 token", bool(payload) and payload.get("sub") == "7", payload)
        check("token 带现场标识 sid", bool(payload) and payload.get("sid") == site.site_id(),
              (payload or {}).get("sid"))
        check("换了库名密钥就变", key_a != site.jwt_key(site.normalize(B)))
        check("同库名密钥稳定", key_a == site.jwt_key(site.normalize(A)))
        check("未配置现场也有密钥（不抛）", bool(site.jwt_key({"pg": {"dbname": ""}})))

        # 切到 B 现场：同一个 token 必须解不开
        site._STATE.update({"cfg": site.normalize(B), "ok": True})
        check("切现场后旧 token 解不开", security.decode_access_token(tok) is None,
              "旧 token 仍可用 = 现场隔离失效")

        # 只在 sid 上做手脚也要拦住（密钥相同、sid 不同）
        import jwt as _jwt

        from app.core.config import settings

        forged = _jwt.encode({"sub": "7", "sid": "another/db", "typ": "access"},
                             site.jwt_key(), algorithm=settings.jwt_algorithm)
        check("sid 不符也拒绝", security.decode_access_token(forged) is None)
    finally:
        site._STATE.clear()
        site._STATE.update(saved)

    print("\n结果：%d 通过 / %d 失败" % (len(OKS), len(FAILS)))
    if FAILS:
        print("失败项：", FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
