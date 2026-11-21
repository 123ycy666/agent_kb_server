# -*- coding: utf-8 -*-
"""提示词页面接口（2026-11-01）。

    GET  /api/v1/prompts/tree           主流程节点 + 每节点挂的材料（八步）
    GET  /api/v1/prompts/material/{slot} 某一份的正文与分节
    PUT  /api/v1/prompts/material/{slot} 保存某一份（整份）
    POST /api/v1/prompts/material/{slot}/import  上传 markdown 覆盖某一份（按标题切）
    GET  /api/v1/prompts/preview        当前基础提示词（按层）
    GET/PUT /api/v1/prompts/complete    补全表（第 4 步用；2026-11-03 恢复）
    GET  /api/v1/prompts/lexicon/{key}  纠错词典（页面直接增删改查）

权限口径（2026-11-02 Fred 定）：**登录用户都能改，不设管理员闸**。
这 13 个接口一律只 `Depends(get_current_actor)`；前端 `/prompts` 页面对所有登录用户开放。
要收紧是另一件事，**别自己加 `is_admin` 判断** —— 加之前先问。
"""
from __future__ import annotations

from fastapi.encoders import jsonable_encoder

import re
import time

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from app.core.deps import CurrentActor, get_current_actor

router = APIRouter(prefix='/prompts', tags=['提示词'])

_HEAD = re.compile(r'^(#{1,4})\s+(.+?)\s*$', re.M)


def _jr(data, status_code: int = 200):
    # ★ 2026-11-03 修 500：词表行里带 `update_time`（datetime），`JSONResponse` 直接编码会抛
    #   TypeError → 整个 /prompts/lexicon/* 全 500，页面显示"共 0 条"（数据其实在）。
    #   jsonable_encoder 统一处理 datetime / Decimal，所有接口一起吃这一处。
    return JSONResponse(content=jsonable_encoder(data), status_code=status_code)


async def _json_body(request: Request):
    """取 JSON 请求体：坏 JSON / 不是对象都返回 (None, 错误文案)。

    2026-11-02 真库实测补的：`await request.json()` 直接抛会让几个写接口变成 500，
    前端只能看到"服务异常"。所有写接口统一走这里。
    """
    try:
        data = await request.json()
    except Exception:                              # noqa: BLE001
        return None, '请求体不是 JSON'
    if not isinstance(data, dict):
        return None, '请求体必须是 JSON 对象'
    return data, ''


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


@router.get('/tools', summary='问数线工具定义原文（模型实际收到的那份）')
def tools_def(actor: CurrentActor = Depends(get_current_actor)):
    from app.secretary import agent
    return _jr({'items': [{'name': t['function']['name'],
                           'description': t['function']['description'],
                           'parameters': t['function'].get('parameters') or {}}
                          for t in agent.TOOLS]})


@router.get('/impact', summary='问数线影响面：九段、13 类输入')
def impact(actor: CurrentActor = Depends(get_current_actor)):
    """答错时要能指到「哪一段的哪个输入」。只读，不改配置。"""
    from app.secretary import impact as I
    segs = I.segments()
    return _jr({'segments': segs,
                'counts': {'segments': len(segs),
                           'items': sum(len(s['items']) for s in segs)}})


@router.get('/impact/body', summary='问数线某一项输入的原文（全链路可读）')
def impact_body(key: str, actor: CurrentActor = Depends(get_current_actor)):
    """点开某一项看原文：返回这一项的存在位置、大小、可改与否、以及正文/表格。"""
    from app.secretary import impact as I
    res = I.body(key)
    if res.get('error'):
        return _jr(res, 404)
    return _jr(res)


@router.get('/impact/office', summary='干活线影响面：全新智能体链路（入口→指令→技能→工具→会话记忆→运行产物）')
def impact_office(actor: CurrentActor = Depends(get_current_actor)):
    """干活线一条真实链路用到哪些输入。只读，不改配置。"""
    from app.secretary import impact_agent as O
    segs = O.segments(actor.user_id)
    return _jr({'segments': segs,
                'counts': {'segments': len(segs),
                           'items': sum(len(s['items']) for s in segs)}})


