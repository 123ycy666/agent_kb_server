# -*- coding: utf-8 -*-
"""智能体接口：SSE 运行、停止、会话与运行记录。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agent import db, runtime, session as sess, trace
from app.core.deps import CurrentActor, get_current_actor

router = APIRouter(tags=["智能体"])

_ensured = False


def _ensure() -> None:
    global _ensured
    if _ensured:
        return
    try:
        db.ensure()
        _ensured = True
    except Exception as exc:                       # noqa: BLE001
        print('[agent] 建表失败（继续跑，可能表已存在）：%s' % str(exc)[:200], flush=True)


class RunIn(BaseModel):
    text: str = ''
    session_id: str | None = None
    file_ids: list[str] = []


class StopIn(BaseModel):
    run_id: str


@router.post('/agent/run', summary='跑一轮智能体（SSE）')
async def run(body: RunIn, actor: CurrentActor = Depends(get_current_actor)):
    _ensure()
    if not (body.text or '').strip():
        raise HTTPException(status_code=400, detail={'code': 'EMPTY', 'message': '说点什么'})
    uid = int(actor.user_id or 0)

    async def gen():
        try:
            async for ev in runtime.run_stream(user_id=uid, text=body.text,
                                               session_id=body.session_id,
                                               file_ids=list(body.file_ids or []),
                                               actor=actor):
                yield 'event: %s\ndata: %s\n\n' % (
                    ev.get('type', 'message'), json.dumps(ev, ensure_ascii=False, default=str))
        except Exception as exc:                   # noqa: BLE001
            err = {'type': 'error', 'message': '%s: %s' % (type(exc).__name__, str(exc)[:300])}
            yield 'event: error\ndata: %s\n\n' % json.dumps(err, ensure_ascii=False)

    return StreamingResponse(gen(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache',
                                      'X-Accel-Buffering': 'no'})


@router.post('/agent/stop', summary='停止一轮')
async def stop(body: StopIn, actor: CurrentActor = Depends(get_current_actor)):
    runtime.stop(body.run_id)
    return {'ok': True}


@router.get('/agent/sessions', summary='我的会话')
async def list_sessions(actor: CurrentActor = Depends(get_current_actor)):
    _ensure()
    return {'items': sess.list_sessions(int(actor.user_id or 0))}


@router.get('/agent/sessions/{sid}', summary='会话详情')
async def get_session(sid: str, actor: CurrentActor = Depends(get_current_actor)):
    _ensure()
    row = db.one('select * from qa.agent_session where id=%s and deleted_flag=0', (sid,))
    if not row:
        raise HTTPException(status_code=404, detail={'code': 'NOT_FOUND', 'message': '没有这个会话'})
    return row


@router.get('/agent/sessions/{sid}/items', summary='会话项（前端回放）')
async def get_items(sid: str, actor: CurrentActor = Depends(get_current_actor)):
    _ensure()
    return {'items': sess.history_for_ui(sid)}


@router.delete('/agent/sessions/{sid}', summary='删除会话（软删）')
async def delete_session(sid: str, actor: CurrentActor = Depends(get_current_actor)):
    db.execute('update qa.agent_session set deleted_flag=1 where id=%s and user_id=%s',
               (sid, int(actor.user_id or 0)))
    return {'ok': True}


@router.get('/agent/runs', summary='运行记录')
async def runs(session_id: str = '', actor: CurrentActor = Depends(get_current_actor)):
    _ensure()
    return {'items': trace.runs(session_id=session_id)}


@router.get('/agent/runs/{run_id}/spans', summary='一轮的每一步')
async def spans(run_id: str, actor: CurrentActor = Depends(get_current_actor)):
    return {'items': trace.spans(run_id)}
