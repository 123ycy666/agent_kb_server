"""工作流框架接口：`/api/flows`。

文档（五段 spec）CRUD、编译预览、试跑、正式跑、触发器、运行记录、元数据。
旧接口 `/api/workflows` 原样保留，互不影响。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db_wf as get_db
from app.core.deps import CurrentActor, get_current_actor
from app.wf import engine, meta, sched, spec as spec_mod
from app.wf.models import Flow, FlowRun, FlowVersion, Trigger, TriggerFlow, ensure_tables

router = APIRouter(prefix='/flows', tags=['工作流框架'])

ensure_tables()


def _jr(data, status_code: int = 200):
    return JSONResponse(content=jsonable_encoder(data), status_code=status_code)


async def _body(request: Request) -> dict:
    try:
        data = await request.json()
    except Exception:                     # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def _flow_or_404(db: Session, flow_id: int) -> Flow:
    f = db.get(Flow, int(flow_id))
    if f is None or f.deleted_flag:
        raise HTTPException(status_code=404, detail={'code': 'FLOW_NOT_FOUND', 'message': '工作流不存在'})
    return f


def _spec_summary(spec: dict) -> dict:
    spec = spec_mod.normalize(spec or {})
    w = spec.get('write') or {}
    src = spec.get('source') or {}
    tables = [str(t.get('table') or '') for t in src.get('tables') or []]
    return {
        'source_tables': tables,
        'target': ('%s.%s' % (meta.DB_LABEL.get(str(w.get('db') or meta.BIZ), ''), w.get('table') or '')).strip('.'),
        'target_table': str(w.get('table') or ''),
        'target_columns': [str(m.get('target') or '') for m in w.get('map') or []],
        'mode': str(w.get('mode') or 'update'),
    }


def _last_run(db: Session, flow_id: int) -> dict | None:
    row = db.execute(select(FlowRun).where(FlowRun.flow_id == int(flow_id))
                     .order_by(FlowRun.id.desc()).limit(1)).scalars().first()
    if row is None:
        return None
    return {'id': row.id, 'status': row.status, 'at': row.finished_at or row.started_at,
            'selected_n': row.selected_n, 'hit_n': row.hit_n, 'written_n': row.written_n,
            'elapsed_ms': row.elapsed_ms, 'dry_run': bool(row.dry_run)}


def _flow_out(db: Session, f: Flow) -> dict:
    spec = spec_mod.normalize(f.spec or {})
    errors = spec_mod.validate(spec)
    return {
        'id': f.id, 'name': f.name, 'enabled': bool(f.enabled),
        'version': f.version, 'updated_by': f.updated_by, 'updated_at': f.updated_at,
        'spec': spec, 'summary': _spec_summary(spec),
        'complete': not errors, 'errors': errors,
        'last_run': _last_run(db, f.id),
        'note': spec.get('note') or '',
    }


# ── 元数据 ──────────────────────────────────────────────────────────────
@router.get('/meta/dbs', summary='可选的库')
def meta_dbs(actor: CurrentActor = Depends(get_current_actor)):
    return _jr([{'value': k, 'label': v} for k, v in meta.DB_LABEL.items()])


@router.get('/meta/tables', summary='表清单（下拉用）')
def meta_tables(db: str = meta.BIZ, keyword: str = '',
                actor: CurrentActor = Depends(get_current_actor)):
    return _jr({'db': db, 'items': meta.list_tables(db, keyword)})


@router.get('/meta/columns', summary='某表的列（下拉用）')
def meta_columns(db: str = meta.BIZ, table: str = '',
                 actor: CurrentActor = Depends(get_current_actor)):
    info = meta.get_table(db, table)
    if info is None:
        raise HTTPException(status_code=404, detail={'code': 'TABLE_NOT_FOUND', 'message': '元数据里没有这张表'})
    return _jr({'db': db, 'table': table, 'comment': info.get('comment') or '',
                'columns': info['columns'], 'key': meta.recommend_key(info)})


@router.post('/meta/refresh', summary='重新读一遍元数据')
def meta_refresh(db: str | None = None, actor: CurrentActor = Depends(get_current_actor)):
    return _jr(meta.refresh(db))


# ── 触发器 ──────────────────────────────────────────────────────────────
def _trigger_out(db: Session, t: Trigger) -> dict:
    flows = sched.flows_of(db, t.id)
    last = db.execute(select(FlowRun).where(FlowRun.flow_id.in_([f.id for f in flows] or [-1]))
                      .order_by(FlowRun.id.desc()).limit(1)).scalars().first() if flows else None
    rule = sched.rule_of(t)
    return {
        'id': t.id, 'name': t.name, 'enabled': bool(t.enabled),
        'times': rule['times'], 'rule': rule, 'rule_text': sched.rule_text(rule),
        'note': t.note or '', 'next': sched.next_runs(rule, 3),
        'flows': [{'id': f.id, 'name': f.name, 'enabled': bool(f.enabled)} for f in flows],
        'last': (None if last is None else {'id': last.id, 'status': last.status,
                                            'at': last.finished_at or last.started_at,
                                            'written_n': last.written_n}),
    }


@router.get('/triggers', summary='触发器列表')
def list_triggers(db: Session = Depends(get_db), actor: CurrentActor = Depends(get_current_actor)):
    rows = db.execute(select(Trigger).order_by(Trigger.id)).scalars().all()
    return _jr({'items': [_trigger_out(db, t) for t in rows], 'status': sched.status()})


@router.post('/triggers', summary='新建触发器')
async def create_trigger(request: Request, db: Session = Depends(get_db),
                         actor: CurrentActor = Depends(get_current_actor)):
    body = await _body(request)
    name = str(body.get('name') or '').strip()
    if not name:
        raise HTTPException(status_code=422, detail={'code': 'NO_NAME', 'message': '触发器要有名字'})
    if db.execute(select(Trigger).where(Trigger.name == name)).scalars().first() is not None:
        raise HTTPException(status_code=409, detail={'code': 'NAME_TAKEN', 'message': '名字重复'})
    times = [str(x).strip() for x in (body.get('times') or []) if str(x).strip()]
    rule = sched.normalize_rule(body.get('rule') or {}, times)
    t = Trigger(name=name[:120], times=rule['times'], rule=rule,
                enabled=1 if body.get('enabled', True) else 0,
                note=str(body.get('note') or ''))
    db.add(t)
    db.commit()
    _set_trigger_flows(db, t, body.get('flow_ids') or [])
    sched.reload()
    return _jr(_trigger_out(db, t))


@router.put('/triggers/{tid}', summary='改触发器')
async def save_trigger(tid: int, request: Request, db: Session = Depends(get_db),
                       actor: CurrentActor = Depends(get_current_actor)):
    t = db.get(Trigger, int(tid))
    if t is None:
        raise HTTPException(status_code=404, detail={'code': 'TRIGGER_NOT_FOUND', 'message': '触发器不存在'})
    body = await _body(request)
    if 'name' in body:
        name = str(body.get('name') or '').strip()
        if not name:
            raise HTTPException(status_code=422, detail={'code': 'NO_NAME', 'message': '触发器要有名字'})
        dup = db.execute(select(Trigger).where(Trigger.name == name, Trigger.id != t.id)).scalars().first()
        if dup is not None:
            raise HTTPException(status_code=409, detail={'code': 'NAME_TAKEN', 'message': '名字重复'})
        t.name = name[:120]
    if 'times' in body or 'rule' in body:
        rule = sched.normalize_rule(body.get('rule') or {}, body.get('times'))
        t.rule = rule
        t.times = rule['times']
    if 'enabled' in body:
        t.enabled = 1 if body.get('enabled') else 0
    if 'note' in body:
        t.note = str(body.get('note') or '')
    db.commit()
    if 'flow_ids' in body:
        _set_trigger_flows(db, t, body.get('flow_ids') or [])
    sched.reload()
    return _jr(_trigger_out(db, t))


@router.delete('/triggers/{tid}', summary='删触发器')
def delete_trigger(tid: int, db: Session = Depends(get_db),
                   actor: CurrentActor = Depends(get_current_actor)):
    t = db.get(Trigger, int(tid))
    if t is None:
        raise HTTPException(status_code=404, detail={'code': 'TRIGGER_NOT_FOUND', 'message': '触发器不存在'})
    db.query(TriggerFlow).filter(TriggerFlow.trigger_id == t.id).delete()
    db.delete(t)
    db.commit()
    sched.reload()
    return _jr({'ok': True})


@router.post('/triggers/{tid}/run', summary='立刻跑一次这个触发器')
def run_trigger(tid: int, db: Session = Depends(get_db),
                actor: CurrentActor = Depends(get_current_actor)):
    t = db.get(Trigger, int(tid))
    if t is None:
        raise HTTPException(status_code=404, detail={'code': 'TRIGGER_NOT_FOUND', 'message': '触发器不存在'})
    try:
        out = sched.run_trigger_now(t.id, force=True)
    except Exception as exc:              # noqa: BLE001
        raise HTTPException(status_code=500, detail={'code': 'RUN_FAILED', 'message': str(exc)[:300]}) from exc
    return _jr(out)


def _set_trigger_flows(db: Session, t: Trigger, flow_ids) -> None:
    want = [int(x) for x in (flow_ids or [])]
    db.query(TriggerFlow).filter(TriggerFlow.trigger_id == t.id).delete()
    for i, fid in enumerate(want):
        db.add(TriggerFlow(trigger_id=t.id, flow_id=fid, seq=i))
    db.commit()


# ── 运行记录 ────────────────────────────────────────────────────────────
@router.get('/runs', summary='运行记录')
def list_runs(flow_id: int | None = None, limit: int = 100, db: Session = Depends(get_db),
              actor: CurrentActor = Depends(get_current_actor)):
    q = select(FlowRun).order_by(FlowRun.id.desc()).limit(max(1, min(limit, 500)))
    if flow_id:
        q = q.where(FlowRun.flow_id == int(flow_id))
    rows = db.execute(q).scalars().all()
    return _jr({'items': [{
        'id': r.id, 'flow_id': r.flow_id, 'flow_name': r.flow_name,
        'flow_version': r.flow_version, 'trigger': r.trigger, 'status': r.status,
        'dry_run': bool(r.dry_run), 'started_at': r.started_at, 'finished_at': r.finished_at,
        'elapsed_ms': r.elapsed_ms, 'selected_n': r.selected_n, 'hit_n': r.hit_n,
        'written_n': r.written_n, 'error': r.error,
    } for r in rows]})


@router.get('/runs/{rid}', summary='一轮的详情')
def run_detail(rid: int, db: Session = Depends(get_db),
               actor: CurrentActor = Depends(get_current_actor)):
    r = db.get(FlowRun, int(rid))
    if r is None:
        raise HTTPException(status_code=404, detail={'code': 'RUN_NOT_FOUND', 'message': '记录不存在'})
    return _jr({'id': r.id, 'flow_id': r.flow_id, 'flow_name': r.flow_name,
                'flow_version': r.flow_version, 'trigger': r.trigger, 'status': r.status,
                'dry_run': bool(r.dry_run), 'started_at': r.started_at,
                'finished_at': r.finished_at, 'elapsed_ms': r.elapsed_ms,
                'selected_n': r.selected_n, 'hit_n': r.hit_n, 'written_n': r.written_n,
                'sql_text': r.sql_text, 'error': r.error})


# ── 文档 ────────────────────────────────────────────────────────────────
@router.get('', summary='工作流列表')
def list_flows(db: Session = Depends(get_db), actor: CurrentActor = Depends(get_current_actor)):
    rows = db.execute(select(Flow).where(Flow.deleted_flag == 0).order_by(Flow.id)).scalars().all()
    items = []
    for f in rows:
        out = _flow_out(db, f)
        out.pop('spec', None)
        out.pop('errors', None)
        items.append(out)
    return _jr({'items': items})


@router.post('', summary='新建工作流')
async def create_flow(request: Request, db: Session = Depends(get_db),
                      actor: CurrentActor = Depends(get_current_actor)):
    body = await _body(request)
    name = str(body.get('name') or '').strip()
    if not name:
        raise HTTPException(status_code=422, detail={'code': 'NO_NAME', 'message': '工作流要有名字'})
    if db.execute(select(Flow).where(Flow.name == name,
                                     Flow.deleted_flag == 0)).scalars().first() is not None:
        raise HTTPException(status_code=409, detail={'code': 'NAME_TAKEN', 'message': '名字重复'})
    spec = spec_mod.normalize(body.get('spec') or spec_mod.blank())
    f = Flow(name=name[:120], spec=spec, enabled=0, version=1, updated_by=actor.username)
    db.add(f)
    db.commit()
    db.add(FlowVersion(flow_id=f.id, version=1, spec=spec, updated_by=actor.username))
    db.commit()
    return _jr(_flow_out(db, f))


@router.get('/{flow_id}', summary='工作流详情')
def get_flow(flow_id: int, db: Session = Depends(get_db),
             actor: CurrentActor = Depends(get_current_actor)):
    f = _flow_or_404(db, flow_id)
    out = _flow_out(db, f)
    vers = db.execute(select(FlowVersion).where(FlowVersion.flow_id == f.id)
                      .order_by(FlowVersion.version.desc()).limit(50)).scalars().all()
    out['versions'] = [{'version': v.version, 'updated_by': v.updated_by, 'at': v.created_at}
                       for v in vers]
    out['templates'] = _enum()
    return _jr(out)


@router.put('/{flow_id}', summary='保存工作流（保存即校验）')
async def save_flow(flow_id: int, request: Request, db: Session = Depends(get_db),
                    actor: CurrentActor = Depends(get_current_actor)):
    f = _flow_or_404(db, flow_id)
    body = await _body(request)
    if 'name' in body:
        name = str(body.get('name') or '').strip()
        if not name:
            raise HTTPException(status_code=422, detail={'code': 'NO_NAME', 'message': '工作流要有名字'})
        dup = db.execute(select(Flow).where(Flow.name == name, Flow.id != f.id,
                                            Flow.deleted_flag == 0)).scalars().first()
        if dup is not None:
            raise HTTPException(status_code=409, detail={'code': 'NAME_TAKEN', 'message': '名字重复'})
        f.name = name[:120]

    new_spec = None
    if 'spec' in body:
        new_spec = spec_mod.normalize(body.get('spec') or {})
    want_spec = new_spec if new_spec is not None else spec_mod.normalize(f.spec or {})
    errors = spec_mod.validate(want_spec)
    if errors and (new_spec is not None or body.get('enabled')):
        return _jr({'detail': {'code': 'SPEC_INVALID',
                               'message': '文档还没填对：' + errors[0]['message'],
                               'errors': errors}}, 422)

    if new_spec is not None:
        f.spec = new_spec
        f.version = int(f.version or 1) + 1
        db.add(FlowVersion(flow_id=f.id, version=f.version, spec=new_spec,
                           updated_by=actor.username))
    if 'enabled' in body:
        f.enabled = 1 if body.get('enabled') else 0
    f.updated_by = actor.username
    db.commit()
    return _jr(_flow_out(db, f))


@router.delete('/{flow_id}', summary='删除工作流')
def delete_flow(flow_id: int, db: Session = Depends(get_db),
                actor: CurrentActor = Depends(get_current_actor)):
    f = _flow_or_404(db, flow_id)
    f.deleted_flag = 1
    f.enabled = 0
    db.query(TriggerFlow).filter(TriggerFlow.flow_id == f.id).delete()
    db.commit()
    sched.reload()
    return _jr({'ok': True})


@router.post('/{flow_id}/duplicate', summary='复制一份')
def duplicate_flow(flow_id: int, db: Session = Depends(get_db),
                   actor: CurrentActor = Depends(get_current_actor)):
    f = _flow_or_404(db, flow_id)
    base = (f.name + ' 副本')[:110]
    cand, i = base, 2
    while db.execute(select(Flow).where(Flow.name == cand,
                                        Flow.deleted_flag == 0)).scalars().first() is not None:
        cand, i = ('%s %d' % (base, i))[:120], i + 1
    nf = Flow(name=cand, spec=dict(f.spec or {}), enabled=0, version=1, updated_by=actor.username)
    db.add(nf)
    db.commit()
    return _jr(_flow_out(db, nf))


@router.post('/{flow_id}/compile', summary='编译预览（不跑）')
def compile_flow(flow_id: int, request_body: dict | None = None,
                 db: Session = Depends(get_db), actor: CurrentActor = Depends(get_current_actor)):
    f = _flow_or_404(db, flow_id)
    out = engine.compile_flow(f)
    return _jr(out, 200 if out.get('ok') else 422)


@router.post('/{flow_id}/dry-run', summary='试跑：不写库')
def dry_run_flow(flow_id: int, db: Session = Depends(get_db),
                 actor: CurrentActor = Depends(get_current_actor)):
    f = _flow_or_404(db, flow_id)
    try:
        return _jr(engine.run_flow(db, f, trigger='test', dry=True))
    except engine.FlowError as exc:
        raise HTTPException(status_code=422, detail={'code': 'SPEC_INVALID', 'message': str(exc)}) from exc
    except engine.RunError as exc:
        raise HTTPException(status_code=500, detail={'code': 'RUN_FAILED', 'message': str(exc)}) from exc


@router.post('/{flow_id}/run', summary='正式跑：回填目标表')
def run_flow(flow_id: int, db: Session = Depends(get_db),
             actor: CurrentActor = Depends(get_current_actor)):
    f = _flow_or_404(db, flow_id)
    if not f.enabled:
        raise HTTPException(status_code=422, detail={'code': 'FLOW_DISABLED', 'message': '工作流还没启用'})
    try:
        return _jr(engine.run_flow(db, f, trigger='manual', dry=False))
    except engine.FlowError as exc:
        raise HTTPException(status_code=422, detail={'code': 'SPEC_INVALID', 'message': str(exc)}) from exc
    except engine.RunError as exc:
        raise HTTPException(status_code=500, detail={'code': 'RUN_FAILED', 'message': str(exc)}) from exc


def _enum() -> dict:
    return {
        'cond_ops': [{'value': k, 'label': v} for k, v in spec_mod.COND_OPS.items()],
        'row_policy': [{'value': 'first', 'label': '取第一行'},
                       {'value': 'latest', 'label': '取最新一行'},
                       {'value': 'max', 'label': '取该列最大的一行'},
                       {'value': 'min', 'label': '取该列最小的一行'}],
        'mode': [{'value': 'update', 'label': '只更新已有行'},
                 {'value': 'upsert', 'label': '有则更新，没有就插入'},
                 {'value': 'insert', 'label': '只插入新行'}],
        'join': [{'value': 'inner', 'label': '内连'}, {'value': 'left', 'label': '左连'}],
    }
