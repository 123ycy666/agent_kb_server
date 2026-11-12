# -*- coding: utf-8 -*-
"""办公线（2026-11-11）：办事题自己一条线，不经过查数的 2–6 步预处理。

一条线做的事：
    取做法（howto） → 计划（记 trace） → 循环（可查库、可读文件、可读本会话前几轮）
    → 交出产物（docx / xlsx） → 自检 → 产物卡

三条分支（同一套循环，契约不同）：
    office  要有文件     read  要清单     chat  直接答（0 工具）

2026-11-12 按 Fred 的要求改：**不加安全网**。
原先那套「时间预算 / 查库次数上限 / 原地打转检测 / 缺产物催办」全删了——
那些是代码替模型管节奏，模型不行就该暴露出来，再一个个解；靠催办遮住只是把问题推迟。
现在只保留两件框架必需：步数上限（防死循环）和模型不再发工具时结束。
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

CONTRACT = ('【交付契约】本轮要交出文件：内容想好后调 make_file(kind="%s", spec={...})。'
            '版式（字体/字号/行距/页边距/落款/页码）由系统按做法自动套，你不用写格式代码。'
            '出完文件后一句话说明做了什么，不要把正文再贴一遍。')

READ_CONTRACT = '【交付契约】本轮交清单：每条按做法给的字段写（结论 / 出处 / 谁做 / 时限）。'


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
                  '只依据读到的文件内容；文件里没有的写「文件中未提及」。']
        if not has_files:
            parts += ['【这一轮没有文件】清单里一个文件都没有，不要调 read_file、'
                      '也不要自己编 file_id —— 直接说清这次没收到文件，请用户把文件发过来，'
                      '或把正文贴进来。']
    else:
        parts += ['【这条线是办事】用户要的是一份能拿走的东西。',
                  '做法比习惯优先：做法里定的结构、字体、篇幅、口吻，一律照做。',
                  '数字只用查回来的原始结果；查不到就写「需另行核实」。',
                  '用户说「刚才问的」「上面这些」「这批」时，指【本会话前几轮】里的内容。']
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
        parts += ['', '【可用文件】清单里既有本轮带来的，也有本会话此前带来的（标「此前」）。'
                      '用户说「这个文章」「这份材料」「刚才那个」时，指清单里的文件——'
                      '用 read_file(file_id) 把正文读出来（分页读到 next_offset 为 null）；'
                      '只有真的读不出来，才说读不到。']
    return '\n'.join(parts)


def _history_block(session_id) -> str:
    """本会话前几轮：用户说「把刚才那些做成 Excel」时，模型得看得见上一轮查回了什么。"""
    if not session_id:
        return ''
    try:
        from app.secretary import qa_log
        hist = qa_log.load_context(session_id, limit=5) or []
    except Exception:                               # noqa: BLE001 - 读不到按无历史
        return ''
    if not hist:
        return ''
    lines = []
    for h in hist[-5:]:
        lines.append('上一轮问：%s\n上一轮答：%s' % (
            str(h.get('raw_question') or '')[:200], str(h.get('answer') or '')[:1500]))
    return '\n\n【本会话前几轮】\n' + '\n\n'.join(lines)


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
    max_steps = int(max_steps or (8 if lane == 'read' else 14))

    hp = {'item': {}, 'block': ''}
    if lane == 'read':
        # 读件线只按这句话准确匹配（命中「速读」「校对」这类），
        # 不按产物兜底 —— 兜底会把「这个文章说了什么」硬套成《文章纠错》
        hit = HT.match(question) or {}
        if hit:
            hp = HT.for_prompt(hit.get('task') or '', '', question)
    elif lane != 'chat':
        hp = HT.for_prompt((routed or {}).get('howto') or '', product, question)
    item = hp.get('item') or {}
    if lane != 'chat':
        trace.append({'seq': len(trace) + 1, 'kind': 'plan', 'tool': 'plan',
                      'result': {'做法': item.get('task') or (
                                     '（无做法，按通用流程）' if lane == 'read'
                                     else '（没匹配到做法，按通用版式）'),
                                 '产物': product,
                                 '步骤': ['取做法', '查数/读文件', '按做法成文', '生成文件',
                                          '自检', '交付'] if lane == 'office'
                                 else ['取做法', '读全文', '按字段出清单']}})
    tools_file.set_ctx(user_id=user_id,
                       out_dir='data/agent_out/%s' % (session_id or 'anon'),
                       howto=item.get('task') or '')

    user_content = '【本轮要办的事】\n' + str(question or '')
    user_content += '\n\n【今天】%s' % (today or time.strftime('%Y-%m-%d'))
    user_content += _history_block(session_id)
    files = list(doc_files or [])
    if files:
        lines = ['【文件%d】《%s》 约 %s 字，file_id=%s%s'
                 % (i, f.get('name'), f.get('chars'), f.get('file_id'),
                    '（本会话此前带来的）' if f.get('prev') else '')
                 for i, f in enumerate(files, 1)]
        user_content += '\n\n【可用的文件】\n' + '\n'.join(lines)
    elif doc_context:
        user_content += '\n\n【用户上传的文件】\n' + str(doc_context)[:20000]

    messages = [{'role': 'system',
                 'content': _system(hp.get('block') or '', product, lane, bool(files))},
                {'role': 'user', 'content': user_content}]

    names = READ_TOOLS if lane == 'read' else OFFICE_TOOLS
    if lane == 'chat' or (lane == 'read' and not files):
        # 没文件就别给 read_file：给一只手它就会自己编 file_id 去读（实测编成 file_id=1）
        tools = []
    else:
        tools = [t for t in A.TOOLS if t['function']['name'] in names]
    answer = ''

    for step in range(1, max_steps + 1):
        if should_stop is not None and should_stop():
            answer = '（已停止生成）'
            break
        resp = A._chat(messages, model, think=False, use_tools=bool(tools), tools=tools or None)
        msg = resp['choices'][0]['message']
        calls = msg.get('tool_calls') or []
        if not calls:
            answer = (msg.get('content') or '').strip()
            break
        messages.append({'role': 'assistant', 'content': msg.get('content') or '',
                         'tool_calls': calls})
        live = [c for c in calls if ((c.get('function') or {}).get('name')) != 'finish']
        outs = A._exec_calls(live) if live else []
        by_id = {tc.get('id'): o for tc, o in zip(live, outs)}
        for tc in calls:
            if ((tc.get('function') or {}).get('name')) == 'finish':
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
        if any(((c.get('function') or {}).get('name')) == 'finish' for c in calls):
            break

    artifacts = []
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

    if not answer:
        answer = ('文件已生成：' + artifacts[0]['name']) if artifacts else '本轮没有给出内容。'
    result = {
        'input_question': question,
        'answer': answer,
        'lane': lane,
        'product': product,
        'howto': item.get('task') or '',
        'plan': (trace[0].get('result') if trace else {}),
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
