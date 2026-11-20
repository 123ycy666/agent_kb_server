# -*- coding: utf-8 -*-
"""AC-06 切换现场后系统账号仍可调用 `/api/alerts/*`。

判据（SRS-01 AC-06 / FR-SYS-001 / IF-SYS-001）：
- `X-Internal-Token` 是**服务级**令牌，切现场不重建 → 两个现场下都能调通；
- 错的令牌一律 401（不得降级成匿名）；
- 一并验证 `/api/internal/*` 与 sga 换 token 入口。

    python tests/ac/ac06_system_token.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util as U  # noqa: E402


def call_alerts(token_hdr: str, account: str = "admin"):
    return U.req("POST", "/api/alerts/daily", {"account": account},
                 headers={"X-Internal-Token": token_hdr} if token_hdr else {})


def main() -> int:
    c = U.Checker("AC-06", "切现场后系统账号仍可用")
    sys_token = U.system_token()
    c.check("服务里有系统令牌（.env INTERNAL_SERVICE_TOKEN）", bool(sys_token),
            (sys_token[:6] + "…") if sys_token else "")

    U.ensure_ad_new()

    U.header("随州库上的系统账号")
    code, body, _ = call_alerts(sys_token)
    c.log("alerts/daily（随州）= HTTP %s %s" % (code, str(body)[:200]))
    c.check("随州：/api/alerts/daily 200", code == 200, "HTTP %s" % code)
    c.check("随州：返回真实结果而不是鉴权失败",
            (body or {}).get("status") in ("ok", "empty", "bad_request")
            and isinstance((body or {}).get("alerts"), list),
            (body or {}).get("status"))
    c.check("随州：鉴权来源是服务配置", (body or {}).get("auth") == "service-env",
            (body or {}).get("auth"))
    code, body, _ = U.req("GET", "/api/internal/status",
                          headers={"X-Internal-Token": sys_token})
    c.check("随州：/api/internal/status 200", code == 200, "HTTP %s" % code)

    code, body, _ = call_alerts("")
    c.check("无令牌 → 401 且沿用旧响应体（status=unauthorized）",
            code == 401 and (body or {}).get("status") == "unauthorized",
            "HTTP %s %s" % (code, (body or {}).get("status")))
    code, body, _ = call_alerts("deadbeef" * 8)
    c.check("错令牌 → 401 且沿用旧响应体",
            code == 401 and (body or {}).get("status") == "unauthorized",
            "HTTP %s %s" % (code, (body or {}).get("status")))

    U.header("切到空库后（旧用户 token 已失效，系统账号必须仍然有效）")
    token_a = U.login(U.SITE_ADMIN["username"], U.SITE_ADMIN["password"])
    code, body, _ = U.switch_site(U.SITE_EMPTY, U.EMPTY_ADMIN, token=token_a)
    c.check("切现场成功", code == 200 and (body or {}).get("ok") is True, str(body)[:120])
    code, body, _ = U.req("GET", "/api/v1/auth/me", token=token_a)
    c.check("（对照）旧用户 token 已 401", code == 401, "HTTP %s" % code)

    code, body, _ = call_alerts(sys_token)
    c.log("alerts/daily（ad_empty）= HTTP %s %s" % (code, str(body)[:200]))
    c.check("切现场后：/api/alerts/daily 仍 200", code == 200, "HTTP %s" % code)
    c.check("切现场后：不是 unauthorized", (body or {}).get("status") != "unauthorized",
            (body or {}).get("status"))
    code, body, _ = U.req("GET", "/api/internal/status",
                          headers={"X-Internal-Token": sys_token})
    c.check("切现场后：/api/internal/status 仍 200", code == 200, "HTTP %s" % code)
    c.check("切现场后：站点信息是新现场",
            str((body or {}).get("site_id", "")).endswith("/ad_empty"),
            (body or {}).get("site_id"))

    U.header("sga 换 token 入口（系统账号可调）")
    # 用**当前库的现场账号**换 token（不能只靠业务库 t_employee 里恰好有这个账号）
    code, body, _ = U.req("POST", "/api/v1/auth/sso/login",
                          {"username": U.EMPTY_ADMIN["username"]},
                          headers={"X-Internal-Token": sys_token})
    c.check("带系统令牌调 sso/login（当前库账号）→ 200 且给 token",
            code == 200 and bool((body or {}).get("access_token")),
            "HTTP %s %s" % (code, (body or {}).get("detail", {}).get("message", "")))

    U.header("切回随州并复查")
    token_b = U.login(U.EMPTY_ADMIN["username"], U.EMPTY_ADMIN["password"])
    U.switch_site(U.SITE_A, U.SITE_ADMIN, token=token_b)
    code, body, _ = call_alerts(sys_token)
    c.check("切回随州后：/api/alerts/daily 仍 200", code == 200, "HTTP %s" % code)
    c.check("切回随州后：不是 unauthorized", (body or {}).get("status") != "unauthorized",
            (body or {}).get("status"))
    c.save()
    return 0 if not c.failed else 1


if __name__ == "__main__":
    sys.exit(main())
