# -*- coding: utf-8 -*-
"""提示词页面接口（2026-11-01）。

六个动作：
    GET  /api/v1/prompts/tree           主流程节点 + 每节点挂的材料
    GET  /api/v1/prompts/material/{slot} 某一份的正文与分节
    PUT  /api/v1/prompts/material/{slot} 保存某一份（整份）
    POST /api/v1/prompts/material/{slot}/import  上传 markdown 覆盖某一份（按标题切）
    GET  /api/v1/prompts/preview        当前基础提示词（按层）
    GET  /api/v1/prompts/complete       补全表
    PUT  /api/v1/prompts/complete       保存补全表

写操作只要求登录。线上只有管理员在用；要收紧再按 `actor.is_admin` 加闸（记在计划 Progress）。
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.core.deps import CurrentActor, get_current_actor

router = APIRouter(prefix='/prompts', tags=['提示词'])

_HEAD = re.compile(r'^(#{1,4})\s+(.+?)\s*$', re.M)


def _jr(data, status_code: int = 200):
    return JSONResponse(content=data, status_code=status_code)


def _split_md(text: str) -> list[dict]:
    """按 ## / ### 标题切成小节；切不出来就整篇一节。"""
    text = (text or '').strip()
    if not text:
        return []
    marks = [(m.start(), m.end(), m.group(2).strip())
             for m in _HEAD.finditer(text) if len(m.group(1)) >= 2]
    if not marks:
        return [{'section': '全文', 'body': text}]
    out = []
    for i, (start, end, title) in enumerate(marks):
        nxt = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        body = text[end:nxt].strip()
        if body:
            out.append({'section': title[:160], 'body': body})
    head = text[:marks[0][0]].strip()
    if head and len(head) > 30:
        out.insert(0, {'section': '说明', 'body': head})
    return out


@router.get('/overview', summary='一次拿全：节点树 + 四份材料 + 补全表 + 代码提示词 + 注入字数')
def overview(actor: CurrentActor = Depends(get_current_actor)):
    """内网对接用：一个请求拿到页面需要的全部数据，不用连调六个接口。"""
    from app.secretary import prompt_tree, prompt_material, complete_rules, prompt_text
    return _jr({
        'ok': True,
        'nodes': prompt_tree.tree(),
        'materials': prompt_material.summary(),
        'complete': complete_rules.rows(),
        'texts': prompt_text.all_texts(),
        'inject': {
            'loop_block': len(prompt_material.block(('fetch', 'judge'))),
            'final_block': len(prompt_material.block(('answer', 'check'))),
        },
        'actor': {'username': actor.username, 'is_admin': actor.is_admin},
    })


@router.get('/tree', summary='主流程节点与材料')
def tree(actor: CurrentActor = Depends(get_current_actor)):
    from app.secretary import prompt_tree
    return _jr({'ok': True, 'nodes': prompt_tree.tree(),
                'actor': {'username': actor.username, 'is_admin': actor.is_admin}})


def _hint(body: str, n: int = 60) -> str:
    """一句话摘要：这一节讲什么（给页面显示「用在哪」）。"""
    s = re.sub(r'[|*`>#\-]{1,}', ' ', str(body or ''))
    s = re.sub(r'\s+', ' ', s).strip()
    return (s[:n] + '…') if len(s) > n else s


@router.get('/material/{slot}', summary='某一份材料的正文与分节')
def material(slot: str, actor: CurrentActor = Depends(get_current_actor)):
    from app.secretary import prompt_material
    if slot not in prompt_material.SLOTS:
        return _jr({'error': '未知 slot：%s' % slot}, 404)
    rows = prompt_material.sections(slot)
    for r in rows:
        r['hint'] = _hint(r.get('body'))
    return _jr({'ok': True, 'slot': slot, 'name': prompt_material.SLOT_NAMES.get(slot, slot),
                'sections': rows,
                'chars': sum(len(str(r.get('body') or '')) for r in rows)})


@router.put('/material/{slot}', summary='保存某一份材料')
async def save_material(slot: str, request: Request,
                        actor: CurrentActor = Depends(get_current_actor)):
    from app.secretary import prompt_material
    if slot not in prompt_material.SLOTS:
        return _jr({'error': '未知 slot：%s' % slot}, 404)
    body = await request.json()
    sections = body.get('sections') or []
    if not isinstance(sections, list):
        return _jr({'error': 'sections 必须是数组'}, 400)
    rows = [{'section': str(x.get('section') or '')[:160],
             'body': str(x.get('body') or ''),
             'sort_no': i,
             'version_no': x.get('version_no') or 1}
            for i, x in enumerate(sections)]
    res = prompt_material.replace_slot(slot, rows, user=actor.username)
    return _jr(res, 200 if res.get('ok') else 500)


