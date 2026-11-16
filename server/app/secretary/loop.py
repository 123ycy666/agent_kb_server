# -*- coding: utf-8 -*-
"""干活线单循环（阶段 3）：7 只工具 + 自检门 + 四件套。

    检索资料 search_material   资料库/制度/判据（**不含说明书** —— 说明书走 read_doc）
    读文档全文 read_doc        说明书正文、知识库文档原文；说明书正文读进来就记「本轮说明书」
    读会话文件 read_session_file  本轮带来的 / 本会话此前上传或产出的
    查数 query_data           指标代号优先，否则 SQL；口径与错字纠正都在工具内部
    出文件 make_deliverable   docx/xlsx/pptx；版式由说明书决定，模型只给内容
    自检 self_check           命中说明书且有检查项时，逐条报结论+依据
    收尾 finish               数据够了/活干完了

交不交文件由**自检门**决定（V2）：本轮读了带检查项的说明书 → 没自检、
或者有不通过项 → 文件不给出去，整改后重报。

代码不替模型挑做法，也不替模型管节奏（保留的只有步数上限，防死循环）。
"""
from __future__ import annotations

import json
import re
import time

TOOLS = [
    {'type': 'function', 'function': {
        'name': 'search_material',
        'description': '在资料库里检索**制度文件、管理办法、流程规定、判据标准**的原文切片。'
                       '问「怎么规定的 / 谁审批 / 标准是多少」这类问题用它。'
                       '它**不返回说明书**——说明书用 read_doc 按名字取。',
        'parameters': {'type': 'object', 'properties': {
            'query': {'type': 'string', 'description': '检索意图（用文档里可能出现的词）'},
            'top_k': {'type': 'integer', 'description': '返回几条，默认 5'}},
            'required': ['query']}}},
    {'type': 'function', 'function': {
        'name': 'read_doc',
        'description': '读一篇文档的正文：传 doc_name 先按**说明书名字**找，找到就返回说明书正文；'
                       '找不到再按知识库文档名找（分页读，next_offset 为 null 才算读完）。'
                       '正文里《书名号》是要顺着读的其它说明书 —— 用 read_doc(doc_name="那个名字") 接着读。',
        'parameters': {'type': 'object', 'properties': {
            'doc_name': {'type': 'string', 'description': '说明书名或知识库文档名'},
            'doc_id': {'type': 'integer', 'description': '或者直接给文档 id'},
            'offset': {'type': 'integer', 'description': '从第几块开始，默认 0'},
            'limit': {'type': 'integer', 'description': '读多少块，默认 40'}}}}},
    {'type': 'function', 'function': {
        'name': 'read_session_file',
        'description': '读本会话的文件（本轮带来的，或此前上传/产出的）。'
                       '用户说「这个文件」「刚才那份」时用它，file_id 见【会话文件清单】。'
                       '一次读一段，next_offset 为 null 才算读完。',
        'parameters': {'type': 'object', 'properties': {
            'file_id': {'type': 'string'},
            'offset': {'type': 'integer'},
            'limit': {'type': 'integer'}},
            'required': ['file_id']}}},
    {'type': 'function', 'function': {
        'name': 'query_data',
        'description': '查业务库的数。有三种用法：'
                       '① 传 code（指标代号，来自 list_metrics 或说明书）——系统按口径把过滤条件拼全、'
                       '把派生值算好，你只传代号/年份/范围 id；'
                       '② 传 sql（或 sql 数组，互不依赖的多条一次全传，框架并发跑）——只允许业务字典里的表；'
                       '③ 什么都不传只传 table —— 看这张表的字段。'
                       '数字只来自这个工具；查不到就写「需另行核实」。',
        'parameters': {'type': 'object', 'properties': {
            'code': {'type': 'string', 'description': '指标代号'},
            'year': {'type': 'string'},
            'scope_id': {'type': 'integer'},
            'sql': {'anyOf': [{'type': 'string'},
                              {'type': 'array', 'items': {'type': 'string'}}]},
            'table': {'type': 'string', 'description': '看这张表的字段清单'},
            'list_tables': {'type': 'boolean', 'description': 'true = 列出可用表'},
            'find_column': {'type': 'string', 'description': '按词找字段'}},
            'required': []}}},
    {'type': 'function', 'function': {
        'name': 'make_deliverable',
        'description': '生成并交付文件（docx / pptx / xlsx）。版式（字体/字号/行距/页边距/落款/'
                       '页码）由说明书自动套，你只给内容。'
                       'kind=docx 给 {title, sections:[[小节标题,正文]], sign_off:[单位,日期], '
                       'tables:[{header:[列],rows:[[值]]}]}；'
                       'kind=pptx 给 {title, slides:[{layout:cover|section|bullets|end,title,bullets}]}；'
                       'kind=xlsx 给 {sheets:[{name,title,header:[列名],rows:[[值]],'
                       'formats:["#,##0.00"],widths:[14],total_row:true,freeze:"A3"}]}。'
                       '★ 说明书有检查项时，**必须先 self_check 报过全通过**，否则这份文件不给出去。',
        'parameters': {'type': 'object', 'properties': {
            'kind': {'type': 'string', 'enum': ['docx', 'pptx', 'xlsx']},
            'spec': {'type': 'object', 'description': '按上面的形状给内容'}},
            'required': ['kind', 'spec']}}},
    {'type': 'function', 'function': {
        'name': 'self_check',
        'description': '交付前自检：按说明书给的检查项逐条报「结论 + 依据」。'
                       'items 用说明书里的检查项原文（一项不改），verdicts 每项一个 '
                       '{item, ok, evidence}。有 false 就不许出文件，整改后重报。',
        'parameters': {'type': 'object', 'properties': {
            'verdicts': {'type': 'array', 'items': {'type': 'object', 'properties': {
                'item': {'type': 'string'},
                'ok': {'type': 'boolean'},
                'evidence': {'type': 'string'}},
                'required': ['item', 'ok']}},
            'note': {'type': 'string'}},
            'required': ['verdicts']}}},
    {'type': 'function', 'function': {
        'name': 'finish',
        'description': '活干完了 / 数查够了就调用它收尾，同时给一句话交付说明。',
        'parameters': {'type': 'object', 'properties': {
            'summary': {'type': 'string', 'description': '一句话说明交了什么'}},
            'required': ['summary']}}},
]

