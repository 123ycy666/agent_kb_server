# -*- coding: utf-8 -*-
"""现场引导配置（AC-01/AC-02 的底层）：引导文件、切库、建库、初始管理员。

    python tests/test_site_bootstrap.py

用一个**临时库**（用完就删）和**临时引导文件**跑，绝不碰 ad_new / ad_empty。
每条都必须是能判真假的行为：文件写没写、schema 在不在、管理员口令改没改。
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAILS = []
OKS = []


def check(name, ok, detail=""):
    (OKS if ok else FAILS).append(name)
    print(("  [ OK ] " if ok else "  [FAIL] ") + name + ((" | " + str(detail)) if detail else ""))


def pg_admin(dbname):
    import psycopg

    return psycopg.connect(host="127.0.0.1", port=5433, dbname=dbname, user="kb",
                           password="kb_dev_pwd", autocommit=True)


TMPDB = "ad_test_boot"
TMPDB2 = "ad_test_boot2"


def drop_db(name):
    with pg_admin("postgres") as c:
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity where datname=%s",
                  (name,))
        c.execute('drop database if exists "%s"' % name)


def main() -> int:
    from app.core import site

    original_boot = site.BOOT_FILE
    tmpdir = Path(tempfile.mkdtemp(prefix="siteboot-"))
    site.BOOT_FILE = tmpdir / "site.json"

    good = {
        "pg": {"host": "127.0.0.1", "port": 5433, "dbname": TMPDB, "user": "kb",
               "password": "kb_dev_pwd"},
        "admin": {"username": "bootadmin", "password": "Boot@123456"},
    }

    try:
        drop_db(TMPDB)
        drop_db(TMPDB2)
        with pg_admin("postgres") as c:
            c.execute('create database "%s"' % TMPDB)
            c.execute('create database "%s"' % TMPDB2)

        # ── 1. normalize 补默认值 ────────────────────────────
        n = site.normalize({"pg": {"dbname": "x"}})
        check("normalize 补默认 host/port", n["pg"]["host"] == "127.0.0.1" and n["pg"]["port"] == 5432,
              n["pg"])
        check("normalize 允许空管理员", n["admin"]["username"] == "" and n["admin"]["password"] == "")

        # ── 2. 引导文件读写：UTF-8 无 BOM，可读回 ────────────
        site.save(good)
        raw = site.BOOT_FILE.read_bytes()
        check("引导文件存在", site.BOOT_FILE.is_file(), str(site.BOOT_FILE))
        check("引导文件无 BOM", not raw.startswith(b"\xef\xbb\xbf"), raw[:3])
        back = site.load()
        check("读回内容一致", back["pg"]["dbname"] == TMPDB
              and back["admin"]["username"] == "bootadmin", back.get("pg"))

        # ── 3. 连不上：不写文件、不切现场 ────────────────────
        site.BOOT_FILE = tmpdir / "bad.json"
        bad = json.loads(json.dumps(good))
        bad["pg"]["port"] = 5999
        st = site.apply(bad)
        check("连不上时 ok=false", st["ok"] is False, st.get("error", "")[:80])
        check("连不上时不写引导文件", not site.BOOT_FILE.exists())

        # ── 4. 切到临时库：四片 + 视图 + 管理员 ──────────────
        site.BOOT_FILE = tmpdir / "site.json"
        site.save(good)
        st = site.apply(good)
        check("切库 ok=true", st["ok"] is True, st.get("error", "")[:120])
        with pg_admin(TMPDB) as c:
            cur = c.execute("select nspname from pg_namespace where nspname in ('cfg','kb','qa','base')")
            got = sorted(r[0] for r in cur.fetchall())
            check("四个 schema 建好", got == ["base", "cfg", "kb", "qa"], got)

            cur = c.execute("""select table_name, table_type from information_schema.tables
                         where table_schema='public' and table_name in
                         ('app_user','document','prompt_material','t_chat_session')""")
            pub = {r[0]: r[1] for r in cur.fetchall()}
            check("public 只剩视图", set(pub.values()) == {"VIEW"}, pub)

            cur = c.execute("select to_regclass('cfg.prompt_material'), to_regclass('kb.document'),"
                            " to_regclass('qa.t_chat_session'), to_regclass('base.app_user')")
            found = cur.fetchone()
            check("实体表落在各自 schema", all(found), found)

            cur = c.execute("select username, role, status, coalesce(password_hash,'')"
                            " from base.app_user")
            rows = cur.fetchall()
            hit = [r for r in rows if r[0] == "bootadmin"]
            check("初始管理员已建", bool(hit), rows)
            check("管理员是 admin 且有口令",
                  bool(hit) and hit[0][1] == "admin" and hit[0][3].startswith("$2"),
                  hit[0][1:] if hit else None)

            from app.core.security import verify_password

            check("初始管理员口令可校验",
                  bool(hit) and verify_password("Boot@123456", hit[0][3]))

        check("切库后 business_empty=true", site.business_empty() is True)
        st = site.status(refresh=True)
        check("status 里 ok/configured/site_id 对得上",
              st["ok"] is True and st["configured"] is True
              and st["site_id"] == "127.0.0.1:5433/" + TMPDB, st.get("site_id"))

        # ── 5. 已有口令的管理员：不覆盖 ──────────────────────
        with pg_admin(TMPDB) as c:
            before = c.execute("select password_hash from base.app_user"
                               " where username='bootadmin'").fetchone()[0]
        again = json.loads(json.dumps(good))
        again["admin"]["password"] = "Other@999999"
        st = site.apply(again)
        with pg_admin(TMPDB) as c:
            after = c.execute("select password_hash, role, status from base.app_user"
                              " where username='bootadmin'").fetchone()
        check("已存在管理员不覆盖口令", st["ok"] and after[0] == before,
              "改前改后哈希一致" if after[0] == before else "哈希被改了")
        check("不覆盖时也不改角色", after[1] == "admin" and after[2] == "active", after[1:])

        # ── 6. 空现场 vs 有内容 ──────────────────────────────
        check("空库空态判定为 true", site.business_empty(refresh=True) is True)
        with pg_admin(TMPDB) as c:
            c.execute("insert into cfg.prompt_material (slot, section, body) "
                      "values ('fetch','x','y')")
        check("有提示词材料后不再算空", site.business_empty(refresh=True) is False)

        # ── 7. 人工改引导文件后 boot() 能读回来 ──────────────
        drop_db(TMPDB2)
        with pg_admin("postgres") as c:
            c.execute('create database "%s"' % TMPDB2)
        third = json.loads(json.dumps(good))
        third["pg"]["dbname"] = TMPDB2
        site.BOOT_FILE.write_text(json.dumps(third, ensure_ascii=False), encoding="utf-8")
        st = site.boot()
        check("boot 跟着文件切库", st["ok"] is True and st["site_id"].endswith("/" + TMPDB2),
              st.get("site_id"))
    finally:
        site.BOOT_FILE = original_boot
        shutil.rmtree(tmpdir, ignore_errors=True)
        drop_db(TMPDB)
        drop_db(TMPDB2)

    print("\n结果：%d 通过 / %d 失败" % (len(OKS), len(FAILS)))
    if FAILS:
        print("失败项：", FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