@router.get('/impact/office/body', summary='干活线某一项输入的原文')
def impact_office_body(key: str, actor: CurrentActor = Depends(get_current_actor)):
    from app.secretary import impact_agent as O
    res = O.body(key, actor.user_id)
    if res.get('error'):
        return _jr(res, 404)
    return _jr(res)


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
    body, err = await _json_body(request)
    if err:
        return _jr({'error': err}, 400)
    sections = body.get('sections') or []
    if not isinstance(sections, list):
        return _jr({'error': 'sections 必须是数组'}, 400)
    rows = []
    for i, x in enumerate(sections):
        if not isinstance(x, dict):
            return _jr({'error': 'sections 里第 %d 项不是对象' % (i + 1)}, 400)
        rows.append({'id': x.get('id') or None,
                     'section': str(x.get('section') or '')[:160],
                     'body': str(x.get('body') or ''),
                     'sort_no': i,
                     'pinned': x.get('pinned', True),
                     'version_no': x.get('version_no') or 1})
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


# ★ 2026-11-03 定稿：补全表的 GET/PUT 在下面（`/complete`）恢复 —— 第 4 步要用。


@router.post('/material/{slot}/section', summary='新增一节（自己加判断类型）')
async def add_section(slot: str, request: Request,
                      actor: CurrentActor = Depends(get_current_actor)):
    from app.secretary import prompt_material
    if slot not in prompt_material.SLOTS:
        return _jr({'error': '未知 slot：%s' % slot}, 404)
    body, err = await _json_body(request)
    if err:
        return _jr({'error': err}, 400)
    return _jr(prompt_material.add_section(
        slot, body.get('section') or '', body.get('body') or '',
        bool(body.get('pinned', True)), user=actor.username))


@router.delete('/material/section/{sec_id}', summary='删一节（默认软删，可恢复）')
def delete_section(sec_id: int, hard: int = 0,
                   actor: CurrentActor = Depends(get_current_actor)):
    from app.secretary import prompt_material
    res = prompt_material.delete_section(sec_id, soft=not bool(hard))
    return _jr(res, 200 if res.get('ok') else 500)


@router.post('/material/section/{sec_id}/restore', summary='恢复软删的一节')
def restore_section(sec_id: int, actor: CurrentActor = Depends(get_current_actor)):
    from app.secretary import prompt_material
    res = prompt_material.restore_section(sec_id)
    return _jr(res, 200 if res.get('ok') else 500)


@router.get('/material/{slot}/deleted', summary='某一份里已软删的节')
def deleted_sections(slot: str, actor: CurrentActor = Depends(get_current_actor)):
    from app.secretary import prompt_material
    if slot not in prompt_material.SLOTS:
        return _jr({'error': '未知 slot：%s' % slot}, 404)
    rows = [r for r in prompt_material.sections(slot, include_disabled=True)
            if not r.get('enabled')]
    return _jr({'ok': True, 'slot': slot, 'rows': rows})


@router.get('/texts', summary='代码里的提示词（身份 / 汇报口吻 / 模式做法 / 词表）')
def list_texts(actor: CurrentActor = Depends(get_current_actor)):
    from app.secretary import prompt_text
    return _jr({'ok': True, 'items': prompt_text.all_texts()})


@router.post('/material/section/{sec_id}', summary='就地保存某一节正文（提示词页问数线用）')
async def save_material_section(sec_id: int, request: Request,
                                actor: CurrentActor = Depends(get_current_actor)):
    from app.secretary import prompt_material
    body, err = await _json_body(request)
    if err:
        return _jr({'error': err}, 400)
    res = prompt_material.save_section(int(sec_id), body.get('body') or '', user=actor.username)
    return _jr(res, 200 if res.get('ok') else 400)


@router.put('/texts/{key}', summary='保存一份代码提示词的覆盖值')
async def save_text(key: str, request: Request,
                    actor: CurrentActor = Depends(get_current_actor)):
    from app.secretary import prompt_text
    body, err = await _json_body(request)
    if err:
        return _jr({'error': err}, 400)
    res = prompt_text.save(key, body.get('body') or '', user=actor.username)
    return _jr(res, 200 if res.get('ok') else 500)


@router.delete('/texts/{key}', summary='恢复成代码默认值')
def reset_text(key: str, actor: CurrentActor = Depends(get_current_actor)):
    from app.secretary import prompt_text
    res = prompt_text.reset(key)
    return _jr(res, 200 if res.get('ok') else 500)


