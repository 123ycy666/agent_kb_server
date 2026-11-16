# -*- coding: utf-8 -*-
"""说明书接口（阶段 2）：上传即分类 → 预览 → 发布 → 试跑。

归属：全局（管理员设）/ 本人。隔离在查询时执行（目录过滤 + 取正文二次校验 + 检索范围）。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile

from app.core.deps import CurrentActor, get_current_actor
from app.secretary import spec as SP

router = APIRouter(prefix='/specs', tags=['说明书'])


def _jr(data, status_code: int = 200):
    from fastapi.responses import JSONResponse
    return JSONResponse(content=json.loads(json.dumps(data, ensure_ascii=False, default=str)),
                        status_code=status_code)


@router.get('/roles', summary='说明书角色（上传时必须选）')
def roles(actor: CurrentActor = Depends(get_current_actor)):
    SP.ensure()
    return _jr({'items': SP._q('select code, label, description, product, sort_no '
                               'from kb.spec_role where enabled order by sort_no')})


@router.get('', summary='可见说明书列表（目录行）')
def listing(space: str = 'spec', actor: CurrentActor = Depends(get_current_actor)):
    SP.ensure()
    uid = actor.user_id
    rows = SP.visible(uid, space)
    out = [{'id': r['id'], 'name': r['name'], 'role_code': r.get('role_code') or '',
            'space': r['space'], 'when_to_use': r.get('when_to_use') or '',
            'product': r.get('product') or '', 'owner': '本人' if r.get('owner_id') else '全局',
            'version_no': r.get('version_no'), 'chars': len(r.get('body') or ''),
            'checks': len(r.get('check_items') or []),
            'conflict_with': r.get('conflict_with') or [],
            'updated_at': str(r.get('updated_at') or '')} for r in rows]
    pend = SP._q("""select id, name, role_code, space, status, version_no, length(body) as chars,
                           when_to_use, product, owner_id
                      from kb.spec where status in ('pending','material')
                        and (owner_id is null or owner_id=%s) order by id desc limit 50""",
                 (uid,))
    return _jr({'items': out, 'pending': [dict(r, owner='本人' if r.get('owner_id') else '全局')
                                          for r in pend],
                'menu': SP.menu_text(uid), 'count': len(out)})


@router.post('/upload', summary='上传即分类：选角色=说明书（待发布）；不选=资料库')
async def upload(file: UploadFile = File(...), role_code: str = Form(''),
                 space: str = Form(''), when_to_use: str = Form(''), applies: str = Form(''),
                 product: str = Form(''), name: str = Form(''),
                 global_scope: int = Form(0),
                 actor: CurrentActor = Depends(get_current_actor)):
    data = await file.read()
    words = [w for w in (applies or '').replace('，', ',').replace('、', ',').split(',') if w.strip()]
    meta = {'name': name or '', 'when_to_use': when_to_use, 'applies': words,
            'product': product,
            'owner_id': None if (global_scope and actor.is_admin) else actor.user_id}
    res = SP.upload(actor.user_id, file.filename or 'spec.md', data, role_code=role_code,
                    space=space, meta=meta, user_code=actor.username)
    res['assumption'] = ('没选角色 → 落资料库，不进目录、可检索（V12）' if not role_code else
                         '进了说明书库，状态待发布；填完四项才能发布')
    return _jr(res)


@router.post('/{spec_id}/classify', summary='填四项：什么时候用 / 适用 / 产物 / 归属')
async def classify(spec_id: int, request: Request,
                   actor: CurrentActor = Depends(get_current_actor)):
    body = await request.json()
    res = SP.classify(actor.user_id, spec_id, role_code=body.get('role_code') or '',
                      when_to_use=body.get('when_to_use') or '',
                      applies=body.get('applies') or [],
                      product=body.get('product') or '',
                      owner_scope=body.get('owner_scope') or 'own',
                      check_items=body.get('check_items'),
                      outline=body.get('outline'), fmt=body.get('fmt'))
    return _jr(res)


@router.get('/{spec_id:int}', summary='详情：正文 + 版本 + 引用 + 冲突')
def detail(spec_id: int, actor: CurrentActor = Depends(get_current_actor)):
    hit = SP.load(actor.user_id, spec_id=spec_id)
    if not hit:
        return _jr({'error': '看不到这篇说明书（不存在或不是你/全局的）'}, 404)
    item = hit['item']
    vers = SP._q("""select version_no, length(body) as chars, saved_by, saved_at
                      from kb.spec_version where spec_id=%s order by version_no desc""",
                 (spec_id,))
    return _jr({'item': {k: item.get(k) for k in
                         ('id', 'name', 'role_code', 'space', 'status', 'when_to_use',
                          'applies', 'product', 'check_items', 'outline', 'fmt',
                          'version_no', 'storage_path', 'published_at', 'updated_by')},
                'owner': '本人' if item.get('owner_id') else '全局',
                'body': hit['body'], 'refs': hit['refs'], 'conflicts': hit['conflicts'],
                'versions': vers, 'chars': len(hit['body'])})


@router.get('/{spec_id}/preview', summary='待发布预览：切片 + 目录行长什么样')
def preview(spec_id: int, actor: CurrentActor = Depends(get_current_actor)):
    return _jr(SP.preview(spec_id))


@router.post('/{spec_id}/publish', summary='发布（60 秒内生效，不重启不发版）')
def publish(spec_id: int, actor: CurrentActor = Depends(get_current_actor)):
    res = SP.publish(spec_id, actor.user_id, admin=actor.is_admin)
    return _jr(res, 400 if res.get('error') else 200)


@router.post('/{spec_id}/retire', summary='退役（不删，可回滚）')
def retire(spec_id: int, actor: CurrentActor = Depends(get_current_actor)):
    return _jr(SP.retire(spec_id, actor.user_id, admin=actor.is_admin))


@router.post('/{spec_id}/enable', summary='把退役/停用的说明书重新启用（H9.1 可回滚）')
def enable(spec_id: int, actor: CurrentActor = Depends(get_current_actor)):
    res = SP.enable(spec_id, actor.user_id, admin=actor.is_admin)
    return _jr(res, 400 if res.get('error') else 200)


@router.get('/retired', summary='退役列表（可重新启用）')
def retired(actor: CurrentActor = Depends(get_current_actor)):
    SP.ensure()
    rows = SP._q("""select id, name, role_code, space, status, owner_id, version_no,
                           length(body) as chars, updated_at
                      from kb.spec where not enabled or status='retired'
                     order by updated_at desc limit 100""")
    return _jr({'items': [dict(r, owner='本人' if r.get('owner_id') else '全局') for r in rows]})


@router.post('/try-run', summary='试跑：这篇会不会被选中、目录行、注入多长')
async def try_run(request: Request, actor: CurrentActor = Depends(get_current_actor)):
    body = await request.json()
    res = SP.try_run(actor.user_id, name=body.get('name') or '',
                     question=body.get('question') or '', spec_id=body.get('spec_id'))
    return _jr(res)


@router.get('/{spec_id}/menu-line', summary='目录行长什么样')
def menu_line(spec_id: int, actor: CurrentActor = Depends(get_current_actor)):
    hit = SP.load(actor.user_id, spec_id=spec_id)
    if not hit:
        return _jr({'error': '看不到这篇说明书'}, 404)
    return _jr({'line': SP._line(hit['item'])})
