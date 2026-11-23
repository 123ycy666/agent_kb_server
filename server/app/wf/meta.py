"""元数据：表清单、列清单、唯一键。所有下拉框的数据都来自这里。

两个库：
- `biz`：业务库（MySQL，只读查询 + 按文档回填）
- `fw` ：预警库（PostgreSQL，框架自己的表）

元数据里没登记的表与列，文档选不到，编译也过不了 —— 这就是「确实有对应的表」。
"""
from __future__ import annotations

import threading
import time

from sqlalchemy import text

BIZ = 'biz'
FW = 'fw'

DB_LABEL = {BIZ: '业务库', FW: '预警库'}

_TTL = 300.0
_lock = threading.Lock()
_cache: dict[str, dict] = {}
_stamp: dict[str, float] = {}


def _schema_biz() -> str:
    from app.secretary import config
    return config.SCHEMA


def _load_biz() -> dict:
    """业务库（MySQL）一次读全：表、列、唯一键。"""
    from app.secretary import tools_db

    schema = _schema_biz()
    tables: dict[str, dict] = {}

    r = tools_db._exec(
        'SELECT table_name AS t, table_comment AS c FROM information_schema.tables '
        'WHERE table_schema=%s ORDER BY table_name', [schema], limit_rows=5000)
    for row in r.get('rows') or []:
        name = str(row.get('t'))
        tables[name] = {'db': BIZ, 'name': name, 'comment': str(row.get('c') or ''),
                        'columns': [], 'uniques': [], 'primary': []}

    r = tools_db._exec(
        'SELECT table_name AS t, column_name AS c, data_type AS ty, column_type AS tyl, '
        'is_nullable AS nul, column_comment AS cm FROM information_schema.columns '
        'WHERE table_schema=%s ORDER BY table_name, ordinal_position', [schema], limit_rows=50000)
    for row in r.get('rows') or []:
        t = tables.get(str(row.get('t')))
        if t is None:
            continue
        t['columns'].append({'name': str(row.get('c')),
                             'type': str(row.get('tyl') or row.get('ty') or ''),
                             'nullable': str(row.get('nul')) == 'YES',
                             'comment': str(row.get('cm') or '')})

    r = tools_db._exec(
        "SELECT tc.table_name AS t, tc.constraint_type AS ct, kcu.column_name AS c "
        "FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu ON kcu.constraint_name=tc.constraint_name "
        "AND kcu.table_schema=tc.table_schema AND kcu.table_name=tc.table_name "
        "WHERE tc.table_schema=%s AND tc.constraint_type IN ('PRIMARY KEY','UNIQUE') "
        "ORDER BY tc.table_name, tc.constraint_name, kcu.ordinal_position", [schema], limit_rows=5000)
    for row in r.get('rows') or []:
        t = tables.get(str(row.get('t')))
        if t is None:
            continue
        if str(row.get('ct')) == 'PRIMARY KEY':
            if str(row.get('c')) not in t['primary']:
                t['primary'].append(str(row.get('c')))
        else:
            t['uniques'].append([str(row.get('c'))])

    # MySQL 里没声明成约束的唯一索引不会出现在 table_constraints，补一遍 statistics
    r = tools_db._exec(
        'SELECT table_name AS t, index_name AS i, column_name AS c '
        'FROM information_schema.statistics WHERE table_schema=%s AND non_unique=0 '
        'ORDER BY table_name, index_name, seq_in_index', [schema], limit_rows=20000)
    seen: dict[tuple, list] = {}
    for row in r.get('rows') or []:
        seen.setdefault((str(row.get('t')), str(row.get('i'))), []).append(str(row.get('c')))
    for (tname, iname), cols in seen.items():
        t = tables.get(tname)
        if t is None or not cols:
            continue
        if iname == 'PRIMARY':
            if not t['primary']:
                t['primary'] = cols
            continue
        if cols not in t['uniques']:
            t['uniques'].append(cols)
    return tables


def _load_fw() -> dict:
    """预警库（PostgreSQL）的 public schema。"""
    from app.core.database import wf_engine

    tables: dict[str, dict] = {}
    with wf_engine.connect() as conn:
        # 注意：这里一律用位置取值。SQLAlchemy 的 Row 有个 t 属性（整个元组），
        # 用 row.t 取列的别名会拿到整行（2026-11-23 实测踩到）。
        for row in conn.execute(text(
                "SELECT c.relname, COALESCE(obj_description(c.oid),'') "
                "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' AND c.relkind='r' ORDER BY c.relname")):
            name, comment = str(row[0]), str(row[1] or '')
            tables[name] = {'db': FW, 'name': name, 'comment': comment,
                            'columns': [], 'uniques': [], 'primary': []}

        for row in conn.execute(text(
                "SELECT table_name, column_name, data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema='public' ORDER BY table_name, ordinal_position")):
            t = tables.get(str(row[0]))
            if t is None:
                continue
            t['columns'].append({'name': str(row[1]), 'type': str(row[2] or ''),
                                 'nullable': str(row[3]) == 'YES', 'comment': ''})

        for row in conn.execute(text(
                "SELECT tc.table_name, tc.constraint_type, kcu.column_name "
                "FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu "
                "  ON kcu.constraint_name=tc.constraint_name AND kcu.table_schema=tc.table_schema "
                " AND kcu.table_name=tc.table_name "
                "WHERE tc.table_schema='public' AND tc.constraint_type IN ('PRIMARY KEY','UNIQUE') "
                "ORDER BY tc.table_name, tc.constraint_name, kcu.ordinal_position")):
            t = tables.get(str(row[0]))
            if t is None:
                continue
            if str(row[1]) == 'PRIMARY KEY':
                if str(row[2]) not in t['primary']:
                    t['primary'].append(str(row[2]))
            else:
                t['uniques'].append([str(row[2])])
    return tables


def _load(db: str) -> dict:
    return _load_fw() if db == FW else _load_biz()


def all_tables(db: str, use_cache: bool = True) -> dict:
    now = time.time()
    with _lock:
        hit = _cache.get(db)
        if use_cache and hit is not None and now - _stamp.get(db, 0) < _TTL:
            return hit
    data = _load(db)
    with _lock:
        _cache[db] = data
        _stamp[db] = time.time()
    return data


def refresh(db: str | None = None) -> dict:
    with _lock:
        for k in ([db] if db else list(_cache)):
            _cache.pop(k, None)
            _stamp.pop(k, None)
    return {'ok': True, 'refreshed': db or 'all'}


def get_table(db: str, name: str) -> dict | None:
    return all_tables(db).get(str(name))


def recommend_key(table: dict) -> list[str]:
    """回填匹配键的推荐值：优先业务唯一键，其次主键。"""
    if table.get('uniques'):
        return list(min(table['uniques'], key=len))
    return list(table.get('primary') or [])


def list_tables(db: str, keyword: str = '') -> list[dict]:
    kw = str(keyword or '').strip().lower()
    out = []
    for t in all_tables(db).values():
        if kw and kw not in t['name'].lower() and kw not in (t.get('comment') or '').lower():
            continue
        out.append({'db': t['db'], 'name': t['name'], 'comment': t.get('comment') or '',
                    'column_count': len(t['columns']), 'key': recommend_key(t)})
    out.sort(key=lambda x: x['name'])
    return out


def columns_of(db: str, table: str) -> list[dict]:
    t = get_table(db, table)
    return list(t['columns']) if t else []


def column_names(db: str, table: str) -> list[str]:
    return [c['name'] for c in columns_of(db, table)]
