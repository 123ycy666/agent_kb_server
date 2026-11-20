# -*- coding: utf-8 -*-
"""纠错词表（单位类型 / 台区类型 / 停用词 / 单位前后缀 / 单字尾词）—— 存当前库 `cfg.t_nc_word`。

分工（FR-MIGR-002）：
- **词面在库**：`cfg.t_nc_word` 一行一个词，页面可改，切现场跟着换；
- **算法在代码**：`libs/name_correction_lib/name_corrector.py` 的匹配逻辑一个字不动。

`kind` 的取值与含义：

| kind | 用途 |
|---|---|
| `unit_kind` | 单位名的类型词（供电公司 / 供电所 / 供电服务站） |
| `unit_level` | 类型词的级别，`extra` 里是 `{"level": 0}`（数字小=级别高） |
| `unit_prefix` | 单位名前缀（国企名头），归一前先剥 |
| `unit_suffix` | 单位名后缀（长词优先，用于吸收「所 / 公司」这类尾巴） |
| `area_suffix` | 台区名类型后缀（公变 / 台区 / 变压器 …） |
| `suffix_hint` | 出现这些词才按「完整名称」去匹配 |
| `generic_word` | 组织通用词（拼音不许触发简称匹配） |
| `common_word` | 禁入常用词（同音的常用词不许被当地名改掉） |
| `asked_unit_suffix` | 用户口语里的级别词，`extra` 里是 `{"kinds": ["供电所"]}` |
| `ask_tail` | 单字级别尾词，`extra` = `{"single": "所", "block_next": "有以在…"}` |
| `strip_head` | 名称归一前可剥掉的地名前缀（目前只入库、算法未接，见交付说明） |
"""
from __future__ import annotations

import time

from app.secretary import pgdb

TTL = 60.0
_CACHE: dict = {"at": 0.0, "rows": None}

KINDS = ("unit_kind", "unit_level", "unit_prefix", "unit_suffix", "area_suffix",
         "suffix_hint", "generic_word", "common_word", "asked_unit_suffix", "ask_tail",
         "strip_head", "biz_word", "station_hint", "org_prefix")


def ensure() -> None:
    with pgdb.connect(autocommit=True) as c:
        c.cursor().execute("""create table if not exists cfg.t_nc_word (
                                  id bigserial primary key,
                                  kind varchar(24) not null,
                                  word varchar(80) not null,
                                  extra jsonb not null default '{}',
                                  sort_no int not null default 0,
                                  enabled boolean not null default true,
                                  updated_at timestamptz not null default now(),
                                  unique (kind, word))""")


def clear_cache() -> None:
    _CACHE.update({"rows": None, "at": 0.0})


def rows(refresh: bool = False) -> list[dict]:
    now = time.time()
    if not refresh and _CACHE["rows"] is not None and (now - _CACHE["at"]) < TTL:
        return list(_CACHE["rows"])
    try:
        with pgdb.connect(autocommit=True) as c:
            cur = c.cursor()
            cur.execute("select kind, word, extra, sort_no from cfg.t_nc_word "
                        "where enabled order by kind, sort_no, length(word) desc, word")
            out = [{"kind": str(r["kind"]), "word": str(r["word"]),
                    "extra": r["extra"] or {}, "sort_no": int(r["sort_no"] or 0)}
                   for r in cur.fetchall()]
    except Exception:                                 # noqa: BLE001 - 空库/连不上都当没有
        out = []
    _CACHE.update({"rows": out, "at": now})
    return list(out)


def load(refresh: bool = False) -> dict:
    """按 `name_corrector.configure()` 要的形状整理成一份词表。"""
    groups: dict = {k: [] for k in KINDS}
    for r in rows(refresh=refresh):
        if r["kind"] in groups:
            groups[r["kind"]].append(r)

    def words(kind: str) -> list[str]:
        return [r["word"] for r in groups[kind]]

    levels = {}
    for r in groups["unit_level"]:
        try:
            levels[r["word"]] = int((r["extra"] or {}).get("level"))
        except (TypeError, ValueError):
            continue
    asked = [{"word": r["word"],
              "kinds": list((r["extra"] or {}).get("kinds") or [])}
             for r in groups["asked_unit_suffix"]]
    ask_tail = (groups["ask_tail"][0]["extra"] if groups["ask_tail"] else {}) or {}

    return {
        "unit_kind": words("unit_kind"),
        "unit_level": levels,
        "unit_prefix": words("unit_prefix"),
        "unit_suffix": words("unit_suffix"),
        "area_suffix": words("area_suffix"),
        "suffix_hint": words("suffix_hint"),
        "generic_word": words("generic_word"),
        "common_word": words("common_word"),
        "asked_unit_suffix": asked,
        "single_char_tails": [str(ask_tail.get("single") or "").strip()] if ask_tail else [],
        "tail_block_next": str(ask_tail.get("block_next") or ""),
        "single_char_kinds": list(ask_tail.get("kinds") or []),
        "strip_head": words("strip_head"),
    }


def save(kind: str, word: str, extra: dict | None = None, sort_no: int = 0) -> dict:
    if kind not in KINDS:
        return {"error": "未知的词表类型：%s" % kind}
    w = (word or "").strip()
    if not w:
        return {"error": "词面为空"}
    import json

    ensure()
    with pgdb.connect(autocommit=True) as c:
        cur = c.cursor()
        cur.execute("insert into cfg.t_nc_word (kind, word, extra, sort_no, updated_at) "
                    "values (%s,%s,%s::jsonb,%s,now()) "
                    "on conflict (kind, word) do update set extra=excluded.extra, "
                    "sort_no=excluded.sort_no, enabled=true, updated_at=now()",
                    (kind, w, json.dumps(extra or {}, ensure_ascii=False), int(sort_no)))
    clear_cache()
    return {"ok": True, "kind": kind, "word": w}
