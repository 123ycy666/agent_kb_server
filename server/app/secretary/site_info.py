# -*- coding: utf-8 -*-
"""现场信息（现场名 / 全称 / 地市标签 / 县公司清单 / 默认所）—— 存当前库 `cfg.site_info`。

为什么在库：这些是**每个现场不同**的内容，代码里出现任何一个具体单位名都违规（NFR-004）。
原先是 `project.py` 里的常量（2026-10-30 由站点包内联进来），现在改成「库里有就用，
没有就空」：空现场拿到空串，调用方各自显示通用文案，不回落任何现场的内容（FR-MIGR-001）。

读不到一律返回空，不抛异常 —— 现场信息不是关键路径，缺了不该让页面 500。
"""
from __future__ import annotations

import json
import time

from app.secretary import pgdb

TTL = 30.0
_CACHE: dict = {"at": 0.0, "rows": None}

KEYS = {
    "site_name": "现场名（例：某市）",
    "site_full": "单位全称",
    "site_code": "现场代码（英文，用于日志/目录）",
    "default_station": "默认供电所（问题没提地点时的兜底）",
    "city_label": "地市层级名称",
    "city_dept_id": "地市部门 id（数字）",
    "county_roles": "县公司角色码（JSON 数组）",
    "city_names": "地市说法（JSON 数组）",
    "county_depts": "县公司清单（JSON 数组，元素含 key/dept_id/role/label）",
}


def ensure() -> None:
    with pgdb.connect(autocommit=True) as c:
        c.cursor().execute("""create table if not exists cfg.site_info (
                                  k varchar(40) primary key,
                                  v text not null default '',
                                  updated_at timestamptz not null default now())""")


def _all(refresh: bool = False) -> dict:
    now = time.time()
    if not refresh and _CACHE["rows"] is not None and (now - _CACHE["at"]) < TTL:
        return dict(_CACHE["rows"])
    rows: dict = {}
    try:
        with pgdb.connect(autocommit=True) as c:
            cur = c.cursor()
            cur.execute("select k, v from cfg.site_info")
            rows = {str(r["k"]): str(r["v"] or "") for r in cur.fetchall()}
    except Exception:                                 # noqa: BLE001 - 空库/连不上都当没有
        rows = {}
    _CACHE.update({"rows": rows, "at": now})
    return dict(rows)


def clear_cache() -> None:
    _CACHE.update({"rows": None, "at": 0.0})


def get(key: str, default: str = "") -> str:
    v = _all().get(key)
    return v if (v or "").strip() else default


def get_list(key: str, default: list | None = None) -> list:
    raw = _all().get(key) or ""
    if not raw.strip():
        return list(default or [])
    try:
        val = json.loads(raw)
        return val if isinstance(val, list) else list(default or [])
    except ValueError:
        return list(default or [])


def get_int(key: str, default: int = 0) -> int:
    try:
        return int(str(_all().get(key) or "").strip() or default)
    except ValueError:
        return default


def all_values() -> dict:
    return _all(refresh=True)


def save(values: dict, user: str = "") -> dict:
    ensure()
    clean = {k: str(v) for k, v in (values or {}).items() if k in KEYS}
    if not clean:
        return {"ok": True, "saved": 0}
    with pgdb.connect(autocommit=True) as c:
        cur = c.cursor()
        for k, v in clean.items():
            cur.execute("insert into cfg.site_info (k, v, updated_at) values (%s,%s,now()) "
                        "on conflict (k) do update set v=excluded.v, updated_at=now()",
                        (k, v))
    clear_cache()
    return {"ok": True, "saved": len(clean)}
