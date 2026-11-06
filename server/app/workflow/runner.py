"""执行器：读文档 → 给工具 → 模型自己跑 → 落库。"""
from __future__ import annotations

import json
import time

from sqlalchemy.orm import Session

from app.workflow import docs as docsdb
from app.workflow import store, tools
from app.workflow.models import RuleDoc, Run


def _system(doc: RuleDoc, today: str) -> str:
    tpl = docsdb.TEMPLATES.get(doc.work_type) or docsdb.TEMPLATES['detect']
    lines = [
        '你是这个系统的「%s」执行器。你只按下面这份规则文档干活，文档没写的不要自行发明。' % tpl['label'],
        '今天的日期：%s' % today,
        '',
        '# 规则文档《%s》（第 %d 版）' % (doc.name, int(doc.version or 1)),
    ]
    for key, title, _hint, _ex in docsdb.sections_of(doc.work_type):
        body = str((doc.doc or {}).get(key) or '').strip()
        lines.append('')
        lines.append('## ' + title)
        lines.append(body or '（这一节空着：不做这件事）')
    lines += [
        '',
        '# 操作纪律',
        '- 只能用给你的工具取证；数字一律来自工具返回，不许自己估。',
        '- 取数前先看表结构和字段，别猜字段名。',
    ]
    if doc.work_type == 'detect':
        lines += [
            '- 每个被检查的对象都要调一次 write_result，命中与不命中都写；不命中就 hit=false。',
            '- detail 与 evidence 必填：detail 放给人看的几个数，evidence 放"表.字段 + 口径 + 数据截止日"。',
            '- 一条对象只写一次；对象键在同一份文档里必须稳定。',
            '- 全部写完后调 finish，checked_n 填你实际检查了几个对象。',
        ]
    else:
        lines += [
            '- 先用 read_items 读事项，再按文档第三节的规则定收件人（用 run_sql 查账号，别猜）。',
            '- 每发一条都要调 send，并写清 reason（为什么发给他）；查不到人就写清理由不发。',
            '- 发完调 finish，sent_n 填实际发出几条。',
        ]
    return '\n'.join(lines)


def run_doc(db: Session, doc_id: int, trigger: str = 'manual', dry: bool = False,
            today: str | None = None) -> dict:
    doc = docsdb.get_doc(db, int(doc_id))
    if doc is None:
        raise ValueError('文档不存在')
    run = store.start_run(db, doc, trigger=trigger, dry=dry)
    store.log(db, run, 'read_doc', {'doc_version': int(doc.version or 1),
                                    'sections': {k: len(str(v or ''))
                                                 for k, v in (doc.doc or {}).items()}})
    ctx = {'db': db, 'run': run, 'doc': doc, 'dry': dry, 'keys': set(),
           'counts': {}, 'finished': False, 'data_as_of': today or store.today()}
    t0 = time.time()
    try:
        from app.secretary import agent as agent_mod
        from app.secretary import config as sec_config
        model = getattr(sec_config, 'MODEL', '') or 'deepseek-chat'
        max_steps = max(1, store.param_int(db, 'max_steps', 24))
        messages = [{'role': 'system', 'content': _system(doc, store.today())},
                    {'role': 'user', 'content': '开始执行这一轮。'}]
        toolset = tools.tools_for(doc.work_type)
        step = 0
        for step in range(1, max_steps + 1):
            resp = agent_mod._chat(messages, model, use_tools=True, tools=toolset,
                                   think=False, max_tokens=2000)
            msg = (resp.get('choices') or [{}])[0].get('message') or {}
            calls = msg.get('tool_calls') or []
            if not calls:
                break
            messages.append({'role': 'assistant', 'content': msg.get('content') or '',
                             'tool_calls': calls})
            for c in calls:
                fn = (c.get('function') or {})
                name = fn.get('name') or ''
                try:
                    args = json.loads(fn.get('arguments') or '{}')
                except (TypeError, ValueError):
                    args = {}
                out = tools.call(ctx, name, args)
                store.log(db, run, 'tool', {
                    'tool': name, 'args': tools.dumps(args)[:1500],
                    'ok': out.get('ok', True), 'out': tools.dumps(out)[:1500]})
                messages.append({'role': 'tool', 'tool_call_id': c.get('id') or '',
                                 'content': tools.dumps(out)[:6000]})
            if ctx.get('finished'):
                break

        counts = ctx.get('counts') or {}
        if doc.work_type == 'detect':
            if not ctx['keys'] and not dry:
                store.finish_run(db, run, status='failed',
                                 error='一个对象都没写（write_result 从未被调用）',
                                 checked_n=counts.get('checked_n', 0))
                return {'run_id': run.id, 'status': 'failed',
                        'error': '一个对象都没写'}
            # 只有"每个检查到的对象都写了结果"才允许关项，否则会把漏写的对象误判成数据恢复
            expected = int(counts.get('checked_n') or 0) or len(ctx['keys'])
            closed = 0
            if dry:
                closed = 0
            elif len(ctx['keys']) < expected:
                store.log(db, run, 'error', {
                    'error': '结果不完整：写了 %d 个对象，模型自报检查了 %d 个；本轮不关项'
                             % (len(ctx['keys']), expected)})
            else:
                closed = store.close_missing(db, run, doc, ctx['keys'])
        else:
            if not ctx.get('reads'):
                store.finish_run(db, run, status='failed',
                                 error='没有读事项（read_items 从未被调用）')
                return {'run_id': run.id, 'status': 'failed', 'error': '没有读事项'}
            closed = 0
        if not dry:
            store.cleanup(db)
        store.finish_run(db, run, status='ok', error=None,
                         checked_n=counts.get('checked_n', 0),
                         hit_n=counts.get('hit_n', 0), new_n=counts.get('new_n', 0),
                         closed_n=closed, sent_n=counts.get('sent_n', 0),
                         data_as_of=ctx.get('data_as_of'))
        return {'run_id': run.id, 'status': 'ok', 'steps': step,
                'elapsed_s': round(time.time() - t0, 1), **counts, 'closed_n': closed,
                'dry': dry}
    except Exception as exc:              # noqa: BLE001
        store.finish_run(db, run, status='failed', error='%s: %s' % (type(exc).__name__, str(exc)[:500]))
        raise
