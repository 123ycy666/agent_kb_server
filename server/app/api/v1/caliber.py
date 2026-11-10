# -*- coding: utf-8 -*-
"""指标契约页面接口（2026-11-10）。

    GET  /api/v1/caliber/metrics                全部契约（含 base）
    PUT  /api/v1/caliber/metrics/{code}         新建或改一条（改完下一轮生效，不用重启）
    POST /api/v1/caliber/metrics/{code}/toggle  启停
    POST /api/v1/caliber/metrics/{code}/preview 把契约摊成 SQL 看一眼
    GET  /api/v1/caliber/metrics/{code}/history 历史版本
    POST /api/v1/caliber/check                  拿一段 SQL 过闸1
    GET  /api/v1/caliber/tables                 可选表（业务字典）
    GET  /api/v1/caliber/columns?table=xxx      可选列

权限口径同 /prompts（2026-11-02 Fred 定）：登录用户都能改，不设管理员闸。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.core.deps import CurrentActor, get_current_actor
from app.secretary import caliber

router = APIRouter(prefix='/caliber', tags=['指标契约'])


def _jr(data, status_code: int = 200):
    return JSONResponse(content=jsonable_encoder(data), status_code=status_code)


async def _json_body(request: Request):
    try:
        data = await request.json()
    except Exception:                              # noqa: BLE001
        return None, '请求体不是 JSON'
    if not isinstance(data, dict):
        return None, '请求体必须是 JSON 对象'
    return data, ''


@router.get('/metrics', summary='全部指标契约')
async def list_metrics(actor: CurrentActor = Depends(get_current_actor)):
    return _jr({'metrics': caliber.metrics(only_enabled=False)})


@router.put('/metrics/{code}', summary='新建或保存一条契约')
async def save_metric(code: str, request: Request,
                      actor: CurrentActor = Depends(get_current_actor)):
    body, err = await _json_body(request)
    if err:
        return _jr({'error': err}, 400)
    r = caliber.save(code, body, user=getattr(actor, 'username', '') or '')
    return _jr(r, 200 if r.get('ok') else 400)


@router.post('/metrics/{code}/toggle', summary='启用或停用')
async def toggle_metric(code: str, request: Request,
                        actor: CurrentActor = Depends(get_current_actor)):
    body, err = await _json_body(request)
    if err:
        return _jr({'error': err}, 400)
    r = caliber.toggle(code, bool(body.get('enabled', True)))
    return _jr(r, 200 if r.get('ok') else 400)


@router.post('/metrics/{code}/preview', summary='把契约摊成 SQL')
async def preview_metric(code: str, request: Request,
                         actor: CurrentActor = Depends(get_current_actor)):
    body, err = await _json_body(request)
    if err:
        return _jr({'error': err}, 400)
    plan = caliber.expand(code, year=body.get('year'), scope_id=body.get('scope_id'))
    if plan.get('error'):
        return _jr(plan, 400)
    return _jr({'sql': plan['sql'], 'expanded_expr': plan['expr'], 'unit': plan['unit'],
                'digits': plan['digits'], 'missing': plan.get('missing') or [],
                'need': plan.get('need') or {}})


@router.get('/metrics/{code}/history', summary='历史版本')
async def metric_history(code: str, actor: CurrentActor = Depends(get_current_actor)):
    return _jr({'versions': caliber.history(code)})


@router.post('/check', summary='一段 SQL 过闸1')
async def check(request: Request, actor: CurrentActor = Depends(get_current_actor)):
    body, err = await _json_body(request)
    if err:
        return _jr({'error': err}, 400)
    return _jr(caliber.check_sql(str(body.get('sql') or ''), year=body.get('year')))


@router.post('/parse', summary='从一段中文口径读出契约草稿')
async def parse(request: Request, actor: CurrentActor = Depends(get_current_actor)):
    body, err = await _json_body(request)
    if err:
        return _jr({'error': err}, 400)
    return _jr(caliber.parse_text(str(body.get('text') or '')))


@router.get('/try', summary='按契约取一次数（试跑）')
async def try_query(code: str = '', year: str = '', scope_id: str = '',
                    actor: CurrentActor = Depends(get_current_actor)):
    sid = scope_id.strip() or None
    if sid is not None:
        try:
            sid = int(sid)
        except ValueError:
            return _jr({'error': 'scope_id 必须是数字'}, 400)
    return _jr(caliber.query(code, year=year.strip() or None, scope_id=sid))


@router.get('/tables', summary='可选的表（业务字典）')
async def tables(actor: CurrentActor = Depends(get_current_actor)):
    from app.secretary import semantic
    return _jr({'tables': sorted(semantic.allowed_tables())})


@router.get('/columns', summary='某张表的列')
async def columns(table: str = '', actor: CurrentActor = Depends(get_current_actor)):
    from app.secretary import semantic
    cols = semantic.table_cols(semantic.table_name(table or '')) or {}
    return _jr({'table': table, 'columns': sorted(cols.keys())})
