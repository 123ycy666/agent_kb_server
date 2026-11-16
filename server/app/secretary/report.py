# -*- coding: utf-8 -*-
"""四件套落库（需求 H6.1、H13.2）：答案 / 数字来源 / 用了哪篇说明 / 自检结果。

落在 `qa.t_chat_query_trace` 的四个 jsonb 列上（会话消息那边只存正文），
页面「记录」与「评测」都从这里读，所以它是结构化字段，不是把答案再解析一遍。
"""
from __future__ import annotations

import json

from psycopg.rows import dict_row

from app.secretary import pgdb

_READY = False
COLS = ('spec_used', 'self_check', 'number_sources', 'injection')


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
    for c in COLS:
        _q("alter table qa.t_chat_query_trace add column if not exists %s jsonb not null "
           "default '%s'::jsonb" % (c, '[]' if c in ('number_sources',) else '{}'))
    _q("alter table qa.t_chat_query_trace add column if not exists speaker varchar(64) "
       "not null default ''")
    _READY = True


def save(qa_id, result) -> dict:
    """把四件套写回这一轮的 trace 行。qa_id 为空（没落库）就跳过。"""
    if not qa_id:
        return {'ok': False, 'why': '没有 qa_id'}
    ensure()
    try:
        _q("""update qa.t_chat_query_trace set spec_used=%s::jsonb, self_check=%s::jsonb,
                     number_sources=%s::jsonb, injection=%s::jsonb where id=%s""",
           (json.dumps(result.get('spec_used') or {}, ensure_ascii=False),
            json.dumps(result.get('self_check') or {}, ensure_ascii=False),
            json.dumps(result.get('number_sources') or [], ensure_ascii=False),
            json.dumps(result.get('injection') or {}, ensure_ascii=False),
            int(qa_id)))
        return {'ok': True, 'qa_id': int(qa_id)}
    except Exception as exc:                       # noqa: BLE001 - 四件套写失败不影响交付
        print('[report] 四件套落库失败：%s: %s' % (type(exc).__name__, exc), flush=True)
        return {'ok': False, 'why': str(exc)[:200]}


def load(qa_id) -> dict:
    ensure()
    rows = _q("""select id, raw_question, spec_used, self_check, number_sources, injection,
                        elapsed_ms, feedback, create_time
                   from qa.t_chat_query_trace where id=%s""", (int(qa_id),))
    return rows[0] if rows else {}


def recent(limit=50, only_office=True) -> list[dict]:
    ensure()
    sql = """select id, raw_question, spec_used, self_check, number_sources, elapsed_ms,
                    feedback, create_time
               from qa.t_chat_query_trace where deleted_flag = 0"""
    if only_office:
        sql += " and spec_used <> '{}'::jsonb"
    sql += ' order by id desc limit %s'
    return _q(sql, (int(limit),))
