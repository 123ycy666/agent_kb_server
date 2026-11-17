# -*- coding: utf-8 -*-
"""知识库目录 / 文档元数据 / 受控词表 接口。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.core.deps import CurrentActor, get_current_actor
from app.secretary import catalog as C

router = APIRouter(prefix='/catalog', tags=['目录与词表'])


@router.post('/seed', summary='灌默认目录与词表（幂等）')
def seed(actor: CurrentActor = Depends(get_current_actor)):
    return {'ok': True, **C.seed()}


@router.get('/tree', summary='目录树（带每目录文档数）')
def tree(actor: CurrentActor = Depends(get_current_actor)):
    return {'items': C.tree()}


@router.post('/node', summary='新增目录（一级或二级）')
async def add_node(request: Request, actor: CurrentActor = Depends(get_current_actor)):
    body = await request.json()
    res = C.add(body.get('name') or '', body.get('parent_id'), body.get('note') or '')
    return res


@router.post('/node/{cid}/enabled', summary='停用/启用目录')
async def node_enabled(cid: int, request: Request,
                       actor: CurrentActor = Depends(get_current_actor)):
    body = await request.json()
    return C.set_enabled(cid, bool(body.get('enabled')))


@router.get('/terms', summary='受控词表（维度：文种 / 主题）')
def terms(dimension: str = '', actor: CurrentActor = Depends(get_current_actor)):
    return {'items': C.terms(dimension)}


@router.post('/term', summary='新增词条')
async def add_term(request: Request, actor: CurrentActor = Depends(get_current_actor)):
    body = await request.json()
    return C.add_term(body.get('dimension') or '', body.get('term') or '',
                      body.get('synonyms') or '')


@router.post('/term/{tid}/enabled', summary='停用/启用词条')
async def term_enabled(tid: int, request: Request,
                       actor: CurrentActor = Depends(get_current_actor)):
    body = await request.json()
    return C.set_term_enabled(tid, bool(body.get('enabled')))


@router.get('/docs', summary='资料库列表（含目录/文种/时效/密级）')
def docs(kb_id: int = 1, limit: int = 200,
         actor: CurrentActor = Depends(get_current_actor)):
    return {'items': C.list_docs(kb_id, limit)}


@router.get('/doc/{doc_id}', summary='文档元数据')
def doc_meta(doc_id: int, actor: CurrentActor = Depends(get_current_actor)):
    return C.doc_meta(doc_id)


@router.post('/doc/{doc_id}', summary='保存文档元数据（目录/文种/单位/文号/日期/状态/密级）')
async def save_doc_meta(doc_id: int, request: Request,
                        actor: CurrentActor = Depends(get_current_actor)):
    body = await request.json()
    return C.save_doc_meta(doc_id, body or {})


@router.get('/completeness', summary='还有多少篇缺必填项')
def completeness(actor: CurrentActor = Depends(get_current_actor)):
    return C.completeness()
