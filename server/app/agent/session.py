# -*- coding: utf-8 -*-
"""会话：事件日志 + 压缩。实现 OpenAI Agents SDK 的 Session 协议。"""
from __future__ import annotations

import json
import uuid

from psycopg.types.json import Jsonb

from app.agent import config, db


def _tokens(payload) -> int:
    try:
        return max(1, len(json.dumps(payload, ensure_ascii=False, default=str)) // 2)
    except Exception:                              # noqa: BLE001
        return 1


def create(user_id: int, title: str = '', session_id: str | None = None) -> str:
    sid = session_id or str(uuid.uuid4())
    db.ensure()
    db.execute(
        'insert into qa.agent_session (id, user_id, title) values (%s, %s, %s) '
        'on conflict (id) do nothing', (sid, user_id, (title or '')[:60]))
    return sid


def ensure(session_id: str, user_id: int) -> None:
    db.execute(
        'insert into qa.agent_session (id, user_id, title) values (%s, %s, %s) '
        'on conflict (id) do nothing', (session_id, user_id, ''))


def list_sessions(user_id: int, limit: int = 50) -> list[dict]:
    return db.query(
        'select id, title, status, created_at, updated_at from qa.agent_session '
        'where user_id=%s and deleted_flag=0 order by updated_at desc limit %s',
        (user_id, limit))


def touch(session_id: str, title: str = '') -> None:
    if title:
        db.execute('update qa.agent_session set updated_at=now(), title=case when '
                   "title='' then %s else title end where id=%s", (title[:60], session_id))
    else:
        db.execute('update qa.agent_session set updated_at=now() where id=%s', (session_id,))


def items(session_id: str, limit: int | None = None) -> list[dict]:
    sql = ('select id, type, payload, tokens from qa.agent_item '
           'where session_id=%s and not archived order by id')
    rows = db.query(sql, (session_id,))
    if limit:
        rows = rows[-limit:]
    return rows


def history_for_ui(session_id: str) -> list[dict]:
    rows = db.query(
        'select id, type, payload, created_at from qa.agent_item '
        'where session_id=%s order by id', (session_id,))
    return rows


def add(session_id: str, items_: list[dict], type_: str = '') -> None:
    """写会话项。type_ 为空时按 item 自身推断。"""
    with db.conn() as c:
        for it in items_:
            t = type_ or _type_of(it)
            c.execute(
                'insert into qa.agent_item (session_id, type, payload, tokens) '
                'values (%s, %s, %s, %s)',
                (session_id, t, Jsonb(it), _tokens(it)))


def _type_of(it: dict) -> str:
    t = str(it.get('type') or '')
    role = str(it.get('role') or '')
    if t == 'message':
        return 'user' if role == 'user' else 'assistant'
    if not t and role in ('user', 'assistant'):
        return role
    if t == 'function_call':
        return 'tool_call'
    if t == 'function_call_output':
        return 'tool_result'
    return t or 'item'


class PGSession:
    """SDK Session 协议：get_items / add_items / pop_item / clear_session。"""

    def __init__(self, session_id: str):
        self.session_id = session_id

    async def get_items(self, limit: int | None = None) -> list[dict]:
        rows = items(self.session_id, limit=limit)
        return [r['payload'] for r in rows]

    async def add_items(self, items_: list[dict]) -> None:
        add(self.session_id, items_)

    async def pop_item(self) -> dict | None:
        row = db.one('select id, payload from qa.agent_item where session_id=%s '
                     'order by id desc limit 1', (self.session_id,))
        if not row:
            return None
        db.execute('delete from qa.agent_item where id=%s', (row['id'],))
        return row['payload']

    async def clear_session(self) -> None:
        db.execute('delete from qa.agent_item where session_id=%s', (self.session_id,))


def maybe_compact(session_id: str, model: str) -> dict:
    """超过预算时把最老的一段压成一条摘要项；原文只标 archived，不删。"""
    rows = items(session_id)
    if not rows:
        return {'compacted': False}
    total = sum(int(r.get('tokens') or 0) for r in rows)
    limit = int(config.CONTEXT_BUDGET * config.COMPACT_AT)
    if total <= limit:
        return {'compacted': False, 'tokens': total}

    keep = max(2, int(config.KEEP_RECENT))
    old = rows[:-keep]
    if len(old) < 4:
        return {'compacted': False, 'tokens': total, 'why': '老的项太少，先不压'}

    lines = []
    for r in old:
        p = r.get('payload') or {}
        body = json.dumps(p, ensure_ascii=False, default=str)
        lines.append('[%s] %s' % (r.get('type'), body[:1500]))
    text = '\n'.join(lines)[:60000]

    from app.secretary import agent as A
    try:
        resp = A._chat([
            {'role': 'system', 'content': '把下面的会话记录压成一段摘要。保留：用户要过什么、'
                                          '做过哪些决定、生成过哪些文件（文件名）、还差什么没做完。'
                                          '丢掉：重复的工具结果、寒暄。只输出摘要正文。'},
            {'role': 'user', 'content': text},
        ], model, think=False, use_tools=False)
        summary = (resp['choices'][0]['message'].get('content') or '').strip()
    except Exception as exc:                       # noqa: BLE001 - 压缩失败不影响回答
        return {'compacted': False, 'error': str(exc)[:200], 'tokens': total}
    if not summary:
        return {'compacted': False, 'tokens': total}

    item = {'role': 'user',
            'content': [{'type': 'input_text',
                         'text': '【更早会话的摘要】\n' + summary[:8000]}]}
    add(session_id, [item], type_='summary')
    ids = [r['id'] for r in old]
    db.execute('update qa.agent_item set archived=true where id = any(%s)', (ids,))
    return {'compacted': True, 'from': total, 'summary_chars': len(summary)}
