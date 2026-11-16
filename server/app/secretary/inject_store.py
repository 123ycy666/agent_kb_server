# -*- coding: utf-8 -*-
"""注入文本的存取（阶段 3）：`cfg.inject_text` + `cfg.inject_budget`。

为什么单独一张表（需求 H10.1）：注入文本要独立成页（身份/红线/优先级/工具说明/数字纪律），
而它原来散在 6 处（prompt_text / prompt_material / 代码常量 / document / 个人库 / MySQL ai_prompt）。
这里只做"一份真身 + 一个页面"：其它读口全部转到这里读（V15）。

不新增"只有代码认识"的键：`KEYS` 里每一项都在页面上有行、有字数、有来源、有生效方式。
"""
from __future__ import annotations

import time

from psycopg.rows import dict_row

from app.secretary import pgdb

TTL = 60
_CACHE: dict = {'rows': None, 'at': 0.0}
_READY = False

# 键 → (中文名, 默认正文, 谁能改)
KEYS = {
    'identity': ('身份', '', '可改'),
    'redlines': ('红线（S0）', '', '可改'),
    'priority': ('谁说了算（S1）', '', '可改'),
    'tools_guide': ('工具说明', '', '可改'),
    'number_discipline': ('数字纪律', '', '可改'),
}


def _q(sql, params=None):
    import psycopg
    with psycopg.connect(pgdb.DSN, autocommit=True, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchall() if cur.description else []


def ensure() -> None:
    global _READY
    if _READY:
        return
    _q("""create table if not exists cfg.inject_text (
              k           varchar(40) primary key,
              label       varchar(80) not null default '',
              body        text not null default '',
              sort_no     integer not null default 100,
              editable    boolean not null default true,
              source      varchar(16) not null default 'db',
              version_no  integer not null default 1,
              updated_by  varchar(64) not null default '',
              updated_at  timestamptz not null default now())""")
    _q("""create table if not exists cfg.inject_budget (
              k           varchar(40) primary key,
              v           integer not null,
              note        varchar(200) not null default '',
              updated_at  timestamptz not null default now())""")
    for i, (k, (label, _d, _e)) in enumerate(KEYS.items(), 1):
        _q("insert into cfg.inject_text (k, label, sort_no) values (%s,%s,%s) "
           "on conflict (k) do update set label=excluded.label, sort_no=excluded.sort_no",
           (k, label, i * 10))
    _q("insert into cfg.inject_budget (k, v, note) values ('resident_chars', 6000, "
       "'常驻上下文上限（需求 H2.2）') on conflict (k) do nothing")
    _READY = True


def invalidate() -> None:
    _CACHE['rows'] = None
    _CACHE['at'] = 0.0


def _rows() -> dict:
    now = time.time()
    hit = _CACHE['rows']
    if hit is not None and now - _CACHE['at'] < TTL:
        return hit
    ensure()
    try:
        rows = {r['k']: r for r in _q("select * from cfg.inject_text")}
    except Exception as exc:                       # noqa: BLE001
        print('[inject] 读注入文本失败（按空）：%s: %s' % (type(exc).__name__, exc), flush=True)
        rows = {}
    _CACHE['rows'] = rows
    _CACHE['at'] = now
    return rows


def get(key, default='') -> str:
    row = _rows().get(key) or {}
    body = str(row.get('body') or '')
    if body.strip():
        return body
    return default or KEYS.get(key, ('', '', ''))[1]


def budget() -> int:
    ensure()
    try:
        row = _q("select v from cfg.inject_budget where k='resident_chars'")
        return int(row[0]['v']) if row else 6000
    except Exception:                              # noqa: BLE001
        return 6000


def save(key, body, user='') -> dict:
    if key not in KEYS:
        return {'error': '不认识的注入项：%s（可用：%s）' % (key, '、'.join(KEYS))}
    ensure()
    _q("""insert into cfg.inject_text (k, label, body, updated_by, updated_at)
          values (%s,%s,%s,%s,now())
          on conflict (k) do update set body=excluded.body,
                version_no=cfg.inject_text.version_no+1,
                updated_by=excluded.updated_by, updated_at=now()""",
       (key, KEYS[key][0], str(body or ''), str(user or '')))
    invalidate()
    return {'ok': True, 'k': key, 'chars': len(str(body or ''))}


def set_budget(v, user='') -> dict:
    ensure()
    v = int(v)
    if v < 1000 or v > 40000:
        return {'error': '预算取 1000–40000 字之间'}
    _q("insert into cfg.inject_budget (k, v, note) values ('resident_chars', %s, %s) "
       "on conflict (k) do update set v=excluded.v, updated_at=now()",
       (v, '由 %s 改' % (user or '页面')))
    invalidate()
    return {'ok': True, 'resident_chars': v}


def items() -> list[dict]:
    """页面用：每项显字数 / 来源 / 最近修改（V14、H15.3）。"""
    rows = _rows()
    out = [{'k': k, 'label': label, 'chars': len(str((rows.get(k) or {}).get('body') or '')),
            'source': '库 · cfg.inject_text', 'state': '可改',
            'updated_by': (rows.get(k) or {}).get('updated_by') or '',
            'version_no': (rows.get(k) or {}).get('version_no') or 0,
            'updated_at': str((rows.get(k) or {}).get('updated_at') or '')}
           for k, (label, _d, _e) in KEYS.items()]
    return out


def seed_from_legacy() -> dict:
    """把 prompt_text 里的 identity 迁到 inject_text，并删掉旧行（一份真身）。

    redlines / priority 本来就没存过表（prompt_text 里 0 行），不用迁。
    """
    from app.secretary import prompt_text
    ensure()
    moved = []
    cur = (get('identity') or '').strip()
    if cur:
        return {'moved': moved, 'note': 'inject_text.identity 已有内容，不覆盖'}
    legacy = (prompt_text.get('identity', '') or '').strip()
    if legacy:
        save('identity', legacy, user='seed')
        moved.append('identity')
        try:
            prompt_text.reset('identity')
        except Exception as exc:                   # noqa: BLE001
            print('[inject] 删旧 identity 行失败：%s' % exc, flush=True)
    return {'moved': moved}
