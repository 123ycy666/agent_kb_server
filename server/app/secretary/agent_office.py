# -*- coding: utf-8 -*-
"""办公线（2026-11-11）：办事题自己一条线，不经过查数的 2–6 步预处理。

一条线做的事：
    取做法（howto） → 计划（代码生成，记 trace） → 工具循环（可查库、可读文件）
    → 必须交出产物（docx / xlsx） → 自检 → 产物卡

三条分支（同一套循环，契约不同）：
    office  要有文件     read  要清单（不需要文件）   chat  直接答（0 工具）

和查数线的关系：**不共用编排**。证据包、交卷对账、写正文一概不用；
`run_sql` 是共用实现，但取数口径不注入这条线。
"""
from __future__ import annotations

import json
import time

OFFICE_TOOLS = ('kb_search', 'kb_read', 'read_file', 'run_sql', 'read_result',
                'describe_table', 'find_column', 'list_tables', 'list_metrics',
                'metric_query', 'make_file', 'run_code', 'finish')
READ_TOOLS = ('read_file', 'kb_read', 'kb_search', 'finish')

KIND_HINT = {
    'docx': 'docx 的 spec 形状：{"title": "标题", "sections": [["一、小节", "正文段落，用\\n分段"]], '
            '"sign_off": ["单位全称", "2026 年 11 月 11 日"], '
            '"tables": [{"header": ["列1"], "rows": [["值"]]}]}',
    'xlsx': 'xlsx 的 spec 形状：{"sheets": [{"name": "sheet名", "title": "表标题", '
            '"header": ["列1", "列2"], "rows": [[值, 值]], "formats": ["#,##0.00", "0.00%"], '
            '"widths": [14, 10], "total_row": true, "freeze": "A3"}]}',
}

CONTRACT = ('【交付契约】本轮必须交出文件，不能只写文字。'
            '内容想好后调 make_file(kind="%s", spec={...})；'
            '版式（字体/字号/行距/页边距/落款/页码）由系统按做法自动套，你不用写格式代码。'
            '出完文件后只回一句话说明做了什么，不要把正文再贴一遍。')

READ_CONTRACT = ('【交付契约】本轮交清单，不生成文件。每条按做法给的字段写：'
                 '结论 / 出处 / 谁做 / 时限；读完全文（read_file 的 next_offset 到 null）再写。')


def _identity() -> str:
    try:
        from app.secretary import prompts_data
        lines = [ln for ln in (prompts_data.IDENTITY or '').strip().splitlines()
                 if not ln.lstrip().startswith('#')]
        txt = '\n'.join(lines).strip()
        if txt:
            return txt
    except Exception:                               # noqa: BLE001
        pass
    return '你是随州供电公司的办公智能助手，负责把活做成能直接用的东西。'


def _system(block: str, product: str, lane: str, has_files: bool) -> str:
    parts = [_identity(), '']
    if lane == 'chat':
        parts += ['【这条线是闲聊】直接用一两句话回答，不查库、不调工具。']
        return '\n'.join(parts)
    if lane == 'read':
        parts += ['【这条线是读件】把文件里跟用户有关的东西挑出来，一条一结论，带出处。',
                  '只依据读到的文件内容；文件里没有的写「文件中未提及」，不许用常识或业务库补。']
    else:
        parts += ['【这条线是办事】用户要的是一份能拿走的东西，不是一段说明。',
                  '做法比你的习惯优先：做法里定的结构、字体、篇幅、口吻，一律照做。',
                  '数字只用工具查回来的原始结果；查不到就写「需另行核实」，不许自己补一个。',
                  '用户说「刚才问的」「上面这些」「这批」时，指【本会话前几轮】里的内容——'
                  '把那里已有的数字和明细照搬进文件，**不要**答「会话里没有上一轮数据」，'
                  '也不要因为没重查就说「需另行核实」。',
                  '不要在正文里复述思考过程，不要写「好的，我来帮你」。']
    if block:
        parts += ['', block]
    if lane == 'office':
        hint = KIND_HINT.get(product)
        if hint:
            parts += ['', '【产物参数】' + hint]
        parts += ['', CONTRACT % product]
    if lane == 'read':
        parts += ['', READ_CONTRACT]
    if has_files:
        parts += ['', '【本轮带来了文件】正文用 read_file(file_id) 分页读到 next_offset 为 null 为止。']
    return '\n'.join(parts)


