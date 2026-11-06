# -*- coding: utf-8 -*-
"""工作流框架接口：文档 / 调度 / 运行 / 事项 / 发送记录 / 参数。

口径：登录用户都能改（与 /prompts 一致，不自行加管理员闸）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import CurrentActor, get_current_actor
from app.workflow import docs as docsdb
from app.workflow import scheduler as wfsched
from app.workflow import store
from app.workflow.models import Item, RuleDoc, Run, Schedule
from app.workflow.runner import run_doc

router = APIRouter(prefix='/workflows', tags=['工作流'])

store.ensure_tables()


def _jr(data, status_code: int = 200):
    return JSONResponse(content=jsonable_encoder(data), status_code=status_code)


async def _body(request: Request) -> dict:
    try:
        data = await request.json()
    except Exception:                    # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def _doc_or_404(db: Session, doc_id: int) -> RuleDoc:
    d = docsdb.get_doc(db, doc_id)
    if d is None:
        raise HTTPException(status_code=404, detail={'code': 'DOC_NOT_FOUND', 'message': '文档不存在'})
    return d


# ── 文档 ────────────────────────────────────────────────────────────────
@router.get('/templates/{work_type}', summary='某工种的空文档模板')
def templates(work_type: str, actor: CurrentActor = Depends(get_current_actor)):
    tpl = docsdb.TEMPLATES.get(work_type) or docsdb.TEMPLATES['detect']
    return _jr({'work_type': work_type, 'label': tpl['label'],
                'sections': [{'key': k, 'title': t, 'hint': h, 'example': e}
                             for k, t, h, e in tpl['sections']]})


@router.get('/docs', summary='文档列表')
def list_docs(work_type: str | None = None, db: Session = Depends(get_db),
              actor: CurrentActor = Depends(get_current_actor)):
    return _jr({'items': docsdb.list_docs(db, work_type=work_type)})


@router.post('/docs', summary='新建文档')
async def create_doc(request: Request, db: Session = Depends(get_db),
                     actor: CurrentActor = Depends(get_current_actor)):
    body = await _body(request)
    name = str(body.get('name') or '').strip()
    if not name:
        raise HTTPException(status_code=422, detail={'code': 'NO_NAME', 'message': '文档名必填'})
    dup = db.execute(select(RuleDoc).where(RuleDoc.name == name[:120])).scalars().first()
    if dup is not None:
        raise HTTPException(status_code=409, detail={'code': 'NAME_TAKEN', 'message': '文档名已存在'})
    d = docsdb.create_doc(db, name=name, work_type=str(body.get('work_type') or 'detect'),
                          alert_type=str(body.get('alert_type') or ''),
                          scope_level=str(body.get('scope_level') or 'city'),
                          updated_by=actor.username)
    return _jr(docsdb.dict_of(d))


@router.get('/docs/{doc_id}', summary='文档详情（含版本）')
def get_doc(doc_id: int, db: Session = Depends(get_db),
            actor: CurrentActor = Depends(get_current_actor)):
    d = _doc_or_404(db, doc_id)
    out = docsdb.dict_of(d)
    out['versions'] = docsdb.versions(db, d.id)
    out['sections'] = [{'key': k, 'title': t, 'hint': h, 'example': e}
                       for k, t, h, e in docsdb.sections_of(d.work_type)]
    return _jr(out)


@router.put('/docs/{doc_id}', summary='保存文档（version+1）')
async def save_doc(doc_id: int, request: Request, db: Session = Depends(get_db),
                   actor: CurrentActor = Depends(get_current_actor)):
    d = _doc_or_404(db, doc_id)
    body = await _body(request)
    docsdb.save_doc(db, d, name=body.get('name'), doc=body.get('doc'),
                    alert_type=body.get('alert_type'),
                    scope_level=body.get('scope_level'),
                    schedule_id=body.get('schedule_id', ('KEEP',)),
                    enabled=body.get('enabled'),
                    disabled_reason=body.get('disabled_reason'),
                    updated_by=actor.username)
    # 停用 = 退出调度：立刻从定时方案上解挂，避免"停用了还在跑"
    detached = None
    if body.get('enabled') is False and d.schedule_id:
        old = d.schedule_id
        d.schedule_id = None
        db.commit()
        sch = db.get(Schedule, int(old))
        detached = sch.name if sch else str(old)
    wfsched.reload()
    out = docsdb.dict_of(d)
    out['detached_from'] = detached
    return _jr(out)


@router.delete('/docs/{doc_id}', summary='删除文档（逻辑删）')
def delete_doc(doc_id: int, db: Session = Depends(get_db),
               actor: CurrentActor = Depends(get_current_actor)):
    d = _doc_or_404(db, doc_id)
    docsdb.delete_doc(db, d)
    wfsched.reload()
    return _jr({'ok': True})


@router.post('/docs/{doc_id}/duplicate', summary='复制为新文档')
async def duplicate_doc(doc_id: int, request: Request, db: Session = Depends(get_db),
                        actor: CurrentActor = Depends(get_current_actor)):
    d = _doc_or_404(db, doc_id)
    body = await _body(request)
    nd = docsdb.duplicate(db, d, name=str(body.get('name') or ''), updated_by=actor.username)
    return _jr(docsdb.dict_of(nd))


@router.get('/docs/{doc_id}/export', summary='导出 markdown')
def export_doc(doc_id: int, db: Session = Depends(get_db),
               actor: CurrentActor = Depends(get_current_actor)):
    d = _doc_or_404(db, doc_id)
    md = docsdb.export_markdown(d)
    from urllib.parse import quote
    name = quote((d.name or 'doc') + '.md')
    return Response(content=md, media_type='text/markdown; charset=utf-8',
                    headers={'Content-Disposition': "attachment; filename*=UTF-8''" + name})


@router.post('/docs/{doc_id}/import', summary='从 markdown 导入（按 ## 标题覆盖对应节）')
async def import_doc(doc_id: int, request: Request, db: Session = Depends(get_db),
                     actor: CurrentActor = Depends(get_current_actor)):
    d = _doc_or_404(db, doc_id)
    body = await _body(request)
    md = str(body.get('markdown') or '')
    if not md.strip():
        raise HTTPException(status_code=422, detail={'code': 'EMPTY', 'message': '内容为空'})
    doc = docsdb.import_markdown(d, md)
    docsdb.save_doc(db, d, doc=doc, updated_by=actor.username)
    return _jr(docsdb.dict_of(d))


@router.get('/docs/{doc_id}/versions/{version}', summary='看某版本正文')
def get_version(doc_id: int, version: int, db: Session = Depends(get_db),
                actor: CurrentActor = Depends(get_current_actor)):
    body = docsdb.version_doc(db, doc_id, version)
    if body is None:
        raise HTTPException(status_code=404, detail={'code': 'VERSION_NOT_FOUND',
                                                     'message': '没有这一版'})
    return _jr({'version': version, 'doc': body})


@router.post('/docs/{doc_id}/dry-run', summary='测试：跑一遍但不写结果与事项')
async def dry_run(doc_id: int, request: Request, db: Session = Depends(get_db),
                  actor: CurrentActor = Depends(get_current_actor)):
    d = _doc_or_404(db, doc_id)
    body = await _body(request)
    today = str(body.get('today') or '') or None
    missing = docsdb.missing_sections(d.work_type, d.doc or {})
    if missing:
        raise HTTPException(status_code=422, detail={
            'code': 'DOC_INCOMPLETE', 'message': '文档还没填完：' + '、'.join(missing),
            'missing': missing})
    try:
        r = run_doc(db, d.id, trigger='test', dry=True, today=today)
    except Exception as exc:             # noqa: BLE001
        raise HTTPException(status_code=500, detail={
            'code': 'RUN_FAILED', 'message': '%s: %s' % (type(exc).__name__, str(exc)[:300])})
    return _jr(r)


@router.post('/run', summary='手工跑一份文档（真跑）')
async def manual_run(request: Request, db: Session = Depends(get_db),
                     actor: CurrentActor = Depends(get_current_actor)):
    body = await _body(request)
    doc_id = int(body.get('doc_id') or 0)
    d = _doc_or_404(db, doc_id)
    missing = docsdb.missing_sections(d.work_type, d.doc or {})
    if missing:
        raise HTTPException(status_code=422, detail={
            'code': 'DOC_INCOMPLETE', 'message': '文档还没填完：' + '、'.join(missing),
            'missing': missing})
    try:
        return _jr(run_doc(db, d.id, trigger='manual', dry=bool(body.get('dry'))))
    except Exception as exc:             # noqa: BLE001
        raise HTTPException(status_code=500, detail={
            'code': 'RUN_FAILED', 'message': '%s: %s' % (type(exc).__name__, str(exc)[:300])})


# ── 调度方案 ────────────────────────────────────────────────────────────
def _check_mountable(db: Session, doc_ids) -> None:
    """只有启用的文档能挂到定时方案上。"""
    want = [int(x) for x in (doc_ids or [])]
    if not want:
        return
    rows = db.execute(select(RuleDoc).where(RuleDoc.id.in_(want),
                                            RuleDoc.deleted_flag == 0)).scalars().all()
    bad = [d.name for d in rows if not d.enabled]
    if bad:
        raise HTTPException(status_code=422, detail={
            'code': 'DOC_DISABLED', 'message': '这些文档已停用，不能挂载：' + '、'.join(bad)})


def _sched_dict(db: Session, s: Schedule) -> dict:
    docs = docsdb.docs_of_schedule(db, s.id)
    crons = [c for c in (s.crons or []) if str(c or '').strip()] or ([s.cron] if s.cron else [])
    return {'id': s.id, 'name': s.name, 'cron': (crons[0] if crons else ''), 'crons': crons,
            'timezone': s.timezone,
            'enabled': bool(s.enabled), 'note': s.note, 'doc_count': len(docs),
            'doc_ids': [d.id for d in docs], 'doc_names': [d.name for d in docs],
            'next': wfsched.next_runs(crons, 5),
            'cron_error': next((e for e in (wfsched.validate_cron(c) for c in crons) if e), '')}


def _crons_of(body: dict) -> list[str]:
    """接受 crons 列表或单个 cron 字符串；返回去空后的列表。"""
    raw = body.get('crons')
    if raw is None:
        raw = [body.get('cron')] if body.get('cron') else []
    if isinstance(raw, str):
        raw = [raw]
    return [str(c).strip() for c in raw if str(c or '').strip()]


@router.get('/schedules', summary='调度方案列表')
def list_schedules(db: Session = Depends(get_db), actor: CurrentActor = Depends(get_current_actor)):
    rows = db.execute(select(Schedule).order_by(Schedule.id)).scalars().all()
    return _jr({'items': [_sched_dict(db, s) for s in rows], 'status': wfsched.status()})


@router.post('/schedules', summary='新建调度方案')
async def create_schedule(request: Request, db: Session = Depends(get_db),
                          actor: CurrentActor = Depends(get_current_actor)):
    body = await _body(request)
    name = str(body.get('name') or '').strip()
    crons = _crons_of(body)
    if not name or not crons:
        raise HTTPException(status_code=422, detail={'code': 'BAD_SCHEDULE',
                                                     'message': '名称和运行时间都要填'})
    for c in crons:
        err = wfsched.validate_cron(c)
        if err:
            raise HTTPException(status_code=422, detail={'code': 'BAD_CRON', 'message': err})
    s = Schedule(name=name[:120], cron=crons[0], crons=crons,
                 enabled=1 if body.get('enabled', True) else 0,
                 note=body.get('note'))
    _check_mountable(db, body.get('doc_ids'))
    db.add(s)
    db.commit()
    want = {int(x) for x in (body.get('doc_ids') or [])}
    if want:
        for d in db.execute(select(RuleDoc).where(RuleDoc.id.in_(want),
                                                  RuleDoc.deleted_flag == 0)).scalars().all():
            d.schedule_id = s.id
        db.commit()
    wfsched.reload()
    return _jr(_sched_dict(db, s))


@router.put('/schedules/{sid}', summary='改调度方案（热重载）')
async def save_schedule(sid: int, request: Request, db: Session = Depends(get_db),
                        actor: CurrentActor = Depends(get_current_actor)):
    s = db.get(Schedule, int(sid))
    if s is None:
        raise HTTPException(status_code=404, detail={'code': 'SCHEDULE_NOT_FOUND',
                                                     'message': '方案不存在'})
    body = await _body(request)
    crons = _crons_of(body) if (body.get('crons') is not None or body.get('cron')) else None
    if crons is not None:
        if not crons:
            raise HTTPException(status_code=422, detail={'code': 'BAD_CRON',
                                                         'message': '至少要有一个运行时间'})
        for c in crons:
            err = wfsched.validate_cron(c)
            if err:
                raise HTTPException(status_code=422, detail={'code': 'BAD_CRON', 'message': err})
        s.crons = crons
        s.cron = crons[0]
    if body.get('name'):
        s.name = str(body.get('name'))[:120]
    if body.get('enabled') is not None:
        s.enabled = 1 if body.get('enabled') else 0
    if body.get('note') is not None:
        s.note = body.get('note')
    if body.get('doc_ids') is not None:
        _check_mountable(db, body.get('doc_ids'))
        want = {int(x) for x in (body.get('doc_ids') or [])}
        for d in db.execute(select(RuleDoc).where(RuleDoc.deleted_flag == 0)).scalars().all():
            if d.id in want and d.schedule_id != s.id:
                d.schedule_id = s.id
            elif d.id not in want and d.schedule_id == s.id:
                d.schedule_id = None
    db.commit()
    wfsched.reload()
    return _jr(_sched_dict(db, s))


@router.post('/schedules/{sid}/dry-run', summary='试跑该方案下的所有文档（不落库）')
def dry_run_schedule(sid: int, db: Session = Depends(get_db),
                     actor: CurrentActor = Depends(get_current_actor)):
    s = db.get(Schedule, int(sid))
    if s is None:
        raise HTTPException(status_code=404, detail={'code': 'SCHEDULE_NOT_FOUND',
                                                     'message': '方案不存在'})
    out = []
    for d in docsdb.docs_of_schedule(db, s.id):
        if not d.enabled:
            out.append({'doc': d.name, 'status': 'skipped', 'reason': '已停用'})
            continue
        missing = docsdb.missing_sections(d.work_type, d.doc or {})
        if missing:
            out.append({'doc': d.name, 'status': 'skipped', 'reason': '缺节：' + '、'.join(missing)})
            continue
        try:
            r = run_doc(db, d.id, trigger='dry', dry=True)
            out.append({'doc': d.name, 'run_id': r.get('run_id'), 'status': r.get('status'),
                        'checked_n': r.get('checked_n'), 'hit_n': r.get('hit_n')})
        except Exception as exc:         # noqa: BLE001
            out.append({'doc': d.name, 'status': 'failed', 'error': str(exc)[:200]})
    return _jr({'schedule': s.name, 'dry': True, 'docs': out})


@router.delete('/schedules/{sid}', summary='删调度方案（挂着文档时拒绝）')
def delete_schedule(sid: int, db: Session = Depends(get_db),
                    actor: CurrentActor = Depends(get_current_actor)):
    s = db.get(Schedule, int(sid))
    if s is None:
        raise HTTPException(status_code=404, detail={'code': 'SCHEDULE_NOT_FOUND',
                                                     'message': '方案不存在'})
    docs = docsdb.docs_of_schedule(db, s.id)
    if docs:
        raise HTTPException(status_code=409, detail={
            'code': 'SCHEDULE_IN_USE', 'message': '还有文档挂在这个方案上',
            'docs': [d.name for d in docs]})
    db.delete(s)
    db.commit()
    wfsched.reload()
    return _jr({'ok': True})


@router.post('/schedules/{sid}/run', summary='立即跑该方案下的文档')
def run_schedule(sid: int, db: Session = Depends(get_db),
                 actor: CurrentActor = Depends(get_current_actor)):
    s = db.get(Schedule, int(sid))
    if s is None:
        raise HTTPException(status_code=404, detail={'code': 'SCHEDULE_NOT_FOUND',
                                                     'message': '方案不存在'})
    out = []
    for d in docsdb.docs_of_schedule(db, s.id):
        if not d.enabled:
            out.append({'doc': d.name, 'status': 'skipped', 'reason': '已停用'})
            continue
        try:
            r = run_doc(db, d.id, trigger='manual')
            out.append({'doc': d.name, 'run_id': r.get('run_id'), 'status': r.get('status'),
                        'hit_n': r.get('hit_n', 0)})
        except Exception as exc:         # noqa: BLE001
            out.append({'doc': d.name, 'status': 'failed', 'error': str(exc)[:200]})
    return _jr({'schedule': s.name, 'docs': out})


# ── 运行记录 ────────────────────────────────────────────────────────────
@router.get('/runs', summary='运行批次')
def list_runs(doc_id: int | None = None, status: str | None = None,
              limit: int = Query(50, ge=1, le=200), offset: int = 0,
              db: Session = Depends(get_db), actor: CurrentActor = Depends(get_current_actor)):
    where = []
    if doc_id:
        where.append(Run.doc_id == int(doc_id))
    if status:
        where.append(Run.status == status)
    total = db.execute(select(func.count()).select_from(Run).where(*where)).scalar_one()
    rows = db.execute(select(Run).where(*where).order_by(Run.id.desc())
                      .limit(limit).offset(offset)).scalars().all()
    return _jr({'total': int(total), 'items': [_run_dict(r) for r in rows]})


def _run_dict(r: Run) -> dict:
    return {'id': r.id, 'doc_id': r.doc_id, 'doc_name': r.doc_name,
            'doc_version': r.doc_version, 'work_type': r.work_type,
            'trigger': r.trigger, 'status': r.status, 'dry_run': bool(r.dry_run),
            'started_at': r.started_at.isoformat() if r.started_at else None,
            'finished_at': r.finished_at.isoformat() if r.finished_at else None,
            'data_as_of': r.data_as_of, 'checked_n': r.checked_n, 'hit_n': r.hit_n,
            'new_n': r.new_n, 'closed_n': r.closed_n, 'sent_n': r.sent_n,
            'error': r.error}


@router.get('/runs/{run_id}', summary='一轮的步骤留痕')
def get_run(run_id: int, db: Session = Depends(get_db),
            actor: CurrentActor = Depends(get_current_actor)):
    r = db.get(Run, int(run_id))
    if r is None:
        raise HTTPException(status_code=404, detail={'code': 'RUN_NOT_FOUND',
                                                     'message': '批次不存在'})
    from app.workflow.models import RunLog
    logs = db.execute(select(RunLog).where(RunLog.run_id == r.id)
                      .order_by(RunLog.seq)).scalars().all()
    out = _run_dict(r)
    out['logs'] = [{'seq': l.seq, 'at': l.at.isoformat() if l.at else None,
                    'kind': l.kind, 'payload': l.payload} for l in logs]
    # 当时那份文档：优先取与运行版本一致的历史版本，取不到退回当前正文
    snap = docsdb.version_doc(db, r.doc_id, r.doc_version)
    doc = db.get(RuleDoc, r.doc_id)
    if snap is None and doc is not None:
        snap = doc.doc or {}
    out['doc_snapshot'] = snap or {}
    if doc is not None:
        out['sections'] = [{'key': k, 'title': t}
                           for k, t, *_ in docsdb.sections_of(doc.work_type)]
    return _jr(out)


# ── 事项 ────────────────────────────────────────────────────────────────
@router.get('/items', summary='事项列表')
def list_items(status: str | None = None, doc_id: int | None = None,
               alert_type: str | None = None, tier: str | None = None,
               q: str | None = None, limit: int = Query(100, ge=1, le=500), offset: int = 0,
               db: Session = Depends(get_db), actor: CurrentActor = Depends(get_current_actor)):
    return _jr(store.list_items(db, status=status or None, doc_id=doc_id,
                                alert_type=alert_type, tier=tier, q=q,
                                limit=limit, offset=offset))


@router.get('/items/{item_id}', summary='事项详情（含历史与发送记录）')
def get_item(item_id: int, db: Session = Depends(get_db),
             actor: CurrentActor = Depends(get_current_actor)):
    it = db.get(Item, int(item_id))
    if it is None:
        raise HTTPException(status_code=404, detail={'code': 'ITEM_NOT_FOUND',
                                                     'message': '事项不存在'})
    out = store.item_dict(it)
    doc = db.get(RuleDoc, it.doc_id)
    out['doc_name'] = doc.name if doc else ''
    out['history'] = store.item_history(db, it)
    out['notifies'] = store.item_notifies(db, it.id)
    return _jr(out)


@router.post('/items/{item_id}/close', summary='手工关闭（必填原因）')
async def close_item(item_id: int, request: Request, db: Session = Depends(get_db),
                     actor: CurrentActor = Depends(get_current_actor)):
    body = await _body(request)
    reason = str(body.get('reason') or '').strip()
    if not reason:
        raise HTTPException(status_code=422, detail={'code': 'NO_REASON', 'message': '关闭要写原因'})
    it = db.get(Item, int(item_id))
    if it is None:
        raise HTTPException(status_code=404, detail={'code': 'ITEM_NOT_FOUND',
                                                     'message': '事项不存在'})
    it.status = 'closed'
    it.close_reason = reason[:32]
    db.commit()
    return _jr(store.item_dict(it))


@router.post('/items/{item_id}/chat', summary='事项进对话')
def item_chat(item_id: int, db: Session = Depends(get_db),
              actor: CurrentActor = Depends(get_current_actor)):
    it = db.get(Item, int(item_id))
    if it is None:
        raise HTTPException(status_code=404, detail={'code': 'ITEM_NOT_FOUND',
                                                     'message': '事项不存在'})
    sid = 'wf-item-%d-%s' % (it.id, actor.username)
    d = store.item_dict(it)
    text = '**%s**\n\n当前值 %s / 阈值 %s / 档位 %s\n\n依据：%s' % (
        d['title'], d['value'], d['threshold'], d['tier'],
        '；'.join('%s=%s' % (k, v) for k, v in (d['evidence'] or {}).items()))
    got = store.append_chat(db, sid, actor.username, text,
                            meta={'kind': 'alert_item', 'item': d}, title='【工作流】' + d['title'])
    return _jr({'session_id': sid, 'chat_store': 'ok' if got else 'unavailable', 'item': d})


# ── 发送记录 / 参数 ─────────────────────────────────────────────────────
@router.get('/notifications', summary='发送记录')
def list_notifications(limit: int = Query(100, ge=1, le=500), offset: int = 0,
                       db: Session = Depends(get_db),
                       actor: CurrentActor = Depends(get_current_actor)):
    return _jr(store.list_notifies(db, limit=limit, offset=offset))


@router.get('/params', summary='运行参数')
def get_params(db: Session = Depends(get_db), actor: CurrentActor = Depends(get_current_actor)):
    return _jr({'items': [{'key': k, 'value': v[0], 'note': v[1]}
                          for k, v in sorted(store.params(db).items())]})


@router.put('/params', summary='改运行参数')
async def save_params(request: Request, db: Session = Depends(get_db),
                      actor: CurrentActor = Depends(get_current_actor)):
    body = await _body(request)
    for k, v in (body.get('items') or {}).items():
        store.set_param(db, str(k), str(v))
    return _jr({'items': [{'key': k, 'value': v[0], 'note': v[1]}
                          for k, v in sorted(store.params(db).items())]})
