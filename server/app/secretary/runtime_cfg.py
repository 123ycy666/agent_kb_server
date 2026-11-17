# -*- coding: utf-8 -*-
"""运行配置：模型、业务库连接、步数、语句超时。

存在 PG 的 `cfg.runtime`（k/v），页面上改；代码里的 `.env` 只是默认值。
页面存了就以页面为准，下一个请求生效（换了库会自动重连）。
"""
from __future__ import annotations

import time

import psycopg
from psycopg.rows import dict_row

TTL = 5.0
_CACHE: dict = {'at': 0.0, 'rows': {}}

KEYS = {
    'model': '问数用的模型',
    'max_steps': '单题最多几轮工具调用',
    'db_host': '业务库地址',
    'db_port': '业务库端口',
    'db_name': '业务库库名',
    'db_user': '业务库账号（只读）',
    'db_password': '业务库密码',
    'sql_timeout_ms': '单条 SQL 超时（毫秒）',
    'tool_result_chars': '单次工具结果给模型的字数上限',
}


def _conn():
    from app.secretary import pgdb
    return psycopg.connect(pgdb.DSN, autocommit=True, row_factory=dict_row)


def ensure() -> None:
    try:
        with _conn() as c:
            cur = c.cursor()
            cur.execute("""create table if not exists cfg.runtime (
                              k varchar(40) primary key,
                              v text not null default '',
                              updated_at timestamptz not null default now())""")
    except Exception as exc:                          # noqa: BLE001
        print('[runtime] 建表失败：%s: %s' % (type(exc).__name__, str(exc)[:120]), flush=True)


def all(refresh: bool = False) -> dict:
    now = time.time()
    if not refresh and _CACHE['rows'] and (now - _CACHE['at']) < TTL:
        return dict(_CACHE['rows'])
    rows = {}
    try:
        with _conn() as c:
            cur = c.cursor()
            cur.execute('select k, v from cfg.runtime')
            rows = {str(r['k']): str(r['v'] or '') for r in cur.fetchall()}
    except Exception as exc:                          # noqa: BLE001
        print('[runtime] 读配置失败（用代码默认）：%s: %s' % (type(exc).__name__, str(exc)[:120]),
              flush=True)
    _CACHE['rows'] = rows
    _CACHE['at'] = now
    return dict(rows)


def save(data: dict, user: str = '') -> dict:
    """存页面传来的键（只认知名的键）；空串 = 恢复成代码默认。"""
    clean = {k: str(v) for k, v in (data or {}).items() if k in KEYS}
    try:
        with _conn() as c:
            cur = c.cursor()
            for k, v in clean.items():
                cur.execute('insert into cfg.runtime (k, v, updated_at) values (%s,%s,now()) '
                            'on conflict (k) do update set v=excluded.v, updated_at=now()',
                            (k, v))
    except Exception as exc:                          # noqa: BLE001
        return {'error': '保存失败：%s: %s' % (type(exc).__name__, str(exc)[:160])}
    _CACHE['at'] = 0.0
    refresh()
    return {'ok': True, 'saved': len(clean), 'values': all(refresh=True)}


def refresh() -> dict:
    """把页面上的值同步到 config（含业务库连接）。"""
    from app.secretary import config, tools_db
    vals = all(refresh=True)

    if vals.get('model'):
        config.MODEL = vals['model']
    for key, attr, cast in (('max_steps', 'MAX_STEPS', int),
                            ('sql_timeout_ms', 'SQL_TIMEOUT_MS', int),
                            ('tool_result_chars', 'TOOL_RESULT_CHARS', int)):
        if vals.get(key):
            try:
                setattr(config, attr, cast(vals[key]))
            except Exception:                         # noqa: BLE001
                pass

    db = dict(config.DB)
    changed = False
    if vals.get('db_host'):
        db['host'] = vals['db_host']
        changed = True
    if vals.get('db_port'):
        try:
            db['port'] = int(vals['db_port'])
            changed = True
        except Exception:                             # noqa: BLE001
            pass
    if vals.get('db_name'):
        db['db'] = vals['db_name']
        changed = True
    if vals.get('db_user'):
        db['user'] = vals['db_user']
        changed = True
    if vals.get('db_password'):
        db['password'] = vals['db_password']
        changed = True
    if changed and db != config.DB:
        config.DB = db
        config.SCHEMA = db.get('db') or config.SCHEMA
        try:
            tools_db._drop_conn()
        except Exception:                             # noqa: BLE001
            pass
        print('[runtime] 业务库连接已切换：%s/%s' % (db.get('host'), db.get('db')), flush=True)
    return vals


def test_conn(target: dict | None = None) -> dict:
    """用页面上的（或当前的）连接试一次 select 1，只回能不能连。"""
    import pymysql

    from app.secretary import config
    t = dict(config.DB)
    for k in ('db_host', 'db_port', 'db_name', 'db_user', 'db_password'):
        v = (target or {}).get(k)
        if v:
            t[{'db_host': 'host', 'db_port': 'port', 'db_name': 'db',
               'db_user': 'user', 'db_password': 'password'}[k]] = (
                int(v) if k == 'db_port' else v)
    t.setdefault('port', 3306)
    try:
        c = pymysql.connect(cursorclass=pymysql.cursors.DictCursor, connect_timeout=6,
                            charset='utf8mb4', **t)
        try:
            cur = c.cursor()
            cur.execute('select 1 as ok')
            row = cur.fetchone()
            cur.close()
        finally:
            c.close()
        return {'ok': True, 'result': row, 'host': t.get('host'), 'db': t.get('db')}
    except Exception as exc:                          # noqa: BLE001 - 只是试连
        return {'ok': False, 'error': '%s: %s' % (type(exc).__name__, str(exc)[:200])}
