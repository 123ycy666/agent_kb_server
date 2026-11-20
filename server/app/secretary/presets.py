# -*- coding: utf-8 -*-
"""示例问题（问答页「点一下就能问」）—— 2026-11-19 起存当前库 `cfg.preset_question`。

为什么进库：它是**每个现场不同**的业务内容，而代码只发一份（NFR-004 / FR-MIGR-001）。
原来有两份重复副本（`prompts_data.PRESET_ITEMS` 与 `presets.PRESETS`），已合并成库里的
一份；代码里不再写任何一条具体问题。

读不到就是空列表 —— 空现场的问答页不给示例，也不回落任何现场的内容。
"""
from __future__ import annotations

import time

from app.secretary import pgdb

TTL = 30.0
_CACHE: dict = {"at": 0.0, "rows": None}


def ensure() -> None:
    with pgdb.connect(autocommit=True) as c:
        c.cursor().execute("""create table if not exists cfg.preset_question (
                                  id bigserial primary key,
                                  group_name varchar(40) not null default '',
                                  qid varchar(40) not null,
                                  text varchar(200) not null,
                                  sort_no int not null default 0,
                                  enabled boolean not null default true,
                                  updated_at timestamptz not null default now(),
                                  unique (qid))""")


def clear_cache() -> None:
    _CACHE.update({"rows": None, "at": 0.0})


def rows(refresh: bool = False) -> list[dict]:
    now = time.time()
    if not refresh and _CACHE["rows"] is not None and (now - _CACHE["at"]) < TTL:
        return list(_CACHE["rows"])
    try:
        with pgdb.connect(autocommit=True) as c:
            cur = c.cursor()
            cur.execute("select group_name, qid, text from cfg.preset_question "
                        "where enabled order by sort_no, id")
            out = [{"id": str(r["qid"]), "group": str(r["group_name"] or ""),
                    "text": str(r["text"] or "")} for r in cur.fetchall()]
    except Exception:                                 # noqa: BLE001 - 空库/连不上都当没有
        out = []
    _CACHE.update({"rows": out, "at": now})
    return list(out)


def current() -> dict:
    items = rows()
    groups: list = []
    for it in items:
        g = it.get("group") or ""
        if g and g not in groups:
            groups.append(g)
    return {"groups": groups, "items": items}


def payload() -> dict:
    cur = current()
    return {"total": len(cur["items"]), "groups": cur["groups"], "items": cur["items"]}


def replace(items: list[dict]) -> dict:
    """整份替换（迁移脚本用；页面上的增删改走 prompts 页那套）。"""
    ensure()
    with pgdb.connect(autocommit=True) as c:
        cur = c.cursor()
        cur.execute("delete from cfg.preset_question")
        for i, it in enumerate(items or ()):
            cur.execute("insert into cfg.preset_question (group_name, qid, text, sort_no) "
                        "values (%s,%s,%s,%s)",
                        (str(it.get("group") or ""), str(it.get("id") or ""),
                         str(it.get("text") or ""), i))
    clear_cache()
    return {"ok": True, "items": len(items or ())}