def ask_office(question, routed, *, lane='office', model, session_id=None, user_id=None,
               user_code=None, client_ip=None, channel=None, doc_files=None,
               doc_context=None, trace=None, should_stop=None, max_steps=None,
               today=None):
    from app.secretary import agent as A
    from app.secretary import howto as HT
    from app.secretary import tools_file, tools_result

    trace = trace if trace is not None else []
    t_all = time.time()
    product = (routed or {}).get('product') or 'docx'
    if lane == 'read':
        product = 'checklist'
    elif lane == 'chat':
        product = 'text'
    elif product not in ('docx', 'xlsx'):
        product = 'docx'
    max_steps = int(max_steps or (8 if lane == 'read' else 12))

    hp = {'item': {}, 'block': ''}
    if lane != 'chat':
        hp = HT.for_prompt((routed or {}).get('howto') or '', product, question)
    item = hp.get('item') or {}
    if lane != 'chat':
        trace.append({'seq': len(trace) + 1, 'kind': 'plan', 'tool': 'plan',
                      'result': {'做法': item.get('task') or '（没匹配到做法，按通用版式）',
                                 '产物': product,
                                 '步骤': ['取做法', '查数/读文件', '按做法成文',
                                          '生成文件', '自检', '交付'] if lane == 'office'
                                 else ['取做法', '读全文', '按字段出清单']}})
    tools_file.set_ctx(user_id=user_id,
                       out_dir='data/agent_out/%s' % (session_id or 'anon'),
                       howto=item.get('task') or '')

    user_content = '【本轮要办的事】\n' + str(question or '')
    user_content += '\n\n【今天】%s' % (today or time.strftime('%Y-%m-%d'))
    # ★ 2026-11-12：本会话前几轮 ——「把刚才问的这些做成 Excel」全靠它。
    #   不注入的话办公线看不见上一轮查回来的数，只能答「会话里没有上一轮数据」。
    history = []
    if session_id:
        try:
            from app.secretary import qa_log as _qa
            history = _qa.load_context(session_id, limit=5) or []
        except Exception:                           # noqa: BLE001 - 读不到历史按无历史
            history = []
    if history:
        lines = []
        for h in history[-5:]:
            lines.append('上一轮问：%s\n上一轮答：%s' % (
                str(h.get('raw_question') or '')[:200],
                str(h.get('answer') or '')[:1500]))
        user_content += ('\n\n【本会话前几轮】（用户说「刚才」「上面这些」「这批」时指这里；'
                         '里面已有的数字和明细可以直接拿来用，不必重查；'
                         '若本轮工具查回了新数，以本轮结果为准）\n' + '\n\n'.join(lines))
    files = list(doc_files or [])
    if files:
        lines = ['【文件%d】《%s》 约 %s 字，file_id=%s'
                 % (i, f.get('name'), f.get('chars'), f.get('file_id'))
                 for i, f in enumerate(files, 1)]
        user_content += '\n\n【本轮带来的文件】\n' + '\n'.join(lines)
    elif doc_context:
        user_content += '\n\n【用户上传的文件】\n' + str(doc_context)[:20000]

    system = _system(hp.get('block') or '', product, lane, bool(files))
    messages = [{'role': 'system', 'content': system},
                {'role': 'user', 'content': user_content}]

    names = READ_TOOLS if lane == 'read' else OFFICE_TOOLS
    tools = [] if lane == 'chat' else [t for t in A.TOOLS if t['function']['name'] in names]
    answer, artifacts = '', []
    nudges = 0
    sql_calls = 0
    seen = set()
    stuck = 0
    budget_s = float((routed or {}).get('budget_s') or 100)

    def _nudge(why: str) -> None:
        messages.append({'role': 'user', 'content':
                         '【收尾】%s 数据已经够用，不要再查。立刻调 make_file(kind="%s", spec={...}) '
                         '把文件生成出来，缺的数字在正文里写「需另行核实」。' % (why, product)})

    for step in range(1, max_steps + 1):
        if should_stop is not None and should_stop():
            answer = '（已停止生成）'
            break
        over_time = budget_s > 0 and (time.time() - t_all) > budget_s
        resp = A._chat(messages, model, think=False, use_tools=bool(tools),
                       tools=tools or None)
        msg = resp['choices'][0]['message']
        calls = msg.get('tool_calls') or []
        if over_time and lane == 'office' and calls and nudges < 2:
            nudges += 1
            messages.append({'role': 'assistant', 'content': msg.get('content') or '',
                             'tool_calls': calls})
            for tc in calls:
                messages.append({'role': 'tool', 'tool_call_id': tc.get('id'),
                                 'content': '{"skipped":true,"reason":"时间预算已到，本轮不再查"}'})
            _nudge('时间预算（%ds）到了。' % budget_s)
            trace.append({'seq': len(trace) + 1, 'kind': 'budget', 'tool': 'time',
                          'content': '到时间预算，催出文件'})
            continue
        if not calls:
            answer = (msg.get('content') or '').strip()
            if lane != 'office':
                break
            if any(t.get('tool') == 'make_file' and not (t.get('result') or {}).get('error')
                   for t in trace):
                break
            if nudges >= 2:
                answer = (answer + '\n\n（文件没生成成功，上面是文书内容，可直接复制使用。）').strip()
                break
            nudges += 1
            messages.append({'role': 'assistant', 'content': answer or '（未产出文件）'})
            messages.append({'role': 'user', 'content':
                             '【还差交付物】文件还没生成。请立刻调 make_file(kind="%s", spec={...})，'
                             '内容用你已经查到或已知的素材；不要只写文字。' % product})
            trace.append({'seq': len(trace) + 1, 'kind': 'nudge', 'tool': 'contract',
                          'content': '未产出交付物，催一轮（第 %d 次）' % nudges})
            continue
        messages.append({'role': 'assistant', 'content': msg.get('content') or '',
                         'tool_calls': calls})
        live = [c for c in calls if ((c.get('function') or {}).get('name')) != 'finish']
        outs = A._exec_calls(live) if live else []
        by_id = {tc.get('id'): o for tc, o in zip(live, outs)}
        for tc in calls:
            name = (tc.get('function') or {}).get('name')
            if name == 'finish':
                messages.append({'role': 'tool', 'tool_call_id': tc.get('id'),
                                 'content': '{"ok":true}'})
                continue
            o = by_id.get(tc.get('id'))
            if o is None:
                continue
            trace.append({'seq': len(trace) + 1, 'kind': 'tool', 'tool': o['name'],
                          'args': o['args'], 'ms': o['ms'], 'round': step,
                          'result': o['result']})
            messages.append({'role': 'tool', 'tool_call_id': tc['id'],
                             'content': tools_result.deliver(o['name'], o['result'],
                                                             session_id=session_id)})
        sql_calls += len([o for o in outs if o.get('name') == 'run_sql'])
        if lane == 'office' and nudges < 2:
            if sql_calls >= 10:
                nudges += 1
                _nudge('这一轮已经查了 %d 次库。' % sql_calls)
                trace.append({'seq': len(trace) + 1, 'kind': 'budget', 'tool': 'sql_cap',
                              'content': '查库 %d 次，催出文件' % sql_calls})
                continue
            sigs = {json.dumps(o.get('result'), ensure_ascii=False, sort_keys=True, default=str)[:800]
                    for o in outs
                    if not (isinstance(o.get('result'), dict) and o['result'].get('error'))}
            if sigs:
                stuck = stuck + 1 if not (sigs - seen) else 0
                seen |= sigs
            if stuck >= 2:
                nudges += 1
                _nudge('连续两轮查回来的是同一批数据。')
                trace.append({'seq': len(trace) + 1, 'kind': 'budget', 'tool': 'stuck',
                              'content': '原地打转，催出文件'})
                continue
        if any(((c.get('function') or {}).get('name')) == 'finish' for c in calls):
            if lane == 'office' and not any(
                    t.get('tool') == 'make_file' and not (t.get('result') or {}).get('error')
                    for t in trace) and nudges < 2:
                nudges += 1
                messages.append({'role': 'user', 'content':
                                 '【还差交付物】文件还没生成，请调 make_file(kind="%s")。' % product})
                continue
            break

    for t in trace:
        r = t.get('result') or {}
        if t.get('kind') == 'tool' and t.get('tool') == 'make_file' and r.get('url'):
            artifacts.append({'kind': r.get('kind') or product, 'name': r.get('name'),
                              'bytes': r.get('bytes'), 'file_id': r.get('file_id'),
                              'url': r.get('url'),
                              'self_check': (r.get('self_check') or {}).get('verdict') or '未自检'})
        for o in (r.get('outputs') or []) if isinstance(r, dict) else []:
            if o.get('url'):
                artifacts.append({'kind': str(o.get('name') or '').rsplit('.', 1)[-1].lower(),
                                  'name': o.get('name'), 'file_id': o.get('file_id'),
                                  'url': o.get('url'), 'self_check': '未自检'})

    result = {
        'input_question': question,
        'answer': answer or (('文件已生成：' + artifacts[0]['name']) if artifacts
                             else '文件没生成成功：本轮查到/写出的内容没有落成文件，'
                                  '请换个说法再试，或把要的内容直接贴给我。'),
        'lane': lane,
        'product': product,
        'howto': item.get('task') or '',
        'plan': (trace[1].get('result') if len(trace) > 1 else {}),
        'artifacts': artifacts,
        'trace': trace,
        'steps': len(trace),
        'model': model,
        'db_query_count': len([t for t in trace if t.get('tool') == 'run_sql']),
        'kb_query_count': len([t for t in trace if t.get('tool') == 'kb_search']),
        'no_db_query': not any(t.get('tool') == 'run_sql' for t in trace),
        'qtype': '办公' if lane == 'office' else ('读件' if lane == 'read' else '闲聊'),
        'today': today or time.strftime('%Y-%m-%d'),
        'files': files,
        'elapsed_ms': int((time.time() - t_all) * 1000),
        'session_id': session_id,
        'qa_id': None,
    }
    try:
        from app.secretary import qa_log
        rec = qa_log.save(result, session_id=session_id, user_id=user_id,
                          user_code=user_code, client_ip=client_ip, channel=channel)
        result['session_id'] = rec.get('session_id') or session_id
        result['qa_id'] = rec.get('qa_id')
    except Exception as exc:                        # noqa: BLE001 - 落库失败不影响交付
        print('[office] 落库失败（不影响交付）：%s: %s' % (type(exc).__name__, exc), flush=True)
    return result