@router.post('/material/{slot}/import', summary='上传 markdown 覆盖某一份')
async def import_material(slot: str, request: Request,
                          actor: CurrentActor = Depends(get_current_actor)):
    from app.secretary import prompt_material
    if slot not in prompt_material.SLOTS:
        return _jr({'error': '未知 slot：%s' % slot}, 404)
    try:
        body = await request.json()
    except Exception:                              # noqa: BLE001
        return _jr({'error': '请求体不是 JSON'}, 400)
    secs = _split_md(str(body.get('text') or ''))
    if not secs:
        return _jr({'error': '没切出任何小节（检查有没有 ## 标题）'}, 400)
    rows = [{'section': s['section'], 'body': s['body'], 'sort_no': i}
            for i, s in enumerate(secs)]
    res = prompt_material.replace_slot(slot, rows, user=actor.username)
    if res.get('ok'):
        res['sections'] = len(secs)
    return _jr(res, 200 if res.get('ok') else 500)


@router.get('/preview', summary='当前基础提示词（按层）')
def preview(mode: str = 'data', actor: CurrentActor = Depends(get_current_actor)):
    """展示用：基础提示词五层 + 四份材料的字数。

    ⚠️ 不调 agent_v2（那要一整套运行时参数）；这里直接用 prompt_stack.build
    拿同一份常量，保证页面看到的就是代码里的原文。
    """
    from app.secretary import prompt_stack, prompts_data, agent, prompt_material
    ans = (agent.COMMON + '\n\n' + agent.CHECK + '\n\n' + prompts_data.ANSWER_DATA).strip()
    r = prompt_stack.build(mode=mode, identity=prompts_data.IDENTITY,
                           modes=prompts_data.MODE_TEXTS, priority=agent.RULES_PRIORITY,
                           answer_format=ans)
    layers = []
    for x in r.get('layers') or []:
        body = ''
        for part in r.get('system', '').split('===== '):
            if part.startswith(x.get('title')):
                body = part[len(x.get('title')):].lstrip(' =====').strip()
                break
        layers.append({'id': x.get('id'), 'title': x.get('title'),
                       'chars': x.get('chars'), 'source': x.get('source'), 'body': body})
    return _jr({'ok': True, 'mode': mode, 'total': len(r.get('system') or ''),
                'layers': layers, 'materials': prompt_material.summary()})


@router.get('/complete', summary='补全表')
def list_complete(actor: CurrentActor = Depends(get_current_actor)):
    from app.secretary import complete_rules
    return _jr({'ok': True, 'rows': complete_rules.rows(),
                'text': complete_rules.as_text()})


@router.put('/complete', summary='保存补全表')
async def save_complete(request: Request, actor: CurrentActor = Depends(get_current_actor)):
    from app.secretary import complete_rules
    body = await request.json()
    res = complete_rules.save(body.get('rows') or [])
    return _jr(res, 200 if res.get('ok') else 500)


@router.post('/material/{slot}/section', summary='新增一节（自己加判断类型）')
async def add_section(slot: str, request: Request,
                      actor: CurrentActor = Depends(get_current_actor)):
    from app.secretary import prompt_material
    if slot not in prompt_material.SLOTS:
        return _jr({'error': '未知 slot：%s' % slot}, 404)
    body = await request.json()
    return _jr(prompt_material.add_section(
        slot, body.get('section') or '', body.get('body') or '',
        bool(body.get('pinned', True)), user=actor.username))


@router.delete('/material/section/{sec_id}', summary='删一节')
def delete_section(sec_id: int, actor: CurrentActor = Depends(get_current_actor)):
    from app.secretary import prompt_material
    res = prompt_material.delete_section(sec_id)
    return _jr(res, 200 if res.get('ok') else 500)


@router.get('/texts', summary='代码里的提示词（身份 / 汇报口吻 / 模式做法 / 词表）')
def list_texts(actor: CurrentActor = Depends(get_current_actor)):
    from app.secretary import prompt_text
    return _jr({'ok': True, 'items': prompt_text.all_texts()})


@router.put('/texts/{key}', summary='保存一份代码提示词的覆盖值')
async def save_text(key: str, request: Request,
                    actor: CurrentActor = Depends(get_current_actor)):
    from app.secretary import prompt_text
    body = await request.json()
    res = prompt_text.save(key, body.get('body') or '', user=actor.username)
    return _jr(res, 200 if res.get('ok') else 500)


@router.delete('/texts/{key}', summary='恢复成代码默认值')
def reset_text(key: str, actor: CurrentActor = Depends(get_current_actor)):
    from app.secretary import prompt_text
    res = prompt_text.reset(key)
    return _jr(res, 200 if res.get('ok') else 500)