NAMES = tuple(t['function']['name'] for t in TOOLS)
DEFAULT_CHECK = ('结构', '篇幅', '出处')
KIND_HINT = {
    'docx': '{"title": "标题", "sections": [["一、小节", "正文段落，用\\n分段"]], '
            '"sign_off": ["单位全称", "2026 年 11 月 16 日"], '
            '"tables": [{"header": ["列1"], "rows": [["值"]]}]}',
    'pptx': '{"title": "标题", "slides": [{"layout": "cover", "title": "", "subtitle": ""}, '
            '{"layout": "bullets", "title": "", "bullets": ["一句一条"]}]}',
    'xlsx': '{"sheets": [{"name": "sheet名", "title": "表标题", "header": ["列1"], '
            '"rows": [[值]], "formats": ["#,##0.00"], "widths": [14], "total_row": true}]}',
}


class Gate:
    """自检门：本轮说明书有检查项时，没报全通过就不给文件。"""

    def __init__(self):
        self.spec = {}          # 本轮读进来的说明书
        self.verdicts = []      # [{item, ok, evidence}]
        self.notes = ''

    def require(self) -> list:
        return list((self.spec or {}).get('check_items') or [])

    def cover(self) -> dict:
        req = self.require()
        if not req:
            return {'ok': True, 'required': [], 'default': list(DEFAULT_CHECK),
                    'verdict': '无需自检（没有检查项）'}
        got = {str(v.get('item') or '').strip(): v for v in self.verdicts}
        missing = [r for r in req if str(r).strip() not in got]
        failed = [v for v in self.verdicts if not v.get('ok')]
        if missing:
            return {'ok': False, 'required': req, 'missing': missing,
                    'verdict': '缺 %d 项没报：%s' % (len(missing), '；'.join(missing[:5])),
                    'how': '照检查项原文逐条 self_check({item, ok, evidence})，一项都别省'}
        if failed:
            return {'ok': False, 'required': req,
                    'failed': failed, 'verdict': '不通过 %d 项：%s'
                    % (len(failed), '；'.join(str(f.get('item')) for f in failed[:5])),
                    'how': '按不通过项整改内容，改完重新 self_check 全部重报'}
        return {'ok': True, 'required': req,
                'verdict': '全通过（%d 项）' % len(req)}


