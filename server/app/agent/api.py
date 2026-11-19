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


class ConfigIn(BaseModel):
    style: str | None = None
    tools_off: list[str] | None = None
    skills: dict | None = None


def _when_not(t) -> str:
    """从工具 docstring 里取「别用在：…」那一段（页面上只读展示）。"""
    for line in (getattr(t, 'description', '') or '').split('\n'):
        if line.strip().startswith('别用在'):
            return line.strip()
    return ''


@router.get('/agent/config', summary='主智能体的配置')
def read_config(actor: CurrentActor = Depends(get_current_actor)):
    from app.agent import config, runtime, skills
    from app.secretary import prompt_text
    off = runtime.disabled_tools()
    mode, names = runtime.skills_scope()
    return {
        'style': runtime.style_text(),
        'tools': [{'name': t.name, 'on': t.name not in off, 'when_not': _when_not(t)}
                  for t in runtime.TOOLS],
        'skills': {'mode': mode, 'names': names, 'available': skills.names(int(actor.user_id or 0))},
        'identity': (prompt_text.get('identity', '') or '').strip(),
        'model': {'name': config.llm()[2], 'max_turns': config.MAX_TURNS,
                  'context_budget': config.CONTEXT_BUDGET, 'compact_at': config.COMPACT_AT},
    }


@router.get('/agent/overview', summary='概览页：今日轮次与失败数')
def overview(actor: CurrentActor = Depends(get_current_actor)):
    _ensure()
    row = db.one("select count(*) as turns, "
                 "count(*) filter (where status not in ('done', 'running')) as failed, "
                 "count(distinct session_id) as sessions "
                 "from qa.agent_run where created_at::date = current_date")
    return {'today_turns': int(row.get('turns') or 0),
            'today_failed': int(row.get('failed') or 0),
            'today_sessions': int(row.get('sessions') or 0)}


@router.put('/agent/config', summary='改主智能体的配置（保存即生效）')
def write_config(body: ConfigIn, actor: CurrentActor = Depends(get_current_actor)):
    from app.secretary import prompt_text
    who = str(actor.user_id or '')
    out: dict = {}
    if body.style is not None:
        out['style'] = prompt_text.save('agent_style', body.style, who)
    if body.tools_off is not None:
        out['tools'] = prompt_text.save(
            'agent_tools', json.dumps(body.tools_off, ensure_ascii=False), who)
    if body.skills is not None:
        out['skills'] = prompt_text.save(
            'agent_skills', json.dumps(body.skills, ensure_ascii=False), who)
    return out
