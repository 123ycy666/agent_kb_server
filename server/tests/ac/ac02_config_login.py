# -*- coding: utf-8 -*-
"""AC-02 在现场配置页填 PG + 初始管理员并保存：能登录，后台各菜单接口可打开。

判据（SRS-01 AC-02 / FR-CFG-001..003 / UI-001 / UI-003）：
- 目标是一个**空库**（只有库、没有任何 schema/表）；
- 保存后：四片 schema + 表 + public 视图自动建好，初始管理员落地；
- 用该管理员能登录，9 个菜单对应的接口都是 2xx（没有 500 = NFR-002）；
- 三条连通性测试都能跑（产品库 / 业务库 / 模型通道）。

    python tests/ac/ac02_config_login.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util as U  # noqa: E402

MENUS = [
    ("概览", "GET", "/api/v1/system/health"),
    ("对话", "GET", "/api/sessions"),
    ("大屏问数", "GET", "/api/models"),
    ("智能体", "GET", "/api/agent/sessions"),
    ("智能体配置", "GET", "/api/agent/config"),
    ("知识库", "GET", "/api/v1/kb"),
    ("提示词", "GET", "/api/v1/prompts/texts"),
    ("预警", "GET", "/api/workflows/docs"),
    ("任务", "GET", "/api/v1/tasks"),
    ("设置", "GET", "/api/v1/runtime/config"),
]


def main() -> int:
    c = U.Checker("AC-02", "配置空库后能登录且菜单可打开")
    token_a = U.ensure_ad_new()
    c.check("先在随州库登录（要 admin 才能改现场配置）", bool(token_a), token_a[:24])
    if not token_a:
        c.save()
        return 1

    U.header("准备一个真正的空库")
    U.create_db("ad_empty")
    rows = U.psql("ad_empty", "select count(*) from pg_namespace where nspname in "
                              "('cfg','kb','qa','base')")
    c.check("ad_empty 是空库（无四片 schema）", rows[0][0] == 0, rows[0][0])
    c.log("切现场：POST/ PUT /api/v1/site/config → ad_empty")

    code, body, _ = U.switch_site(U.SITE_EMPTY, U.EMPTY_ADMIN, token=token_a)
    c.log("switch → HTTP %s %s" % (code, body))
    c.check("保存并重连成功", code == 200 and body.get("ok") is True,
            (body or {}).get("error", ""))
    st = U.site_status()
    c.check("现场切到 ad_empty", st.get("site_id", "").endswith("/ad_empty"), st.get("site_id"))
    c.check("空库判定为 business_empty", st.get("business_empty") is True,
            st.get("business_empty"))
    c.check("系统令牌仍显示已配置（服务级）", st.get("system_token_configured") is True)

    U.header("空库结构：四片 + public 视图 + 初始管理员")
    schemas = [r[0] for r in U.psql("ad_empty",
                                    "select nspname from pg_namespace where nspname in "
                                    "('cfg','kb','qa','base') order by 1")]
    c.check("四个 schema 建好", schemas == ["base", "cfg", "kb", "qa"], schemas)
    n_tab = U.psql("ad_empty", "select count(*) from information_schema.tables "
                               "where table_schema in ('cfg','kb','qa','base')")[0][0]
    c.check("实体表数量 ≥ 30", int(n_tab) >= 30, n_tab)
    n_view = U.psql("ad_empty", "select count(*) from information_schema.tables "
                                "where table_schema='public' and table_type='VIEW'")[0][0]
    c.check("public 留了兼容视图", int(n_view) >= 20, n_view)
    adm = U.psql("ad_empty", "select username, role, coalesce(password_hash,'') "
                             "from base.app_user")
    c.check("初始管理员已建且带口令",
            any(r[0] == U.EMPTY_ADMIN["username"] and r[1] == "admin" and r[2].startswith("$2")
                for r in adm), adm)

    U.header("登录 + 菜单接口")
    token = U.login(U.EMPTY_ADMIN["username"], U.EMPTY_ADMIN["password"])
    c.check("用初始管理员登录成功", bool(token), token[:24])
    if not token:
        c.save()
        return 1
    code, me, _ = U.req("GET", "/api/v1/auth/me", token=token)
    c.check("/auth/me 带管理权限", code == 200 and "admin" in (me or {}).get("roles", []),
            (me or {}).get("roles"))
    for name, method, path in MENUS:
        code, body, _ = U.req(method, path, token=token)
        detail = body if not isinstance(body, (dict, list)) else ""
        c.check("菜单「%s」%s %s → 2xx（不 500）" % (name, method, path),
                code in (200, 201, 204), "HTTP %s %s" % (code, str(detail)[:80]))

    U.header("三条连通性测试")
    for target, payload in (("pg", {"pg": U.SITE_EMPTY}),
                            ("mysql", {"mysql": U.mysql_cfg()}),
                            ("model", {"model": U.model_cfg()})):
        code, body, _ = U.req("POST", "/api/v1/site/test", dict(payload, target=target),
                              token=token, timeout=60)
        c.log("test %-6s → HTTP %s %s" % (target, code, str(body)[:200]))
        c.check("连通性测试 %s 通过" % target,
                code == 200 and (body or {}).get("ok") is True,
                "%s" % (body or {}).get("detail", ""))

    U.header("FR-CFG-002 两个边界：已有口令不覆盖 / 非管理员 403")
    before = U.psql("ad_empty", "select coalesce(password_hash,'') from base.app_user "
                                "where username=%s" % "'" + U.EMPTY_ADMIN["username"] + "'")
    code, body, _ = U.switch_site(U.SITE_EMPTY,
                                  {"username": U.EMPTY_ADMIN["username"],
                                   "password": "Another@999999"}, token=token)
    after = U.psql("ad_empty", "select coalesce(password_hash,'') from base.app_user "
                               "where username=%s" % "'" + U.EMPTY_ADMIN["username"] + "'")
    c.check("再次保存不会覆盖已有口令", before and after and before[0][0] == after[0][0],
            "哈希一致" if before[0][0] == after[0][0] else "哈希被改了")
    c.check("口令没被覆盖：旧口令仍能登录",
            bool(U.login(U.EMPTY_ADMIN["username"], U.EMPTY_ADMIN["password"])))
    c.check("新口令登不上", U.login(U.EMPTY_ADMIN["username"], "Another@999999") == "")
    # 非管理员：新建一个 user 角色账号来试 PUT
    import sys as _sys
    _sys.path.insert(0, str(U.SERVER))
    from app.core.security import hash_password as _hp

    U.psql("ad_empty", "insert into base.app_user "
                       "(username, display_name, role, status, password_hash) values "
                       "('ac_plain', 'ac_plain', 'user', 'active', %s) "
                       "on conflict (username) do nothing"
           % ("'" + _hp("Plain@123456") + "'"))
    tok_plain = U.login("ac_plain", "Plain@123456")
    c.check("普通账号能登录", bool(tok_plain))
    code, body, _ = U.switch_site(U.SITE_A, U.SITE_ADMIN, token=tok_plain)
    c.check("普通账号改现场配置 → 403", code == 403, "HTTP %s" % code)
    U.psql("ad_empty", "delete from base.app_user where username='ac_plain'")

    # 恢复现场：后续 AC 都假定现场在 ad_empty（空库）
    U.switch_site(U.SITE_EMPTY, U.EMPTY_ADMIN, token=token)
    c.check("结束时现场仍在 ad_empty", U.site_status().get("site_id", "").endswith("/ad_empty"),
            U.site_status().get("site_id"))
    c.save()
    return 0 if not c.failed else 1


if __name__ == "__main__":
    sys.exit(main())
