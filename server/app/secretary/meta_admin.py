# -*- coding: utf-8 -*-
"""数据表元数据的「覆盖层」（2026-11-02）。

业务库（`dlj_data`）里的 `ai_data.ai_table_metadata` / `ai_column_metadata` **改不了** ——
本系统对业务库的连接是只读的（`tools_db.run_sql` 只放 SELECT，实测连 `show tables` 都被拒）。

所以这里做一层覆盖：本系统 PG 存"我改过什么"，`semantic._build()` 读完业务库后套一层。
好处是业务库加了表、改了注释，这边自动跟着走；这边只记差异，**完全不写业务库**。

覆盖粒度：表级 `cn_name / desc / level`，字段级 `cn_name / desc`。
"""
from __future__ import annotations

from app.secretary import pgdb

KIND_TABLE = 'table'
KIND_COLUMN = 'column'
FIELDS = ('cn_name', 'desc', 'level')          # level 只对表有效

DDL = ('CREATE TABLE IF NOT EXISTS data_meta_override ('
       'id bigserial PRIMARY KEY, '
       "kind varchar(8) NOT NULL, "              # table / column
       'table_name varchar(64) NOT NULL, '
       "column_name varchar(64) NOT NULL DEFAULT '', "
       "field varchar(16) NOT NULL, "            # cn_name / desc / level
       'value text NOT NULL, '
       "updated_by varchar(64) NOT NULL DEFAULT '', "
       'updated_at timestamptz NOT NULL DEFAULT now(), '
       'UNIQUE (kind, table_name, column_name, field))')

_TTL = 60.0
_CACHE: dict = {'at': 0.0, 'rows': None, 'by': None}


def ensure() -> None:
    """建表（幂等）。db_init 也会建一次，这里兜底给老库补上。"""
    try:
        conn = pgdb.connect(autocommit=True)
        cur = conn.cursor()
        try:
            cur.execute(DDL)
        finally:
            cur.close()
            conn.close()
    except Exception as exc:                       # noqa: BLE001
        print('[meta_admin] 建表失败：%s: %s' % (type(exc).__name__, str(exc)[:160]), flush=True)


def clear_cache() -> None:
    _CACHE['rows'] = None
    _CACHE['by'] = None
    _CACHE['at'] = 0.0


def rows() -> list[dict]:
    """全部覆盖行（TTL 60 秒）。"""
    import time
    now = time.time()
    if _CACHE['rows'] is not None and (now - _CACHE['at']) < _TTL:
        return _CACHE['rows']
    out: list[dict] = []
    try:
        conn = pgdb.connect(autocommit=True)
        cur = conn.cursor()
        try:
            cur.execute('SELECT id, kind, table_name, column_name, field, value, updated_at '
                        'FROM data_meta_override ORDER BY table_name, column_name, field')
            out = [dict(r) for r in (cur.fetchall() or [])]
        finally:
            cur.close()
            conn.close()
    except Exception as exc:                       # noqa: BLE001
        print('[meta_admin] 读覆盖失败（按无覆盖）：%s: %s'
              % (type(exc).__name__, str(exc)[:120]), flush=True)
        return _CACHE['rows'] or []
    _CACHE['rows'] = out
    _CACHE['at'] = now
    return out


def _index() -> dict:
    """{(kind, table, column, field): value}，套覆盖时用。"""
    import time
    now = time.time()
    if _CACHE['by'] is not None and (now - _CACHE['at']) < _TTL:
        return _CACHE['by']
    by = {}
    for r in rows():
        by[(r['kind'], r['table_name'], r['column_name'] or '', r['field'])] = r['value']
    _CACHE['by'] = by
    return by


def save(kind: str, table_name: str, field: str, value: str,
         column_name: str = '', user: str = '') -> dict:
    kind = str(kind or '').strip()
    table_name = str(table_name or '').strip()
    column_name = str(column_name or '').strip()
    field = str(field or '').strip()
    if kind not in (KIND_TABLE, KIND_COLUMN):
        return {'error': 'kind 只能是 table / column'}
    if not table_name:
        return {'error': 'table_name 不能为空'}
    if kind == KIND_COLUMN and not column_name:
        return {'error': '改字段必须给 column_name'}
    if field not in FIELDS:
        return {'error': 'field 只能是 %s' % ' / '.join(FIELDS)}
    if kind == KIND_COLUMN and field == 'level':
        return {'error': '字段级没有 level'}
    value = str(value if value is not None else '')
    conn = None
    try:
        ensure()
        conn = pgdb.connect(autocommit=False)
        cur = conn.cursor()
        try:
            if not value.strip():
                # 值清空 = 取消这条覆盖，回退业务库原值
                cur.execute('DELETE FROM data_meta_override WHERE kind=%s AND table_name=%s '
                            'AND column_name=%s AND field=%s',
                            (kind, table_name, column_name, field))
            else:
                cur.execute(
                    'INSERT INTO data_meta_override (kind, table_name, column_name, field, '
                    'value, updated_by, updated_at) VALUES (%s,%s,%s,%s,%s,%s,now()) '
                    'ON CONFLICT (kind, table_name, column_name, field) DO UPDATE '
                    'SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by, '
                    'updated_at = now()',
                    (kind, table_name, column_name, field, value, str(user or '')[:64]))
        finally:
            cur.close()
        conn.commit()
        clear_cache()
        return {'ok': True, 'kind': kind, 'table': table_name, 'column': column_name,
                'field': field, 'cleared': not value.strip()}
    except Exception as exc:                       # noqa: BLE001
        try:
            if conn is not None:
                conn.rollback()
        except Exception:                          # noqa: BLE001
            pass
        return {'error': '保存失败：%s' % str(exc)[:200]}
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:                          # noqa: BLE001
            pass


def apply(payload: dict) -> dict:
    """把覆盖值套到 semantic 的元数据上（表级 + 字段级）。原地改，返回 payload。"""
    by = _index()
    if not by:
        return payload
    for row in payload.get('tables') or []:
        t = row.get('table_name')
        for field, key in (('cn_name', 'table_cn_name'), ('desc', 'business_desc'),
                           ('level', 'level')):
            v = by.get((KIND_TABLE, t, '', field))
            if v is not None:
                row[key] = v
    for t, cols in (payload.get('cols') or {}).items():
        for c, row in cols.items():
            for field, key in (('cn_name', 'column_cn_name'), ('desc', 'business_desc')):
                v = by.get((KIND_COLUMN, t, c, field))
                if v is not None:
                    row[key] = v
    return payload
