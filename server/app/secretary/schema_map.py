# -*- coding: utf-8 -*-
"""全库结构（定稿 H14.2 的兜底）：读 `information_schema` 与 PG 注释，不另存一份表清单。

页面「库结构」直接渲染 `overview()`；字段说明取自 `col_description`，
`set_comment()` 把页面上的修改写回 `COMMENT ON COLUMN`，让 psql、代码、页面看到同一句。
"""
from __future__ import annotations

from psycopg import sql
from psycopg.rows import dict_row

from app.secretary import pgdb

GROUPS = ('kb', 'cfg', 'qa', 'base')

# 只登记「已知有页面」的表；没登记的在页面上标红，这正是 H14.2 要看的数。
SEED_PAGE = {
    'kb.document': '知识库',
    'kb.knowledge_base': '知识库',
    'kb.spec': '知识库',
    'kb.spec_version': '知识库',
    'cfg.inject_text': '提示词',
    'cfg.inject_budget': '提示词',
    'qa.t_chat_query_trace': '记录',
    'qa.t_chat_trace_step': '记录',
}


def _q(query, params=None, fetch=True):
    import psycopg
    with psycopg.connect(pgdb.DSN, autocommit=True, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(query, params or ())
            return cur.fetchall() if fetch and cur.description else []


def ensure():
    _q("""create table if not exists cfg.page_entry (
              table_name varchar(120) primary key,
              page varchar(80) not null default '',
              updated_at timestamptz not null default now())""")
    for full, page in SEED_PAGE.items():
        _q("insert into cfg.page_entry (table_name, page) values (%s,%s) "
           "on conflict (table_name) do nothing", (full, page))


def overview() -> list[dict]:
    ensure()
    tables = _q("""select t.table_schema s, t.table_name t,
                          obj_description((quote_ident(t.table_schema) || '.' ||
                                           quote_ident(t.table_name))::regclass, 'pg_class') c
                     from information_schema.tables t
                    where t.table_schema = any(%s) and t.table_type = 'BASE TABLE'
                    order by 1, 2""", (list(GROUPS),))
    cols: dict = {}
    for c in _q("""select table_schema s, table_name t, column_name c
                     from information_schema.columns
                    where table_schema = any(%s)""", (list(GROUPS),)):
        cols.setdefault('%s.%s' % (c['s'], c['t']), []).append(c['c'])
    pages = {r['table_name']: r['page'] for r in _q("select * from cfg.page_entry")}
    out = []
    for r in tables:
        full = '%s.%s' % (r['s'], r['t'])
        try:
            n = int(_q('select count(*) c from %s.%s' % (r['s'], r['t']))[0]['c'])
        except Exception:                      # noqa: BLE001 - 行数取不到不影响结构展示
            n = -1
        out.append({'group': r['s'], 'table': r['t'], 'zh': r['c'] or '',
                    'purpose': r['c'] or '', 'documented': bool(r['c']),
                    'cols': len(cols.get(full, [])), 'rows': n,
                    'page': pages.get(full, '')})
    return out


def table_detail(full: str) -> dict:
    s, t = full.split('.', 1)
    cols = _q("""select c.column_name n, c.data_type d, c.is_nullable nl, c.column_default df,
                        col_description((quote_ident(c.table_schema) || '.' ||
                                         quote_ident(c.table_name))::regclass,
                                        c.ordinal_position::int) cm
                   from information_schema.columns c
                  where c.table_schema = %s and c.table_name = %s
                  order by c.ordinal_position""", (s, t))
    rows = _q(sql.SQL('select * from {}.{} limit 20').format(
        sql.Identifier(s), sql.Identifier(t)))
    return {'columns': [{'name': c['n'], 'type': c['d'], 'nullable': c['nl'] == 'YES',
                         'default': c['df'], 'comment': c['cm'] or ''} for c in cols],
            'rows': rows}


def set_comment(full: str, column: str, text: str) -> dict:
    s, t = full.split('.', 1)
    _q(sql.SQL('comment on column {}.{}.{} is {}').format(
        sql.Identifier(s), sql.Identifier(t), sql.Identifier(column), sql.Literal(text or '')),
       fetch=False)
    return {'ok': True, 'table': full, 'column': column}


def set_table_comment(full: str, text: str) -> dict:
    s, t = full.split('.', 1)
    _q(sql.SQL('comment on table {}.{} is {}').format(
        sql.Identifier(s), sql.Identifier(t), sql.Literal(text or '')), fetch=False)
    return {'ok': True, 'table': full}


def set_page(full: str, page: str) -> dict:
    ensure()
    _q("insert into cfg.page_entry (table_name, page, updated_at) values (%s,%s,now()) "
       "on conflict (table_name) do update set page=excluded.page, updated_at=now()",
       (full, page or ''))
    return {'ok': True, 'table': full, 'page': page}
