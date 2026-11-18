# -*- coding: utf-8 -*-
"""观测：每次运行一条 run，每次模型/工具一条 span。"""
from __future__ import annotations

import time
import uuid

from psycopg.types.json import Jsonb

from app.agent import db


def start(session_id: str, user_id: int, model: str) -> str:
    run_id = str(uuid.uuid4())
    db.execute('insert into qa.agent_run (id, session_id, user_id, model, status) '
               'values (%s, %s, %s, %s, %s)', (run_id, session_id, user_id, model, 'running'))
    return run_id


def span(run_id: str, kind: str, name: str = '', payload=None, ms: int = 0) -> None:
    try:
        db.execute('insert into qa.agent_span (run_id, kind, name, payload, ms) '
                   'values (%s, %s, %s, %s, %s)',
                   (run_id, kind, name or '', Jsonb(payload or {}), int(ms or 0)))
    except Exception:                              # noqa: BLE001 - 观测失败不影响回答
        pass


def end(run_id: str, status: str, *, error: str = '', tokens: int = 0, ms: int = 0) -> None:
    if not run_id:
        return
    try:
        db.execute('update qa.agent_run set status=%s, error=%s, tokens=%s, ms=%s where id=%s',
                   (status, (error or '')[:2000], int(tokens or 0), int(ms or 0), run_id))
    except Exception:                              # noqa: BLE001
        pass


def runs(session_id: str = '', limit: int = 20) -> list[dict]:
    if session_id:
        return db.query('select * from qa.agent_run where session_id=%s '
                        'order by created_at desc limit %s', (session_id, limit))
    return db.query('select * from qa.agent_run order by created_at desc limit %s', (limit,))


def spans(run_id: str) -> list[dict]:
    return db.query('select * from qa.agent_span where run_id=%s order by id', (run_id,))
