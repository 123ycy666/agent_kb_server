# -*- coding: utf-8 -*-
"""当前现场：引导配置、连库、切换、现场级派生值。

一个现场 = 一个 PG 库（SRS-01 §1.2）。本模块是「当前现场」的**唯一所有者**：
连接、JWT 密钥、空现场判定、降级门禁全部从这里取，别处不再各算一套。

引导配置（本地文件 `config/site.json`，路径可用 SITE_CONFIG_FILE 覆盖）：

    {"pg": {"host": "127.0.0.1", "port": 5433, "dbname": "ad_new",
            "user": "kb", "password": "…"},
     "admin": {"username": "admin", "password": "…"},
     "wf_database": "ad_wf"}

「现场配置」页保存时先试连，连不上**不写文件、不切库** —— 否则一次手滑就把实例锁死。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
from pathlib import Path

from app.core.config import PROJECT_ROOT, settings

BOOT_FILE = Path(os.environ.get("SITE_CONFIG_FILE")
                 or (PROJECT_ROOT / "config" / "site.json"))

_TTL_EMPTY = 30.0
_RETRY_SEC = 30.0

_STATE: dict = {
    "cfg": None,          # 归一后的引导配置
    "ok": False,
    "error": "",
    "at": 0.0,            # 上次连库成功/失败的时间
    "empty": True,        # 业务配置是否为空
    "empty_at": 0.0,
}
_LOCK = threading.Lock()


# ══════════════════════════════════════════════════════════════
#  引导配置：读 / 写 / 归一
# ══════════════════════════════════════════════════════════════

def normalize(cfg: dict | None) -> dict:
    pg = dict((cfg or {}).get("pg") or {})
    admin = dict((cfg or {}).get("admin") or {})
    try:
        port = int(pg.get("port") or 5432)
    except (TypeError, ValueError):
        port = 5432
    return {
        "pg": {
            "host": str(pg.get("host") or "127.0.0.1").strip(),
            "port": port,
            "dbname": str(pg.get("dbname") or "").strip(),
            "user": str(pg.get("user") or "").strip(),
            "password": str(pg.get("password") or ""),
        },
        "admin": {
            "username": str(admin.get("username") or "").strip(),
            "password": str(admin.get("password") or ""),
        },
        "wf_database": str((cfg or {}).get("wf_database") or "").strip() or None,
    }


def load() -> dict | None:
    """读引导配置。文件不存在 / 不是合法 JSON → None。"""
    p = Path(BOOT_FILE)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return normalize(data)


def save(cfg: dict) -> None:
    cfg = normalize(cfg)
    p = Path(BOOT_FILE)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)


# ══════════════════════════════════════════════════════════════
#  连接串与派生值
# ══════════════════════════════════════════════════════════════

def _cur_cfg() -> dict | None:
    return _STATE.get("cfg")


def _url(cfg: dict | None = None) -> str:
    from urllib.parse import quote_plus

    pg = (cfg or _cur_cfg() or normalize({}))["pg"]
    return ("postgresql://%s:%s@%s:%s/%s"
            % (quote_plus(pg["user"]), quote_plus(pg["password"]),
               pg["host"], pg["port"], pg["dbname"]))


# ★ search_path 必须固定成 public：库账号名就叫 `kb`，默认 search_path 是
#   `"$user", public`，于是 `$user` 指到真实 schema `kb` —— 不带前缀建的表现在会
#   落进 `kb`，把该进 cfg/base 的表建错地方（db/migrate_v5.py 里记过同一个坑）。
_SEARCH_PATH = "?options=-csearch_path%3Dpublic"


def dsn(cfg: dict | None = None) -> str:
    """psycopg 用的连接串（不含 SQLAlchemy 方言前缀）。"""
    return _url(cfg) + _SEARCH_PATH


def sqlalchemy_url(cfg: dict | None = None) -> str:
    # SQLAlchemy 的引擎自己带 connect_args（search_path），这里不能再拼 query。
    return "postgresql+psycopg://" + _url(cfg).split("://", 1)[1]


def site_id(cfg: dict | None = None) -> str:
    pg = (cfg or _cur_cfg() or normalize({}))["pg"]
    if not pg.get("dbname"):
        return ""
    return "%s:%s/%s" % (pg["host"], pg["port"], pg["dbname"])


def jwt_key(cfg: dict | None = None) -> str:
    """JWT 密钥随现场派生：切库后旧 token 的签名必然验不过（FR-LOGIN-001）。"""
    base = str(settings.jwt_secret or "")
    sid = site_id(cfg) or "unconfigured"
    return hmac.new(base.encode("utf-8"), ("site:" + sid).encode("utf-8"),
                    hashlib.sha256).hexdigest()


def ok() -> bool:
    return bool(_STATE.get("ok"))


# ══════════════════════════════════════════════════════════════
#  连通性测试
# ══════════════════════════════════════════════════════════════

def test_pg(pg: dict, timeout: int = 5) -> tuple[bool, str]:
    """连一次目标库，回「能不能连 + 一句人话」。"""
    import psycopg
    from urllib.parse import quote_plus

    c = normalize({"pg": pg})["pg"]
    if not c["dbname"] or not c["user"]:
        return False, "库名与账号都得填"
    url = ("postgresql://%s:%s@%s:%s/%s%s" % (quote_plus(c["user"]), quote_plus(c["password"]),
                                             c["host"], c["port"], c["dbname"], _SEARCH_PATH))
    try:
        with psycopg.connect(url, connect_timeout=timeout, autocommit=True) as conn:
            cur = conn.execute("select version(), current_database()")
            ver, db = cur.fetchone()
        return True, "%s | 库 %s" % (str(ver).split(",")[0], db)
    except Exception as exc:                          # noqa: BLE001 - 就是试连
        return False, "%s: %s" % (type(exc).__name__, str(exc)[:200])


# ══════════════════════════════════════════════════════════════
#  切现场
# ══════════════════════════════════════════════════════════════

def apply(cfg: dict, admin: dict | None = None) -> dict:
    """连上目标库 → 写引导文件 → 重绑连接 → 建库结构 → 初始管理员 → 清缓存。"""
    with _LOCK:
        cfg = normalize(cfg)
        if admin:
            cfg["admin"] = normalize({"admin": admin})["admin"]
        # cfg 先挂上：连不上时 status 里要能看出「配的是哪个库」——
        # configured=「引导配置存在」而不是「连接成功」，否则现场配置页无法显示失败目标。
        _STATE.update({"cfg": cfg})
        good, detail = test_pg(cfg["pg"])
        if not good:
            _STATE.update({"ok": False, "error": "连不上产品库：%s" % detail, "at": time.time()})
            return status(refresh=False)
        save_error = ""
        try:
            try:
                save(cfg)
            except OSError as exc:
                # 容器里引导文件可能只读挂载：写不下去就**只警告**，内存里照样生效。
                # 绝不能因此把 ok 打成 false —— 那会让整个实例退化成只有配置页。
                save_error = "引导文件不可写（%s）：本次配置只在本进程生效，重启会回到文件里的那份" % exc
                print("[site] " + save_error, flush=True)
            _STATE.update({"cfg": cfg, "ok": True, "error": ""})
            _rebind(cfg)
            # 必须先清缓存再建表：inject_store / spec / route_rule / report 都有
            # `_READY` 闸，不清就会「以为已经建过」，新库里这三张表根本不会建。
            reset_caches()
            _prepare_db(cfg)
            # 任务/预警的 wf_* 表跟着现场库走：切到新库时在这里补建（换现场不用手工建表）。
            try:
                from app.workflow import store as _wf_store

                _wf_store.ensure_tables()
            except Exception as exc:                  # noqa: BLE001 - 工作流是二期功能，建不出来不挡切库
                print("[site] wf 表补建失败（任务/预警页会空）：%s: %s"
                      % (type(exc).__name__, str(exc)[:120]), flush=True)
            admin_res = _ensure_admin(cfg["admin"])
            reset_caches()
            _STATE.update({"cfg": cfg, "ok": True, "error": "", "at": time.time(),
                           "empty_at": 0.0})
            _refresh_business_config()
            out = dict(status(refresh=True), admin=admin_res, pg_detail=detail)
            if save_error:
                out["warning"] = save_error
            return out
        except Exception as exc:                      # noqa: BLE001 - 切库失败要把原因带出去
            _STATE.update({"ok": False, "error": "%s: %s" % (type(exc).__name__,
                                                             str(exc)[:200]),
                           "at": time.time()})
            return status(refresh=False)


def boot() -> dict:
    """启动时调用：读引导文件并连库。读不到/连不上就降级，不拖垮进程。"""
    cfg = load()
    if cfg is None:
        _STATE.update({"cfg": None, "ok": False,
                       "error": "没有引导配置（%s），请在该实例的「现场配置」页填写" % BOOT_FILE,
                       "at": time.time()})
        return status(refresh=False)
    return apply(cfg)


def _rebind(cfg: dict) -> None:
    from app.core import database
    from app.secretary import pgdb

    database.rebind(sqlalchemy_url(cfg))
    database.rebind_wf(_wf_url(cfg))
    pgdb.set_dsn(dsn(cfg))


def _wf_url(cfg: dict) -> str:
    """工作流库：引导配置里写了就跟着切，没写沿用 .env 派生的那份（老现场零退化）。"""
    from app.core.config import settings as st

    db = cfg.get("wf_database")
    if db:
        return _url(cfg).rsplit("/", 1)[0] + "/" + db
    # .env 里的值不在 os.environ（pydantic 直接读文件），两处都要看
    env = os.environ.get("WF_DATABASE_URL") or (getattr(st, "wf_database_url", "") or "").strip()
    if env:
        return env
    # 引导配置没写 wf_database、环境也没指定 → 用**当前现场自己的库**（任务/预警跟着现场走）。
    # 老现场的历史数据在独立库里时，用 WF_DATABASE_URL / 引导配置的 wf_database 指过去即可。
    return sqlalchemy_url(cfg)


def _prepare_db(cfg: dict) -> None:
    """空库补齐：四片 + 建表 + 搬表 + public 视图。已有库只刷视图。"""
    import psycopg

    from app.core import database, pg_schema

    with psycopg.connect(dsn(cfg), autocommit=True) as conn:
        fresh = pg_schema.relkind(conn, "cfg", "prompt_material") is None
        pg_schema.ensure_schemas(conn)
        if fresh:
            # 只有全新库才建表：老库里 public 同名的是视图，
            # create_all 会把它们当已存在的表跳过或直接报错，不必再试。
            from app.core.database import Base
            from app.db_init import ensure_extensions

            ensure_extensions()
            Base.metadata.create_all(bind=database.engine)
            pg_schema.init_tables(conn)
        # 增量 DDL 每次切库都跑（幂等）：老库新增列靠它，别再让它变成只在 fresh 时执行。
        from app.db_init import EXTRA_DDL

        pg_schema.apply_extra(conn, EXTRA_DDL)
        # 新表/新列到位后统一刷一遍 public 视图
        pg_schema.migrate(conn)
    # ★ 无条件建：全是 `create table if not exists`。只在 fresh 时建的话，
    #   老库（已有现场）会永远缺 cfg.site_info / cfg.preset_question / cfg.t_nc_word，
    #   而读侧把「表不存在」吞成空 —— 身份、示例问题、纠错词表静默失效。
    _ensure_module_tables()


# 这些模块的表带 schema 前缀、且不在 ORM / EXTRA_DDL / 结构 SQL 里 —— 空库要它们自己建。
_MODULE_SQL = ("app.secretary.catalog", "app.secretary.kb_eval", "app.secretary.spec",
               "app.secretary.inject_store", "app.secretary.route_rule",
               "app.secretary.runtime_cfg", "app.secretary.schema_map",
               "app.secretary.site_info", "app.secretary.presets",
               "app.secretary.lex_words", "app.secretary.report",
               "app.agent.db", "app.api.v1.eval")


def _ensure_module_tables() -> None:
    import importlib

    for name in _MODULE_SQL:
        try:
            m = importlib.import_module(name)
            fn = getattr(m, "ensure", None)
            if callable(fn):
                fn()
        except Exception as exc:                      # noqa: BLE001 - 单表建不出来不挡启动
            print("[site] 建表失败 %s: %s: %s" % (name, type(exc).__name__, str(exc)[:120]),
                  flush=True)


def _ensure_admin(admin: dict) -> dict:
    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.core.security import hash_password
    from app.models import AppUser

    username = (admin or {}).get("username") or ""
    password = (admin or {}).get("password") or ""
    if not username or not password:
        return {"skipped": True, "reason": "未填初始管理员"}
    db = SessionLocal()
    try:
        row = db.execute(select(AppUser).where(AppUser.username == username)).scalar_one_or_none()
        if row is not None and (row.password_hash or "").strip():
            return {"skipped": True, "username": username, "reason": "管理员已存在，口令未改"}
        if row is None:
            row = AppUser(username=username, display_name=username, role="admin",
                          status="active")
            db.add(row)
            created = True
        else:
            created = False
        row.password_hash = hash_password(password)
        db.commit()
        return {"created": created, "username": username}
    finally:
        db.close()


def _refresh_business_config() -> None:
    """业务库连接与模型通道都从**当前库**的 cfg.runtime 读，切完立刻生效。"""
    try:
        from app.secretary import config as sconf

        sconf.reset_db()
    except Exception:                                 # noqa: BLE001
        pass
    try:
        from app.secretary import runtime_cfg

        runtime_cfg.reset_model()
        runtime_cfg.refresh()
    except Exception:                                 # noqa: BLE001
        pass
    try:
        from app.core.config import settings as st

        st.model_channel = st.model_channel  # 保留设置对象，避免外部持有旧引用
    except Exception:                                 # noqa: BLE001
        pass


# ══════════════════════════════════════════════════════════════
#  状态与空态
# ══════════════════════════════════════════════════════════════

def status(refresh: bool = False) -> dict:
    cfg = _STATE.get("cfg")
    if refresh and not _STATE.get("ok") and cfg is not None:
        _retry()
        cfg = _STATE.get("cfg")
    pg = (cfg or {}).get("pg") or {}
    return {
        "configured": cfg is not None,
        "ok": bool(_STATE.get("ok")),
        "error": _STATE.get("error") or "",
        "site_id": site_id(cfg) if cfg else "",
        "site": {"host": pg.get("host", ""), "port": pg.get("port", 0),
                 "dbname": pg.get("dbname", ""), "user": pg.get("user", "")},
        "business_empty": business_empty(refresh=refresh),
        "system_token_configured": bool((settings.internal_service_token or "").strip()),
    }


def _retry() -> None:
    """降级状态下的自愈：隔一会儿重试一次已保存的引导配置。"""
    if time.time() - float(_STATE.get("at") or 0) < _RETRY_SEC:
        return
    cfg = load()
    if cfg is None:
        return
    apply(cfg)


def business_empty(refresh: bool = False) -> bool:
    """业务配置是否为空：三张核心表全空即空现场（AC-03）。"""
    if not _STATE.get("ok"):
        return True
    now = time.time()
    if not refresh and (now - float(_STATE.get("empty_at") or 0)) < _TTL_EMPTY:
        return bool(_STATE.get("empty"))
    total = 0
    try:
        import psycopg

        with psycopg.connect(dsn(), autocommit=True, connect_timeout=3) as conn:
            for schema, table in (("cfg", "prompt_material"), ("kb", "document"),
                                  ("cfg", "caliber_metric")):
                try:
                    n = conn.execute('select count(*) from "%s"."%s"' % (schema, table)).fetchone()[0]
                except Exception:                     # noqa: BLE001 - 表还没建就是 0
                    n = 0
                total += int(n or 0)
    except Exception:                                 # noqa: BLE001
        return bool(_STATE.get("empty", True))
    _STATE.update({"empty": total == 0, "empty_at": now})
    return total == 0


# ══════════════════════════════════════════════════════════════
#  缓存清理（切库不清 = 读到旧库数据）
# ══════════════════════════════════════════════════════════════

_CACHES = [
    ("app.secretary.prompt_text", "clear_cache"),
    ("app.secretary.prompt_material", "clear_cache"),
    ("app.secretary.complete_rules", "clear_cache"),
    ("app.secretary.meta_admin", "clear_cache"),
    ("app.secretary.inject_store", "invalidate"),
    ("app.secretary.route_rule", "invalidate"),
    ("app.secretary.spec", "invalidate"),
    ("app.secretary.kb_adapter", "reset_default_cache"),
    ("app.secretary.lex_words", "clear_cache"),
    ("app.secretary.site_info", "clear_cache"),
    ("app.secretary.presets", "clear_cache"),
    ("app.secretary.pgdb", "reset_pools"),
]


def reset_caches() -> None:
    import importlib

    for mod, fn in _CACHES:
        try:
            m = importlib.import_module(mod)
            for name in ([fn] if isinstance(fn, str) else fn):
                f = getattr(m, name, None)
                if callable(f):
                    f()
        except Exception:                             # noqa: BLE001 - 清缓存失败不许挡切库
            pass
    for mod in ("app.secretary.inject_store", "app.secretary.spec",
                "app.secretary.route_rule", "app.secretary.report"):
        try:
            setattr(importlib.import_module(mod), "_READY", False)
        except Exception:                             # noqa: BLE001
            pass
    for mod, attrs in (("app.secretary.caliber", ("_CACHE",)),
                       ("app.secretary.semantic", ("_cache",)),
                       ("app.secretary.tools_kb", ("_CACHE",)),
                       ("app.secretary.name_fix", ("_CORRECTOR",))):
        try:
            m = importlib.import_module(mod)
            for a in attrs:
                if a == "_CACHE":
                    v = getattr(m, a, None)
                    if isinstance(v, dict):
                        v.clear()
                        v.update({"at": 0.0, "t": 0.0, "rows": None, "row": None, "by": None})
                else:
                    setattr(m, a, None)
        except Exception:                             # noqa: BLE001
            pass
    try:
        from app.providers.factory import reset_providers

        reset_providers()
    except Exception:                                 # noqa: BLE001
        pass
    try:
        from app.secretary import tools_db

        tools_db._drop_conn()
    except Exception:                                 # noqa: BLE001
        pass
    try:
        from app.secretary import runtime_cfg

        runtime_cfg.refresh()
    except Exception:                                 # noqa: BLE001
        pass
