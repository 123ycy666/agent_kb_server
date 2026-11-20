# -*- coding: utf-8 -*-
"""AC-05 切换现场后旧 token 失效，用新现场账号可登录。

判据（SRS-01 AC-05 / FR-LOGIN-001 / UI-005）：
- 随州签发的 token 在切到 ad_empty 之后访问任何接口都是 401；
- ad_empty 自己的初始管理员能登录（账号来自当前库的 base 片）；
- 随州的账号在 ad_empty 登不上（账号不跨库）；
- 切回随州后，ad_empty 的 token 也立即失效。

    python tests/ac/ac05_switch_token.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util as U  # noqa: E402


def main() -> int:
    c = U.Checker("AC-05", "切现场后旧 token 失效")
    U.ensure_ad_new()

    token_a = U.login(U.SITE_ADMIN["username"], U.SITE_ADMIN["password"])
    c.check("随州账号登录成功", bool(token_a), token_a[:24])
    code, me, _ = U.req("GET", "/api/v1/auth/me", token=token_a)
    c.check("切之前 token 可用", code == 200, "HTTP %s" % code)
    sid_before = U.site_status().get("site_id")

    U.header("切到 ad_empty")
    code, body, _ = U.switch_site(U.SITE_EMPTY, U.EMPTY_ADMIN, token=token_a)
    c.check("切现场请求成功", code == 200 and (body or {}).get("ok") is True, str(body)[:120])
    sid_after = U.site_status().get("site_id")
    c.check("现场已换", sid_after.endswith("/ad_empty") and sid_after != sid_before, sid_after)

    for path in ("/api/v1/auth/me", "/api/v1/kb", "/api/sessions"):
        code, body, _ = U.req("GET", path, token=token_a)
        c.check("旧 token 访问 %s → 401" % path, code == 401,
                "HTTP %s %s" % (code, (body or {}).get("detail", {}).get("code")))

    U.header("新现场账号")
    token_b = U.login(U.EMPTY_ADMIN["username"], U.EMPTY_ADMIN["password"])
    c.check("ad_empty 的初始管理员能登录", bool(token_b), token_b[:24])
    code, me, _ = U.req("GET", "/api/v1/auth/me", token=token_b)
    c.check("新 token 可用", code == 200 and (me or {}).get("username") == U.EMPTY_ADMIN["username"],
            (me or {}).get("username"))
    c.check("随州账号在 ad_empty 登不上", U.login(U.SITE_ADMIN["username"],
                                                  U.SITE_ADMIN["password"]) == "")

    U.header("切回随州：ad_empty 的 token 也要失效")
    code, body, _ = U.switch_site(U.SITE_A, U.SITE_ADMIN, token=token_b)
    c.check("用新现场管理员切回随州", code == 200 and (body or {}).get("ok") is True, str(body)[:120])
    code, _, _ = U.req("GET", "/api/v1/auth/me", token=token_b)
    c.check("ad_empty 的 token 已失效", code == 401, "HTTP %s" % code)
    token_a2 = U.login(U.SITE_ADMIN["username"], U.SITE_ADMIN["password"])
    c.check("随州账号重新登录成功", bool(token_a2), token_a2[:24])
    code, me, _ = U.req("GET", "/api/v1/auth/me", token=token_a2)
    c.check("重新登录后可用", code == 200, "HTTP %s" % code)
    c.save()
    return 0 if not c.failed else 1


if __name__ == "__main__":
    sys.exit(main())