# ── 补全表（2026-11-03 恢复）────────────────────────────────────────
# 第 4 步拿「原问题 + 补后问题」来这张表里找命中条目，**只查不改**。
# Fred 在页面上迭代这张表（用户体验就靠它）：整表覆盖保存，失败回滚、一行不少。
@router.get('/complete', summary='补全表全部条目（含已软删的）')
def complete_list(actor: CurrentActor = Depends(get_current_actor)):
    from app.secretary import complete_rules
    return _jr({'ok': True, 'rows': [dict(r) for r in complete_rules.rows()],
                'deleted': [dict(r) for r in complete_rules.deleted()],
                'preview': complete_rules.as_text()})


@router.post('/complete/{rid}/restore', summary='恢复一条软删的补全条目')
def complete_restore(rid: int, actor: CurrentActor = Depends(get_current_actor)):
    from app.secretary import complete_rules
    res = complete_rules.restore(rid)
    return _jr(res, 200 if res.get('ok') else 500)


@router.put('/complete', summary='整表覆盖保存补全表')
async def complete_save(request: Request,
                        actor: CurrentActor = Depends(get_current_actor)):
    from app.secretary import complete_rules
    body, err = await _json_body(request)
    if err:
        return _jr({'error': err}, 400)
    items = body.get('rows')
    if items is None:
        items = body.get('items')
    res = complete_rules.save(items or [])
    return _jr(res, 200 if res.get('ok') else 500)


# ── 纠错词典（2026-11-02）───────────────────────────────────────────
# 四张 `t_nc_*` 表在本系统 PG 里，页面直接增删改查。
# `t_nc_catalog` / `t_nc_pinyin` 不开放：离线编译产物，改一个字要重算两万行。
@router.get('/lexicon', summary='纠错词典四张表的概览')
def lexicon_overview(actor: CurrentActor = Depends(get_current_actor)):
    from app.secretary import lex_admin
    return _jr({'ok': True, 'tables': lex_admin.overview()})


@router.get('/lexicon/status', summary='纠错词典体检：七类条数 + 缺哪一步会关掉什么能力')
def lexicon_status(actor: CurrentActor = Depends(get_current_actor)):
    from app.secretary import lex_import
    return _jr(lex_import.status())


