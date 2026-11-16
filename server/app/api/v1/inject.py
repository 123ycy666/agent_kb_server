# -*- coding: utf-8 -*-
"""提示词与注入接口（阶段 3/5）：注入文本独立成页 + 注入预览 + 三态注册表 + 入口判据。

V14 注入预览：能看到本轮注入文本与字数构成。
V15 无重复真身：注册表里每类配置只有一条在用，其它标"已退役"。
V16 配置可追溯：每项显版本、修改人、生效时间。
H10.2 原「提示词」页 = 问数链路的说明书，页头写明只影响问数线（本接口带 `affects` 字段）。
H14.1 每项标三态：可改 / 只读·库 / 只读·代码。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request

from app.core.deps import CurrentActor, get_current_actor
from app.secretary import inject, inject_store, report, route_rule
from app.secretary import spec as SP

router = APIRouter(prefix='/inject', tags=['提示词与注入'])


def _jr(data, status_code: int = 200):
    from fastapi.responses import JSONResponse
    return JSONResponse(content=json.loads(json.dumps(data, ensure_ascii=False, default=str)),
                        status_code=status_code)


@router.get('/items', summary='注入项：字数 / 来源 / 最近修改 / 生效方式')
def items(actor: CurrentActor = Depends(get_current_actor)):
    inject_store.ensure()
    return _jr({'items': inject_store.items(),
                'budget': inject_store.budget(),
                'affects': '干活线（办公/读件/闲聊）',
                'note': '问数链路的口径材料在「问数」页，不在这里。'})


@router.post('/text', summary='保存一项注入文本')
async def save_text(request: Request, actor: CurrentActor = Depends(get_current_actor)):
    body = await request.json()
    res = inject_store.save(body.get('k') or '', body.get('body') or '',
                            user=actor.username)
    return _jr(res, 400 if res.get('error') else 200)


@router.get('/budget', summary='常驻字数上限')
def get_budget(actor: CurrentActor = Depends(get_current_actor)):
    return _jr({'resident_chars': inject_store.budget()})


@router.post('/budget', summary='改常驻字数上限（默认 6000）')
async def set_budget(request: Request, actor: CurrentActor = Depends(get_current_actor)):
    body = await request.json()
    res = inject_store.set_budget(body.get('resident_chars') or 6000, user=actor.username)
    return _jr(res, 400 if res.get('error') else 200)


@router.get('/preview', summary='注入预览：本轮注入文本与字数构成（V14）')
def preview(question: str = '', session_id: str = '', spec_name: str = '',
            actor: CurrentActor = Depends(get_current_actor)):
    hit = {}
    if spec_name:
        hit = SP.load(actor.user_id, name=spec_name)
    elif question:
        hit = SP.load(actor.user_id, question=question)
    history = []
    summary = ''
    if session_id:
        try:
            from app.secretary import qa_log
            history = qa_log.load_context(session_id, limit=5) or []
        except Exception:                          # noqa: BLE001
            history = []
    out = inject.preview(actor.user_id, session_id or None, question,
                         spec=hit or None, history=history, summary=summary)
    out['spec'] = (hit.get('item') or {}).get('name') or ''
    out['question'] = question
    return _jr(out)


@router.get('/nodes', summary='三态注册表：每一项标 可改 / 只读·库 / 只读·代码')
def nodes(actor: CurrentActor = Depends(get_current_actor)):
    """每一张配置表 / 每一段代码常量都要有一行，并指到页面入口（H14.2）。"""
    inject_store.ensure()
    SP.ensure()
    route_rule.ensure()
    out = [
        {'group': '注入文本', 'key': 'identity', 'label': '身份', 'state': '可改',
         'storage': 'cfg.inject_text', 'page': '提示词与注入', 'api': '/api/inject/text',
         'affects': '两条线'},
        {'group': '注入文本', 'key': 'redlines', 'label': '红线（S0）', 'state': '可改',
         'storage': 'cfg.inject_text', 'page': '提示词与注入', 'api': '/api/inject/text',
         'affects': '两条线'},
        {'group': '注入文本', 'key': 'priority', 'label': '谁说了算（S1）', 'state': '可改',
         'storage': 'cfg.inject_text', 'page': '提示词与注入', 'api': '/api/inject/text',
         'affects': '两条线'},
        {'group': '注入文本', 'key': 'tools_guide', 'label': '工具说明', 'state': '可改',
         'storage': 'cfg.inject_text', 'page': '提示词与注入', 'api': '/api/inject/text',
         'affects': '两条线'},
        {'group': '注入文本', 'key': 'number_discipline', 'label': '数字纪律', 'state': '可改',
         'storage': 'cfg.inject_text', 'page': '提示词与注入', 'api': '/api/inject/text',
         'affects': '两条线'},
        {'group': '注入预算', 'key': 'resident_chars', 'label': '常驻上限', 'state': '可改',
         'storage': 'cfg.inject_budget', 'page': '提示词与注入', 'api': '/api/inject/budget',
         'affects': '两条线'},
        dict(route_rule.meta(), group='入口分流', label='入口判据', page='系统',
             api='/api/inject/route-rule', affects='两条线',
             values={k: v for k, v in route_rule.rules().items()}),
        {'group': '说明书', 'key': 'kb.spec', 'label': '说明书库', 'state': '可改',
         'storage': 'kb.spec', 'page': '知识库', 'api': '/api/specs', 'affects': '干活线'},
        {'group': '说明书', 'key': 'kb.spec_role', 'label': '说明书角色', 'state': '可改',
         'storage': 'kb.spec_role', 'page': '知识库', 'api': '/api/specs/roles',
         'affects': '干活线'},
        {'group': '问数链路', 'key': 'cfg.prompt_text', 'label': '预处理/作答规则（只影响问数线）',
         'state': '可改', 'storage': 'cfg.prompt_text', 'page': '问数',
         'api': '/api/prompts/texts', 'affects': '只影响问数线'},
        {'group': '问数链路', 'key': 'cfg.prompt_material', 'label': '口径材料（1.8 万字常驻，本次不改）',
         'state': '可改', 'storage': 'cfg.prompt_material', 'page': '问数',
         'api': '/api/prompts/material/{slot}', 'affects': '只影响问数线'},
        {'group': '口径', 'key': 'cfg.caliber_metric', 'label': '指标口径', 'state': '可改',
         'storage': 'cfg.caliber_metric / cfg.caliber_base', 'page': '问数',
         'api': '/api/caliber/list', 'affects': '两条线（共用事实）'},
        {'group': '口径', 'key': 'cfg.complete_rule', 'label': '补全表', 'state': '可改',
         'storage': 'cfg.complete_rule', 'page': '问数', 'api': '/api/prompts/complete-rules',
         'affects': '只影响问数线'},
        {'group': '词典', 'key': 'cfg.t_nc_*', 'label': '纠错词典 / 词表 / 拼音表', 'state': '可改',
         'storage': 'cfg.t_nc_catalog / t_nc_pinyin / t_nc_term / t_nc_typo / t_nc_wordlist',
         'page': '系统', 'api': '/api/prompts/lexicon', 'affects': '只影响问数线'},
        {'group': '字典覆盖', 'key': 'cfg.data_meta_override', 'label': '表名字段名覆盖层',
         'state': '可改', 'storage': 'cfg.data_meta_override', 'page': '系统',
         'api': '/api/prompts/meta', 'affects': '只影响问数线'},
        {'group': '业务字典', 'key': 'mysql.dlj_data.ai_*', 'label': '业务库字典（表/字段/指标/同义词）',
         'state': '只读·库', 'storage': 'MySQL dlj_data.ai_table_metadata 等',
         'page': '系统', 'api': '/api/prompts/meta', 'affects': '只影响问数线'},
        {'group': '代码常量', 'key': 'prompt_text.DEFAULT_TOOLS_GUIDE',
         'label': '工具说明默认段（库里空着才用它）', 'state': '只读·代码',
         'storage': 'server/app/secretary/prompt_text.py', 'page': '提示词与注入',
         'api': '', 'affects': '两条线'},
        {'group': '代码常量', 'key': 'loop.KIND_HINT', 'label': '产物参数形状（docx/xlsx/pptx）',
         'state': '只读·代码', 'storage': 'server/app/secretary/loop.py', 'page': '知识库',
         'api': '', 'affects': '干活线'},
        {'group': '代码常量', 'key': 'spec.FOUR', 'label': '上传必须填的四项',
         'state': '只读·代码', 'storage': 'server/app/secretary/spec.py', 'page': '知识库',
         'api': '', 'affects': '干活线'},
        {'group': '代码常量', 'key': 'workflow', 'label': '工作流 9 张表（ad_wf）',
         'state': '只读·库', 'storage': 'ad_wf 库', 'page': '任务与预警',
         'api': '/api/workflows', 'affects': '不动'},
    ]
    return _jr({'items': out,
                'summary': {'可改': len([x for x in out if x['state'] == '可改']),
                            '只读·库': len([x for x in out if x['state'] == '只读·库']),
                            '只读·代码': len([x for x in out if x['state'] == '只读·代码']),
                            'total': len(out)}})


@router.get('/route-rule', summary='入口分流判据（原代码常量，现页面可改）')
def get_route(actor: CurrentActor = Depends(get_current_actor)):
    return _jr(dict(route_rule.meta(), values=route_rule.rules()))


@router.post('/route-rule', summary='保存入口分流判据')
async def save_route(request: Request, actor: CurrentActor = Depends(get_current_actor)):
    body = await request.json()
    return _jr(route_rule.save(body, user=actor.username))


@router.get('/report', summary='四件套：某轮或最近若干轮（V14/E9）')
def get_report(qa_id: int = 0, limit: int = 50,
               actor: CurrentActor = Depends(get_current_actor)):
    if qa_id:
        return _jr(report.load(qa_id))
    return _jr({'items': report.recent(limit)})