def _system(question, spec_hit, product, files, today, inj) -> str:
    parts = [inj['system']]
    if spec_hit:
        parts.append('【按说明书的交付契约】这一轮要先照《%s》做；它写的步骤、格式、口吻、'
                     '检查项优先于你的习惯。' % spec_hit['item']['name'])
    if product:
        parts.append('【这一轮要交 %s】内容想好后调 make_deliverable(kind="%s", spec=%s)。'
                     '出完文件一句话说明做了什么，不要把正文再贴一遍。'
                     % (product, product, KIND_HINT.get(product, '{}')))
        parts.append('数字只用 query_data 查回来的原始结果；查不到就写「需另行核实」。')
    else:
        parts.append('【按用户说的办】要读文件就读、要查数就查、要出文件就出、要聊天就聊天；'
                     '不需要工具的时候直接回答，不要为了显得忙去查库或出文件。')
    if files:
        parts.append('【会话文件】用户说「这个文件」「刚才那份」时，指清单里的文件；'
                     '用 read_session_file(file_id) 读正文（读到 next_offset 为 null）。')
    else:
        parts.append('【没有可用文件】用户提到文件时，直接说这次没收到，'
                     '请他把文件发过来或把正文贴进来，不要编 file_id。')
    return '\n\n'.join(p for p in parts if p and p.strip())


def _num_sources(trace) -> list:
    out = []
    for t in trace:
        if t.get('kind') != 'tool' or t.get('tool') != 'query_data':
            continue
        r = t.get('result') or {}
        sqls = r.get('sqls') or ([r['sql']] if r.get('sql') else [])
        if isinstance(r.get('results'), list):
            sqls += [x.get('sql') for x in r['results'] if isinstance(x, dict) and x.get('sql')]
        for s in sqls:
            out.append({'sql': str(s)[:2000], 'step': t.get('seq'),
                        'rows': r.get('row_count') or r.get('count')})
    return out


