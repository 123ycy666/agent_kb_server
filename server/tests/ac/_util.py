# -*- coding: utf-8 -*-
"""AC 脚本共用：HTTP、断言、落盘、切现场、重启后端。

设计约束：
- 每项 AC 都**必须留下实际输出**（落 `output/test_results/2026-11-19-骨架与业务库/`），
  不是只在终端打一行「通过」；
- 脚本自己管前置状态（切哪个现场、要不要重启），可单独跑，也可由 `run_all.py` 串起来。
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2]
PROJECT = SERVER.parent
EVID = PROJECT / "output" / "test_results" / "2026-11-19-骨架与业务库"
BOOT = PROJECT / "config" / "site.json"

BASE = os.environ.get("AC_BASE") or "http://127.0.0.1:8200"
PY = os.environ.get("AC_PYTHON") or r"C:\Users\Fred\.workbuddy\binaries\python\envs\default\Scripts\python.exe"

SITE_A = {"host": "127.0.0.1", "port": 5433, "dbname": "ad_new", "user": "kb",
          "password": "kb_dev_pwd"}
SITE_EMPTY = {"host": "127.0.0.1", "port": 5433, "dbname": "ad_empty", "user": "kb",
              "password": "kb_dev_pwd"}
EMPTY_ADMIN = {"username": "admin", "password": "Ac@empty2026"}
SITE_ADMIN = {"username": "kbadmin", "password": "Kb@123456"}


# ══════════════════════════════════════════════════════════════
#  HTTP
# ══════════════════════════════════════════════════════════════

def req(method, path, body=None, token=None, headers=None, timeout=180):
    hdr = dict(headers or {})
    if token:
        hdr["Authorization"] = "Bearer " + token
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        hdr["Content-Type"] = "application/json"
    r = urllib.request.Request(BASE + path, data=data, headers=hdr, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, _load(raw), raw
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, _load(raw), raw
    except Exception as exc:                          # noqa: BLE001
        return 0, {"error": "%s: %s" % (type(exc).__name__, str(exc)[:160])}, b""


def _load(raw: bytes):
    try:
        return json.loads(raw)
    except Exception:                                 # noqa: BLE001
        return raw.decode("utf-8", "replace")


def system_token() -> str:
    env = PROJECT / ".env"
    if not env.is_file():
        return ""
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("INTERNAL_SERVICE_TOKEN="):
            return line.split("=", 1)[1].strip()
    return ""


def env_text(key: str) -> str:
    env = PROJECT / ".env"
    if not env.is_file():
        return ""
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip()
    return ""


def mysql_cfg() -> dict:
    """业务库连接（取自 .env，用于 AC-02 的真实连通性测试）。"""
    return {"db_host": env_text("MYSQL_HOST"), "db_port": env_text("MYSQL_PORT") or 3306,
            "db_name": env_text("DB_NAME") or "ai_data",
            "db_user": env_text("MYSQL_USER"), "db_password": env_text("MYSQL_PASSWORD")}


def model_cfg() -> dict:
    """模型通道（取自 .env，用于 AC-02 的真实连通性测试）。"""
    return {"model_channel": (env_text("MODEL_CHANNEL") or "online").lower(),
            "llm_base_url": env_text("ONLINE_LLM_BASE_URL"),
            "llm_api_key": env_text("ONLINE_LLM_API_KEY"),
            "llm_model": env_text("ONLINE_LLM_MODEL") or "deepseek-v4.1-flash"}


def site_status() -> dict:
    return req("GET", "/api/v1/site/status")[1] or {}


def login(username: str, password: str):
    st, body, _ = req("POST", "/api/v1/auth/login", {"username": username, "password": password})
    return (body or {}).get("access_token", "") if st == 200 else ""


def ensure_ad_new() -> str:
    """把现场切回随州并返回一个可用的随州管理员 token（失败返回空串）。"""
    tok = login(SITE_ADMIN["username"], SITE_ADMIN["password"])
    if tok:
        if not (site_status().get("site_id") or "").endswith("/ad_new"):
            switch_site(SITE_A, SITE_ADMIN, token=tok)
        return login(SITE_ADMIN["username"], SITE_ADMIN["password"]) or tok
    tok_b = login(EMPTY_ADMIN["username"], EMPTY_ADMIN["password"])
    if tok_b:
        switch_site(SITE_A, SITE_ADMIN, token=tok_b)
        return login(SITE_ADMIN["username"], SITE_ADMIN["password"])
    return ""


def ensure_ad_empty() -> str:
    """把现场切到空库（ad_empty）并返回它的管理员 token。"""
    cur = (site_status().get("site_id") or "")
    if cur.endswith("/ad_empty"):
        return login(EMPTY_ADMIN["username"], EMPTY_ADMIN["password"])
    tok_a = ensure_ad_new()
    if not tok_a:
        return ""
    switch_site(SITE_EMPTY, EMPTY_ADMIN, token=tok_a)
    return login(EMPTY_ADMIN["username"], EMPTY_ADMIN["password"])


def switch_site(pg: dict, admin: dict, token: str = "", mysql: dict | None = None,
                model: dict | None = None):
    body = {"pg": pg, "admin": admin}
    if mysql:
        body["mysql"] = mysql
    if model:
        body["model"] = model
    return req("PUT", "/api/v1/site/config", body, token=token, timeout=300)


# ══════════════════════════════════════════════════════════════
#  进程与库
# ══════════════════════════════════════════════════════════════

def kill_backend() -> None:
    ps = ("$p = (Get-NetTCPConnection -LocalPort 8200 -State Listen -ErrorAction SilentlyContinue)"
          ".OwningProcess; if ($p) { Stop-Process -Id $p -Force }")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)


def start_backend(wait: int = 90) -> bool:
    subprocess.Popen([PY, r"D:\pg16local\serve_backend.py"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    for _ in range(wait):
        time.sleep(1)
        st, body, _ = req("GET", "/api/v1/site/status", timeout=3)
        if st == 200:
            return True
    return False


def restart_backend() -> bool:
    kill_backend()
    time.sleep(2)
    return start_backend()


def psql(dbname: str, sql: str) -> list[tuple]:
    import psycopg

    with psycopg.connect(host="127.0.0.1", port=5433, dbname=dbname, user="kb",
                         password="kb_dev_pwd", autocommit=True) as c:
        cur = c.execute(sql)
        return cur.fetchall() if cur.description else []


def create_db(dbname: str, drop: bool = True) -> None:
    import psycopg

    with psycopg.connect(host="127.0.0.1", port=5433, dbname="postgres", user="kb",
                         password="kb_dev_pwd", autocommit=True) as c:
        if drop:
            c.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                      "where datname=%s", (dbname,))
            c.execute('drop database if exists "%s"' % dbname)
        c.execute('create database "%s"' % dbname)


def fingerprint(dbname: str) -> dict:
    """cfg/kb 两片的指纹：行数 + 关键表内容哈希（用来证明「没被改坏」）。"""
    import hashlib

    out = {}
    rows = psql(dbname, """select n.nspname, c.relname from pg_class c
                           join pg_namespace n on n.oid = c.relnamespace
                           where c.relkind='r' and n.nspname in ('cfg','kb')
                           order by 1,2""")
    for schema, table in rows:
        n = psql(dbname, 'select count(*) from "%s"."%s"' % (schema, table))[0][0]
        out["%s.%s" % (schema, table)] = int(n)
    for key in ("prompt_text", "site_info", "preset_question", "t_nc_word"):
        try:
            data = psql(dbname, 'select * from cfg."%s" order by 1' % key)
            h = hashlib.sha256(json.dumps(
                [list(map(str, r)) for r in data], ensure_ascii=False,
                sort_keys=True).encode()).hexdigest()[:16]
            out["cfg.%s#sha" % key] = h
        except Exception:                             # noqa: BLE001
            out["cfg.%s#sha" % key] = "n/a"
    return out


def dump_dom(url: str = "", wait_ms: int = 9000) -> str:
    """用 Edge 无头浏览器渲染页面并返回最终 DOM（验证 UI-002 用）。

    为什么不用 HTTP 抓 HTML：SPA 的门槛在**渲染后**——未配置时 React 只挂载现场配置页，
    HTTP 看到的 index.html 两种情况长得一模一样。
    """
    edge = os.environ.get("AC_EDGE") or r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    if not Path(edge).is_file():
        edge = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    if not Path(edge).is_file():
        return ""
    out = Path(os.environ.get("TEMP") or ".") / ("ac-dom-%d.html" % int(time.time() * 1000))
    profile = Path(os.environ.get("TEMP") or ".") / ("ac-edge-%d" % int(time.time() * 1000))
    try:
        subprocess.run([edge, "--headless=new", "--disable-gpu", "--no-first-run",
                        "--user-data-dir=%s" % profile,
                        "--virtual-time-budget=%d" % wait_ms, "--dump-dom",
                        url or BASE + "/"],
                       stdout=out.open("wb"), stderr=subprocess.DEVNULL, timeout=120)
        return out.read_text(encoding="utf-8", errors="replace") if out.is_file() else ""
    except Exception:                                 # noqa: BLE001
        return ""
    finally:
        try:
            out.unlink(missing_ok=True)
        except OSError:
            pass


# ══════════════════════════════════════════════════════════════
#  断言与落盘
# ══════════════════════════════════════════════════════════════

class Checker:
    def __init__(self, ac: str, title: str):
        self.ac = ac
        self.title = title
        self.rows: list[tuple[str, bool, str]] = []
        self.lines: list[str] = []
        EVID.mkdir(parents=True, exist_ok=True)

    def log(self, text: str) -> None:
        self.lines.append(text)
        print(text)

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.rows.append((name, bool(ok), str(detail)))
        print(("  [ OK ] " if ok else "  [FAIL] ") + name + ((" | " + str(detail)) if detail else ""))
        return bool(ok)

    @property
    def failed(self) -> list[str]:
        return [n for n, ok, _ in self.rows if not ok]

    def save(self, extra: str = "") -> Path:
        p = EVID / ("%s-%s.md" % (self.ac, _slug(self.title)))
        import sys as _sys
        script = Path(_sys.argv[0]).name or (self.ac.lower() + ".py")
        body = ["# %s %s" % (self.ac, self.title), "",
                "> 由 `server/tests/ac/%s` 生成，含实际输出；时间 %s"
                % (script, time.strftime("%Y-%m-%d %H:%M:%S")), "",
                "| 检查项 | 结果 | 实际输出 |", "|---|---|---|"]
        for name, ok, detail in self.rows:
            body.append("| %s | %s | %s |"
                        % (name.replace("|", "\\|"),
                           "PASS" if ok else "FAIL",
                           (detail or "").replace("|", "\\|").replace("\n", " ")[:600]))
        body.append("")
        if self.lines:
            body += ["## 过程输出", "", "```text"] + self.lines[-400:] + ["```", ""]
        if extra:
            body += ["## 附加证据", "", "```text", extra, "```", ""]
        body.append("**结论：%s**（%d 通过 / %d 失败）"
                    % ("PASS" if not self.failed else "FAIL", len(self.rows) - len(self.failed),
                       len(self.failed)))
        p.write_text("\n".join(body), encoding="utf-8")
        print("\n证据：%s" % p)
        print("结论：%s（%d 通过 / %d 失败）"
              % ("PASS" if not self.failed else "FAIL",
                 len(self.rows) - len(self.failed), len(self.failed)))
        return p


def _slug(title: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in title)[:40]


def header(text: str) -> None:
    print("\n" + "=" * 70)
    print(text)
    print("=" * 70)
