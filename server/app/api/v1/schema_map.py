# -*- coding: utf-8 -*-
"""库结构接口：全库全景、单表字段、写回注释与页面入口。

页面「库结构」是定稿 H14.2「每张表必须有页面入口」的兜底检查口。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.core.deps import CurrentActor, get_current_actor
from app.secretary import schema_map as S

router = APIRouter(prefix='/schema', tags=['库结构'])


@router.get('/overview', summary='全库全景：四组×表×字段数×行数×页面入口')
def overview(actor: CurrentActor = Depends(get_current_actor)):
    items = S.overview()
    return {'items': items,
            'summary': {'tables': len(items),
                        'no_page': len([x for x in items if not x['page']]),
                        'undocumented': len([x for x in items if not x['documented']])}}


@router.get('/table/{full:path}', summary='单表字段与前 20 行')
def table(full: str, actor: CurrentActor = Depends(get_current_actor)):
    return S.table_detail(full)


@router.post('/comment', summary='字段含义写回 COMMENT ON COLUMN')
async def comment(request: Request, actor: CurrentActor = Depends(get_current_actor)):
    body = await request.json()
    return S.set_comment(body.get('table') or '', body.get('column') or '',
                         body.get('text') or '')


@router.post('/table-comment', summary='表说明写回 COMMENT ON TABLE')
async def table_comment(request: Request, actor: CurrentActor = Depends(get_current_actor)):
    body = await request.json()
    return S.set_table_comment(body.get('table') or '', body.get('text') or '')


@router.post('/page', summary='登记某张表的页面入口')
async def page(request: Request, actor: CurrentActor = Depends(get_current_actor)):
    body = await request.json()
    return S.set_page(body.get('table') or '', body.get('page') or '')