def run(question, *, lane='office', model, session_id=None, user_id=None, user_code=None,
        client_ip=None, channel=None, doc_files=None, doc_context=None, trace=None,
        should_stop=None, max_steps=None, today=None, routed=None) -> dict:
    from app.secretary import agent as A
    from app.secretary import config as CFG
    from app.secretary import inject, name_fix, qa_log, report, spec as SP
    from app.secretary import tools_db, tools_file, tools_kb, tools_result

    trace = trace if trace is not None else []
    t_all = time.time()
    model = model or CFG.MODEL
    max_steps = int(max_steps or 14)
    routed = routed or {}
    product = '' if lane == 'chat' else (routed.get('product') or '')
    if product not in ('docx', 'xlsx', 'pptx'):
        product = '' if lane == 'chat' else (product or 'docx')
    today = today or time.strftime('%Y-%m-%d')

    # 0) 错字/名称归一（工具内部完成，模型感知不到；与问数线各自一份，互不影响）
    q_text, nf = str(question or ''), {}
    try:
        nf = name_fix.fix(q_text)
        if nf.get('used'):
            q_text = nf['text']
    except Exception as exc:                       # noqa: BLE001
        print('[loop] 纠错失败（按原话）：%s' % str(exc)[:100], flush=True)
    if nf.get('used'):
        trace.append({'seq': len(trace) + 1, 'kind': 'namefix', 'tool': 'name_fix',
                      'args': {'input': question},
                      'result': {'used': True, 'fixed': q_text,
                                 'word_fixes': nf.get('word_fixes') or [],
                                 'name_fixes': nf.get('name_fixes') or []}})

    # 1) 说明书：代码只按优先级挑（点名 > 适用词 > 本人 > 全局 > 最新版），正文按需注入
    spec_hit = {}
    if lane != 'chat':
        try:
            spec_hit = SP.load(user_id, question=q_text)
        except Exception as exc:                   # noqa: BLE001
            print('[loop] 取说明书失败（按无说明书）：%s: %s' % (type(exc).__name__, exc),
                  flush=True)
    if spec_hit and not product:
        product = str(spec_hit['item'].get('product') or '') or 'docx'
    files = list(doc_files or [])
    # 上一轮产出的文件也要进会话清单（E2「接着改」/E3「这个文件」都靠它）：
    # 模型看不到上一轮的正文，只能按 file_id 读回来。只回看最近几轮。
    if session_id:
        try:
            have = {str(f.get('file_id')) for f in files}
            for f in (qa_log.session_files(session_id, limit=6) or []):
                if str(f.get('file_id')) not in have:
                    files.append(dict(f, prev=True))
                    have.add(str(f.get('file_id')))
        except Exception as exc:                   # noqa: BLE001
            print('[loop] 读会话文件清单失败（按本轮文件）：%s' % str(exc)[:100], flush=True)

    # 2) 装配：身份→纪律→目录→摘要→前几轮→会话文件→说明书正文→工具说明（顺序固定）
    history, summary = [], ''
    if lane != 'chat' and session_id:
        try:
            history = qa_log.load_context(session_id, limit=5) or []
        except Exception:                          # noqa: BLE001
            history = []
        try:
            from app.secretary import session_summary
            s = session_summary.get(session_id) if hasattr(session_summary, 'get') else None
            summary = (s or {}).get('summary') if isinstance(s, dict) else (s or '')
        except Exception:                          # noqa: BLE001
            summary = ''
    inj = inject.build(user_id, session_id, q_text, spec=spec_hit or None,
                       history=history, files=files, summary=summary or '')
    trace.append({'seq': len(trace) + 1, 'kind': 'inject', 'tool': 'inject',
                  'result': {'total': inj['total'], 'budget': inj['budget'],
                             'parts': inj['parts'], 'compressed': inj['compressed']}})

    tools_file.set_ctx(user_id=user_id, out_dir='data/agent_out/%s' % (session_id or 'anon'),
                       howto=(spec_hit.get('item') or {}).get('name') or '')
    gate = Gate()
    gate.spec = spec_hit.get('item') or {}
    checks = gate.require()
    trace.append({'seq': len(trace) + 1, 'kind': 'plan', 'tool': 'plan',
                  'result': {'说明书': spec_hit['item']['name'] if spec_hit else '（未命中，按通用能力做）',
                             '产物': product or '（由用户诉求决定）',
                             '检查项': checks or list(DEFAULT_CHECK),
                             '自检': '必过' if checks else '默认三项（不强制拦）',
                             '注入字数': inj['total']}})

    user_content = '【本轮要办的事】\n' + str(source_q := question or '')
    if q_text != str(question or ''):
        user_content += '\n（系统按错字表与名称表已把业务对象名改对，读作：%s）' % q_text
    user_content += '\n\n【今天】' + today
    if gate.require():
        user_content += ('\n\n【自检要求】这篇说明书的检查项必须在交付前逐条 self_check 报过'
                         '（一项不改、每项给依据）：\n' + '\n'.join(
                             '%d. %s' % (i, c) for i, c in enumerate(gate.require(), 1)))
    messages = [{'role': 'system',
                 'content': _system(question, spec_hit or None, product, files, today, inj)},
                {'role': 'user', 'content': user_content}]
    tools = [] if lane == 'chat' else [t for t in TOOLS
                                       if not (t['function']['name'] == 'read_session_file'
                                               and not files)]

    # 3) 循环
    answer, stopped, model_err = '', False, ''
    last_finish = ''
    for step in range(1, max_steps + 1):
        if should_stop is not None and should_stop():
            stopped = True
            break
        try:
            resp = A._chat(messages, model, think=False, use_tools=bool(tools),
                           tools=tools or None)
            msg = resp['choices'][0]['message']
        except Exception as exc:                   # noqa: BLE001 - 失败要给人话 + 重试（V7/E8）
            model_err = '%s: %s' % (type(exc).__name__, str(exc)[:200])
            print('[loop] 模型调用失败：%s' % model_err, flush=True)
            break
        calls = msg.get('tool_calls') or []
        if not calls:
            answer = (msg.get('content') or '').strip()
            break
        messages.append({'role': 'assistant', 'content': msg.get('content') or '',
                         'tool_calls': calls})
        for tc in calls:
            name = (tc.get('function') or {}).get('name') or ''
            args = _args(tc)
            t0 = time.time()
            try:
                out, done = _exec(name, args, gate=gate, session_id=session_id,
                                  user_id=user_id, trace=trace)
            except Exception as exc:               # noqa: BLE001 - 工具出错回给模型自纠
                out, done = {'error': '这只工具执行失败：%s。换个做法或改参数重试。'
                                      % str(exc)[:200]}, False
            trace.append({'seq': len(trace) + 1, 'kind': 'tool', 'tool': name,
                          'args': args, 'round': step,
                          'ms': int((time.time() - t0) * 1000), 'result': out})
            messages.append({'role': 'tool', 'tool_call_id': tc.get('id'),
                             'content': tools_result.deliver(name, out,
                                                             session_id=session_id)
                             if name in ('query_data', 'read_session_file', 'search_material')
                             else json.dumps(out, ensure_ascii=False, default=str)[:6000]})
            if name == 'finish':
                last_finish = str(args.get('summary') or '')
        if any((tc.get('function') or {}).get('name') == 'finish' for tc in calls):
            break

    # 4) 交付物 + 四件套
    artifacts, spec_used = [], {}
    for t in trace:
        r = t.get('result') or {}
        if not isinstance(r, dict):
            continue
        if r.get('spec_name') and r.get('spec_body_read'):
            spec_used = {'name': r['spec_name'], 'version_no': r.get('spec_version'),
                         'check_items': r.get('spec_checks') or []}
        if t.get('tool') == 'make_deliverable' and r.get('url'):
            artifacts.append({'kind': r.get('kind'), 'name': r.get('name'),
                              'bytes': r.get('bytes'), 'file_id': r.get('file_id'),
                              'url': r.get('url'),
                              'self_check': (r.get('self_check') or {}).get('verdict') or '未自检'})
    if not spec_used and gate.spec:
        spec_used = {'name': gate.spec.get('name'), 'version_no': gate.spec.get('version_no'),
                     'check_items': gate.require()}
    cover = gate.cover()
    numbers = _num_sources(trace)
    if model_err and not answer:
        answer = ('模型这次没答上来（%s）。可以说「重试」再来一次；'
                  '如果一直这样，换一句更具体的说法或稍后再试。'
                  % ('连不上模型服务' if 'ModelUnavailable' in model_err
                     else '调用出错'))
    if not answer:
        if stopped:
            answer = '（已停止生成）'
        elif artifacts:
            answer = '文件已生成：' + artifacts[0]['name']
        elif last_finish:
            answer = last_finish
        else:
            answer = '本轮没有给出内容。'
    if stopped and artifacts:
        # 可中断（E7）：停下就不留半成品交付物
        artifacts = []
    # 本轮产出的文件也记进会话清单：下一轮说「这个文件」「再改一版」时能找回来
    session_files = list(files)
    have_f = {str(f.get('file_id')) for f in session_files}
    for a in artifacts:
        if a.get('file_id') and str(a['file_id']) not in have_f:
            session_files.append({'file_id': a['file_id'], 'name': a.get('name'),
                                  'chars': 0, 'product': True})
            have_f.add(str(a['file_id']))

    result = {
        'input_question': question, 'answer': answer, 'lane': lane, 'product': product,
        'howto': spec_used.get('name') or '',
        'spec_used': spec_used, 'self_check': cover, 'number_sources': numbers,
        'artifacts': artifacts, 'trace': trace, 'steps': len(trace), 'model': model,
        'db_query_count': len([t for t in trace if t.get('tool') == 'query_data']),
        'kb_query_count': len([t for t in trace if t.get('tool') == 'search_material']),
        'no_db_query': not any(t.get('tool') == 'query_data' for t in trace),
        'qtype': '闲聊' if lane == 'chat' else ('办公' if product else '读件'),
        'today': today, 'files': session_files,
        'injection': {'total': inj['total'], 'budget': inj['budget'],
                      'parts': inj['parts'], 'compressed': inj['compressed']},
        'stopped': stopped,
        'elapsed_ms': int((time.time() - t_all) * 1000),
        'session_id': session_id, 'qa_id': None,
    }
    try:
        rec = qa_log.save(result, session_id=session_id, user_id=user_id,
                          user_code=user_code, client_ip=client_ip, channel=channel)
        result['session_id'] = rec.get('session_id') or session_id
        result['qa_id'] = rec.get('qa_id')
        report.save(result.get('qa_id'), result)
    except Exception as exc:                       # noqa: BLE001 - 落库失败不影响交付
        print('[loop] 落库失败（不影响交付）：%s: %s' % (type(exc).__name__, exc), flush=True)
    return result