@router.get('/lexicon/template/{key}', summary='下载导入模板（CSV，中文表头）')
def lexicon_template(key: str, actor: CurrentActor = Depends(get_current_actor)):
    from app.secretary import lex_import
    text = lex_import.template_csv(key)
    if text is None:
        return _jr({'error': '不支持的导入类型：%s' % key}, 404)
    return Response(content=text.encode('utf-8'), media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition': 'attachment; filename="%s_template.csv"' % key})


@router.get('/lexicon/export/{key}', summary='把当前库导出成同格式 CSV（搬到另一套实例用）')
def lexicon_export(key: str, actor: CurrentActor = Depends(get_current_actor)):
    from app.secretary import lex_import
    text = lex_import.export_csv(key)
    if text is None:
        return _jr({'error': '不支持的导入类型：%s' % key}, 404)
    return Response(content=text.encode('utf-8'), media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition': 'attachment; filename="%s.csv"' % key})


@router.post('/lexicon/import', summary='导入纠错词典（CSV / XLSX，拼音码服务端现算）')
def lexicon_import(file: UploadFile = File(...), key: str = Form('catalog'),
                   mode: str = Form('append'),
                   actor: CurrentActor = Depends(get_current_actor)):
    from app.core.config import settings
    from app.secretary import lex_import
    limit = settings.max_upload_mb * 1024 * 1024
    content = file.file.read(limit + 1)
    if len(content) > limit:
        return _jr({'error': '文件超过 %d MB' % settings.max_upload_mb}, 400)
    res = lex_import.import_file(key, file.filename, content, mode)
    return _jr(res, 200 if res.get('ok') else 400)


@router.post('/lexicon/reload', summary='立刻重载纠错引擎（不用等 60 秒）')
def lexicon_reload(actor: CurrentActor = Depends(get_current_actor)):
    from app.secretary import name_fix
    res = name_fix.reload_now()
    return _jr(res, 200 if res.get('ok') else 500)


@router.post('/lexicon/try', summary='试一句：看当前词典把这句话改成什么')
async def lexicon_try(request: Request, actor: CurrentActor = Depends(get_current_actor)):
    from app.secretary import name_fix
    body, err = await _json_body(request)
    if err:
        return _jr({'error': err}, 400)
    q = str(body.get('q') or '').strip()
    if not q:
        return _jr({'error': '先写一句话'}, 400)
    r = name_fix.fix(q)
    return _jr({'ok': True, 'original': r.get('original'), 'text': r.get('text'),
                'used': r.get('used'), 'word_fixes': r.get('word_fixes'),
                'name_fixes': r.get('name_fixes'),
                'unresolved': r.get('unresolved'),
                'error': r.get('error') or ''})


@router.get('/lexicon/{key}', summary='查一张词表（分页 + 模糊搜索）')
def lexicon_rows(key: str, q: str = '', limit: int = 50, offset: int = 0,
                 actor: CurrentActor = Depends(get_current_actor)):
    from app.secretary import lex_admin
    if key not in lex_admin.TABLES:
        return _jr({'error': '未知词表：%s' % key}, 404)
    res = lex_admin.rows(key, q=q, limit=limit, offset=offset)
    return _jr(res, 200 if res.get('ok') else 500)


@router.post('/lexicon/{key}', summary='新增一条')
async def lexicon_create(key: str, request: Request,
                         actor: CurrentActor = Depends(get_current_actor)):
    from app.secretary import lex_admin
    if key not in lex_admin.TABLES:
        return _jr({'error': '未知词表：%s' % key}, 404)
    body, err = await _json_body(request)
    if err:
        return _jr({'error': err}, 400)
    res = lex_admin.create(key, body)
    return _jr(res, 200 if res.get('ok') else 400)


@router.put('/lexicon/{key}/{rid}', summary='改一条')
async def lexicon_update(key: str, rid: int, request: Request,
                         actor: CurrentActor = Depends(get_current_actor)):
    from app.secretary import lex_admin
    if key not in lex_admin.TABLES:
        return _jr({'error': '未知词表：%s' % key}, 404)
    body, err = await _json_body(request)
    if err:
        return _jr({'error': err}, 400)
    res = lex_admin.update(key, rid, body)
    return _jr(res, 200 if res.get('ok') else 400)


@router.delete('/lexicon/{key}/{rid}', summary='删一条（软删，改完 60 秒内生效）')
def lexicon_delete(key: str, rid: int, actor: CurrentActor = Depends(get_current_actor)):
    from app.secretary import lex_admin
    if key not in lex_admin.TABLES:
        return _jr({'error': '未知词表：%s' % key}, 404)
    res = lex_admin.delete(key, rid)
    return _jr(res, 200 if res.get('ok') else 500)


# ── 数据表元数据覆盖层（2026-11-02）────────────────────────────────
# 业务库只读，改过的中文名/说明存在本系统 PG；读取时覆盖值优先。
@router.get('/meta', summary='数据表清单 + 字段说明（含已覆盖的标记）')
def meta_list(actor: CurrentActor = Depends(get_current_actor)):
    from app.secretary import semantic
    cat = semantic.catalog() or {}
    tables = []
    for t in (cat.get('tables') or []):
        name = t.get('table_name')
        cols = [{'column_name': c.get('column_name'),
                 'column_cn_name': c.get('column_cn_name'),
                 'business_desc': c.get('business_desc'),
                 'data_type': c.get('data_type')}
                for c in ((cat.get('cols') or {}).get(name) or {}).values()]
        tables.append({'table_name': name,
                       'table_cn_name': t.get('table_cn_name'),
                       'business_desc': t.get('business_desc'),
                       'level': t.get('level'),
                       'count': (cat.get('counts') or {}).get(name),
                       'columns': cols})
    return _jr({'ok': True, 'tables': tables,
                'overrides': cat.get('overrides') or []})


@router.put('/meta/override', summary='改一条元数据（清空 value = 恢复业务库原值）')
async def meta_save(request: Request, actor: CurrentActor = Depends(get_current_actor)):
    from app.secretary import meta_admin
    body, err = await _json_body(request)
    if err:
        return _jr({'error': err}, 400)
    res = meta_admin.save(kind=body.get('kind') or 'table',
                          table_name=body.get('table_name') or '',
                          field=body.get('field') or 'cn_name',
                          value=body.get('value') if body.get('value') is not None else '',
                          column_name=body.get('column_name') or '',
                          user=actor.username)
    return _jr(res, 200 if res.get('ok') else 400)
