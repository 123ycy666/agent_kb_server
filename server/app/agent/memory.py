# -*- coding: utf-8 -*-
"""记忆：会话内笔记、跨会话偏好/事实。只存摘要，不自动全量注入。"""
from __future__ import annotations

from app.agent import db


def remember(user_id: int, kind: str, text: str) -> dict:
    text = (text or '').strip()
    if not text:
        return {'ok': False, 'error': '要记的内容是空的'}
    kind = (kind or 'fact').strip().lower()
    if kind not in ('preference', 'fact', 'artifact'):
        kind = 'fact'
    db.execute('insert into qa.agent_memory (user_id, kind, text) values (%s, %s, %s)',
               (user_id, kind, text[:4000]))
    return {'ok': True}


def recall(user_id: int, query: str, top_k: int = 5) -> list[dict]:
    q = (query or '').strip()
    if not q:
        rows = db.query('select kind, text from qa.agent_memory where user_id=%s '
                        'order by id desc limit %s', (user_id, top_k))
    else:
        rows = db.query(
            'select kind, text from qa.agent_memory where user_id=%s and text ilike %s '
            'order by id desc limit %s', (user_id, '%' + q + '%', top_k))
    return rows


def about(user_id: int, limit: int = 5) -> str:
    """「关于我」：只放偏好，最多几行。"""
    rows = db.query(
        "select text from qa.agent_memory where user_id=%s and kind='preference' "
        'order by id desc limit %s', (user_id, limit))
    return '\n'.join('- ' + r['text'][:200] for r in rows)


def save_note(session_id: str, key: str, body: str) -> dict:
    key = (key or '').strip()[:40]
    if not key:
        return {'ok': False, 'error': 'key 不能空'}
    db.execute(
        'insert into qa.agent_note (session_id, key, body) values (%s, %s, %s) '
        'on conflict (session_id, key) do update set body=excluded.body, updated_at=now()',
        (session_id, key, body or ''))
    return {'ok': True}


def read_note(session_id: str, key: str) -> dict:
    row = db.one('select key, body, updated_at from qa.agent_note '
                 'where session_id=%s and key=%s', (session_id, key))
    return row or {'ok': False, 'error': '没有这条笔记'}