def _args(tc) -> dict:
    raw = (tc.get('function') or {}).get('arguments') or '{}'
    try:
        out = json.loads(raw)
        return out if isinstance(out, dict) else {}
    except Exception:                              # noqa: BLE001
        return {}


def _exec(name, args, *, gate, session_id, user_id, trace):
    """执行一只工具。返回 (结果, 是否收尾)。"""
    from app.secretary import agent as A
    from app.secretary import spec as SP, tools_file

    if name == 'search_material':
        out = A.execute('kb_search', {'query': args.get('query') or '',
                                      'top_k': int(args.get('top_k') or 5)})
        nodes = [n for n in (out.get('nodes') or [])
                 if not _is_spec_doc(n.get('doc_name'))]
        return {'nodes': nodes, 'total': len(nodes),
                'note': '资料/制度切片。说明书不在检索里，用 read_doc(doc_name=…) 取。'}, False

    if name == 'read_doc':
        nm = str(args.get('doc_name') or '').strip()
        if nm:
            hit = SP.load(user_id, name=nm)
            if hit:
                from app.secretary import tools_file as TF
                TF.set_howto(hit['item']['name'])
                return {'spec_name': hit['item']['name'], 'spec_body_read': True,
                        'spec_version': hit['item'].get('version_no'),
                        'spec_checks': hit['item'].get('check_items') or [],
                        'product': hit['item'].get('product') or '',
                        'body': hit['body'],
                        'refs': hit['refs'], 'conflicts': hit['conflicts'],
                        'note': '这是说明书正文，照它做；《书名号》是别的说明书，要就再 read_doc。'
                        }, False
        return tools_file.kb_read(args.get('doc_id'), nm, int(args.get('offset') or 0),
                                  int(args.get('limit') or 40)), False

    if name == 'read_session_file':
        return tools_file.read_file(str(args.get('file_id') or ''),
                                    int(args.get('offset') or 0),
                                    int(args.get('limit') or 40)), False

    if name == 'query_data':
        if args.get('list_tables'):
            return A.execute('list_tables', {'keyword': ''}), False
        if args.get('find_column'):
            return A.execute('find_column', {'keyword': args['find_column']}), False
        if args.get('table'):
            return A.execute('describe_table', {'table': args['table']}), False
        if args.get('code'):
            return A.execute('metric_query', {'code': args['code'], 'year': args.get('year'),
                                              'scope_id': args.get('scope_id')}), False
        if args.get('sql'):
            sql = args['sql']
            out = A.execute('run_sql', {'sql': sql})
            if isinstance(out, dict):
                out = dict(out)
                out.setdefault('sql', sql if isinstance(sql, str) else None)
                out.setdefault('sqls', [sql] if isinstance(sql, str) else list(sql))
            return out, False
        return {'error': 'query_data 要给 code / sql / table / list_tables / find_column 之一'}, False

    if name == 'self_check':
        vs = args.get('verdicts') or []
        gate.verdicts = [{'item': str(v.get('item') or ''), 'ok': bool(v.get('ok')),
                          'evidence': str(v.get('evidence') or '')}
                         for v in vs if isinstance(v, dict)]
        gate.notes = str(args.get('note') or '')
        cover = gate.cover()
        return dict(cover, verdicts=gate.verdicts), False

    if name == 'make_deliverable':
        cover = gate.cover()
        if not cover['ok']:
            return {'error': '自检没过，文件不给出去：' + cover['verdict'],
                    'self_check': cover,
                    'how': cover.get('how') or '按检查项整改后重新 self_check，再出文件'}, False
        out = A.execute('make_file', {'kind': args.get('kind') or 'docx',
                                      'spec': args.get('spec') or {}})
        if isinstance(out, dict) and out.get('error'):
            return out, False
        if isinstance(out, dict):
            out = dict(out)
            out['self_check'] = {'verdict': cover['verdict'], 'items': gate.verdicts,
                                 'gate': 'spec' if gate.require() else 'default'}
        return out, False

    if name == 'finish':
        return {'ok': True, 'summary': str(args.get('summary') or '')}, True

    return {'error': '不认识的工具：%s（可用：%s）' % (name, '、'.join(NAMES))}, False


def _is_spec_doc(doc_name) -> bool:
    n = str(doc_name or '')
    return bool(re.match(r'^\[?说明书\]?', n)) or '说明书' in n
