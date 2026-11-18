# -*- coding: utf-8 -*-
"""问数线：问题 → 按口径写 SQL → 拿数据 → 按作答规则回答。

代码只做四件事：载入上下文、拼提示词、跑工具循环、落库。
口径、作答规则、补全表、纠错词典、入口判据全部在页面上（PG），代码里不写、不校验、不拦截。
"""
import json
import os
import re
import socket
import ssl
import time
import urllib.error
import urllib.request

from app.secretary import agent_v2
from app.secretary import config
from app.secretary import name_fix
from app.secretary import preprocess
from app.secretary import qa_log
from app.secretary import router
from app.secretary import semantic
from app.secretary import tools_db
from app.secretary import tools_file
from app.secretary import tools_kb

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE


class ModelUnavailable(RuntimeError):
    """模型端点不可用（区别于「模型给出了答案」）。"""


TOOLS = [
    {'type': 'function', 'function': {
        'name': 'run_sql',
        'description': '在业务库执行一条只读查询，返回列名与数据行。查数、核对明细、做汇总都用它。',
        'parameters': {'type': 'object', 'properties': {
            'sql': {'type': 'string', 'description': '一条只读 SQL 语句'}},
            'required': ['sql']}}},
]



def _model_error_text(model, exc, detail=''):
    """把 urllib 的异常翻成一句人话（路由层直接回给用户）。

    2026-10-20 修：原来只报 HTTP 码，完全不看网关返回的 body ——
    结果「请求体里 tool 消息没补齐」（HTTP 400）被一律显示成
    「模型不可用，请换一个」，把用户和运维都引到错误方向（模型其实是好的）。
    现在把网关原文带上，并在能判定时直接说清是哪一类问题。
    """
    if isinstance(exc, urllib.error.HTTPError):
        low = (detail or '').lower()
        if 'tool_calls' in low or 'tool_call_id' in low:
            # 请求结构问题：_sanitize_messages 已经兜过一层，还报说明上游有别的缺口
            return ('模型「%s」拒绝了本次请求（HTTP %d）：工具调用消息结构不完整。'
                    '已自动重试仍失败，请把这条反馈给管理员（附时间）。'
                    % (model, exc.code))
        if 'model' in low and ('not found' in low or 'does not exist' in low
                               or 'not exist' in low or 'no such' in low):
            return ('模型「%s」在当前模型网关不存在（HTTP %d）。'
                    '请到「模型」下拉框换一个再问，或联系管理员确认该模型是否已部署。'
                    % (model, exc.code))
        tail = ('网关返回：%s' % detail[:200]) if detail else '网关未返回错误详情'
        if exc.code == 400:
            # 400 一律不是「模型没部署」——模型名不对会走上面的 not found 分支
            return ('模型「%s」返回参数错误（HTTP 400）。%s。'
                    '这通常是网关不接受本次请求的某个参数，请把这条反馈给管理员。'
                    % (model, tail))
        return ('模型「%s」在当前模型网关不可用（HTTP %d）。%s。'
                '请到「模型」下拉框换一个再问，或联系管理员确认该模型是否已部署。'
                % (model, exc.code, tail))
    return ('无法连接模型服务（%s）。请确认模型地址与网络是否正常。'
            % (getattr(exc, 'reason', exc),))


def _sanitize_messages(messages):
    """发送前修复 messages 结构 —— 挡住严格网关（DeepSeek）的两类 HTTP 400。

    2026-10-20 实测复现（原样报给用户的却是「模型不可用」，误导排查方向）：

      ① 缺 tool 响应
         {"code":"invalid_parameter_error",
          "message":"An assistant message with 'tool_calls' must be followed by tool
                     messages responding to each 'tool_call_id'.
                     (insufficient tool messages following tool_calls message)"}
      ② tool_call_id 对不上
         {"message":"The tool_call_id in the response message does not match the
                     tool_call_id in the tool_call: messages: message[2]."}

    产生原因（都在上游，不该让用户看见）：
      - 循环里某个工具结果取不到时走了 `continue`，少回填一条 tool 消息；
      - 网关偶发**不返回 id**，我们照着 None 回填；
      - 预算/步数到顶时 break，assistant(tool_calls) 后面还没来得及补 tool。

    qwen 系列对这两点很宽容（照常返回），所以只在 DeepSeek 上炸 ——
    这就是「同一个问题换个模型就好了」的真相，不是模型没部署。

    ⚠️ 这里是**兜底**，不替上游修 bug：能补就补（宁可让模型看到「结果缺失」，
       也不能让整轮问答 500）。返回新列表，不改调用方持有的 messages。
    """
    out = []
    n = len(messages)
    i = 0
    while i < n:
        m = messages[i]
        i += 1
        if m.get('role') != 'assistant' or not m.get('tool_calls'):
            out.append(m)
            continue

        # 深拷贝一层：下面可能要改 id，不能污染调用方（它后面还要接着用）
        m = dict(m)
        m['tool_calls'] = [dict(tc) for tc in m['tool_calls']]
        out.append(m)
        calls = m['tool_calls']
        # 网关没给 id → 自己造一个。必须**在 assistant 消息里也改**，
        # 只改 tool 消息没用（两边都对不上）。
        for k, tc in enumerate(calls):
            if not tc.get('id'):
                tc['id'] = 'call_auto_%d_%d' % (i, k)
        want = [tc.get('id') for tc in calls]

        got = set()
        # 先把紧随其后的 tool 消息原样搬过去（保持原有顺序）
        while i < n and messages[i].get('role') == 'tool':
            tm = messages[i]
            tid = tm.get('tool_call_id')
            if tid not in want:
                # 对不上的 id（典型是 None）：重映射到**还没被响应**的那个 call。
                # 丢弃它反而会少一条响应，重映射是唯一能同时满足「数量够 + id 匹配」的做法。
                spare = [w for w in want if w not in got]
                if spare:
                    tm = dict(tm, tool_call_id=spare[0])
                    tid = spare[0]
            if tid in want and tid not in got:
                got.add(tid)
                out.append(tm)
            i += 1
        # 仍然缺响应的 call：补占位。宁可让模型看到「结果缺失」，
        # 也不能让它整轮 400 —— 那会连历史记录都留不下来。
        for tid in want:
            if tid not in got:
                out.append({'role': 'tool', 'tool_call_id': tid,
                            'content': '{"ok":false,"error":"工具结果缺失，已用占位补齐"}',
                            'name': 'unknown'})
    return out


def _chat(messages, model, use_tools=True, max_tokens=None, think=True, tool_choice=None,
          tools=None):
    # ★ 默认开思考（**不要**关）：关掉思考时模型没有隐藏草稿纸，过程话术会直接写进 content，
    #   正文里出现「这是对策题。回答要点：…」「让我确认一下」「现在我已经收集了…」——
    #   领导看到的就是思考过程（实测，output/probe_thinking.py、probe_final_turn.py）。
    #   开思考时过程落在 reasoning 字段，content 只剩最终回答。
    # 代价：reasoning 也占 completion token —— 所以额度不设死（见下面 body 里的 output_tokens）。
    # 2026-10-10：模型换百炼 deepseek-v4.1-flash（内网 v4 到期），思考开关用 enable_thinking。
    body = {'model': model, 'messages': _sanitize_messages(messages),
            'temperature': 0}
    # 额度只有一个口（2026-11-08）：None / 0 = 不带这个字段，由网关按模型上限给。
    _mt = config.output_tokens(max_tokens)
    if _mt > 0:
        body['max_tokens'] = _mt
    if not think:
        # ★ 只用**查数轮**（正文不要，只要 tool_calls）：关思考每轮 59 token / 1.3s，
        #   开思考同一轮 159 token / 2.6s，而实测真实链路里开思考的查数轮能涨到
        #   1000~4000 token 的内心独白（一轮 20~68s）—— 那才是「一道分析题 3 分钟」的真凶。
        #   关掉它，查数轮的正文我们本来就不看（下面不再拿 content 当答案）。
        #   百炼的 DeepSeek-V4.1 认 **enable_thinking**（非标准参数、放顶层，实测关掉后 reasoning=0、
        #   正文直接出来）；原来内网 dsv4 认的是 chat_template_kwargs，随内网 v4 到期一起停用。
        body['enable_thinking'] = False
    if use_tools:
        # tools：给一个工具子集（收尾阶段只开放 get_rule，物理上不让它再去查数）
        body['tools'] = tools if tools else TOOLS
        if tool_choice:
            body['tool_choice'] = tool_choice
    body = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(config.LLM_BASE + '/chat/completions', data=body, headers={
        'Authorization': 'Bearer ' + config.LLM_KEY, 'Content-Type': 'application/json'})
    # ★ 重推理题偶发单次调用超过 HTTP_TIMEOUT（实测问「今年能达标吗」那次跑到 137s），
    #   直接抛出去就变成「服务异常：TimeoutError」这种用户看不懂的东西 —— 等于这题废了。
    #   所以**超时重试一次**并把等待放宽：多等一会儿，换回一个答案。
    last = None
    last_detail = ''
    for tmo in (config.HTTP_TIMEOUT, config.HTTP_TIMEOUT + 90):
        try:
            raw = urllib.request.urlopen(req, timeout=tmo, context=_ctx).read().decode('utf-8', 'replace')
            return json.loads(raw)
        except urllib.error.HTTPError as e:
            # ★ 必须先把 body 读出来：这是唯一能区分「模型没部署」和「请求体不合法」的证据。
            #   漏掉它，400 就只能笼统报「模型不可用」（2026-10-20 的真实误诊）。
            try:
                last_detail = e.read().decode('utf-8', 'replace')[:500]
            except Exception:                      # noqa: BLE001  body 读失败不影响主流程
                last_detail = ''
            print('[agent] 模型 %s 返回 HTTP %d：%s'
                  % (model, e.code, last_detail[:300]), flush=True)
            # 429/5xx 是**端点侧暂时不可用**：实测 3 并发时页面调用被 429 打回，
            # 一次不重试就白丢一张页面（日志证据见 output/test_results/…/EV-0005）。
            # 4xx（除 429）是请求本身的问题，重试没意义，照样抛。
            if e.code not in (429, 500, 502, 503, 504):
                raise ModelUnavailable(_model_error_text(model, e, last_detail)) from e
            last = e
            time.sleep(5)
            print('[agent] 模型端点暂时不可用（HTTP %s），等 5 秒重试一次' % e.code, flush=True)
        except (socket.timeout, TimeoutError) as e:
            last = e
            print('[agent] 模型调用超时（%ss），重试一次' % tmo, flush=True)
        except (urllib.error.URLError, ConnectionResetError) as e:
            if isinstance(e, urllib.error.URLError) and not isinstance(
                    getattr(e, 'reason', None), (socket.timeout, TimeoutError, ConnectionResetError)):
                raise ModelUnavailable(_model_error_text(model, e)) from e  # 不是超时（DNS/连接被拒…）就别重试
            last = e
            time.sleep(2)
            print('[agent] 模型连接被重置，重试一次', flush=True)
    raise ModelUnavailable(_model_error_text(model, last, last_detail)) from last


def _ask_short(system, user, model, max_tokens=None):
    """一次极短的判断调用（第 1 步「要不要走」、第 6 步「问题确定」用）。

    关思考 + 不给工具 + 低温：要的是"一个词"或"四行定稿"，不要散文。
    失败直接抛 —— 调用方兜底（按"走" / 不归整），不许把整题搞挂。
    """
    resp = _chat([{'role': 'system', 'content': str(system or '')},
                  {'role': 'user', 'content': str(user or '')}],
                 model, use_tools=False, max_tokens=max_tokens, think=False)
    msg = resp['choices'][0]['message'] or {}
    return str(msg.get('content') or '').strip()


def execute(name, args):
    if name == 'kb_search':
        return tools_kb.kb_search(args.get('query', ''), int(args.get('top_k') or 3))
    if name == 'find_column':
        return tools_db.find_column(args.get('keyword', ''))
    if name == 'list_tables':
        # 2026-11-10：函数早就在 tools_db 里，只是没有工具面 —— 模型只能靠写错表名从报错里
        #   读可用表清单（q1 蒙对、q2 干脆 0 条 SQL）。给它一只手自己看。
        return tools_db.list_tables(args.get('keyword', ''))
    if name == 'describe_table':
        # 2026-10-30：字段明细从"常驻提示词"改成"按需取"（system 里只留表清单）。
        return describe_table(args.get('table', ''))
    if name == 'list_metrics':
        return tools_db.list_metrics()
    if name == 'metric_query':
        return tools_db.metric_query(args.get('code', ''), args.get('year'),
                                     args.get('scope_id'))
    if name == 'run_sql':
        # 2026-11-05：sql 允许传数组 —— 一次调用带多条互不依赖的查询，框架内部并发跑。
        # 为什么要这样：模型经常一条一条发（每轮一条），8 轮问数就是这么来的；
        # 数组参数让"一次查全"不依赖它发多个 tool_call。
        _sql = args.get('sql')
        # 模型常把数组当成 JSON 字符串传（'["SELECT …","SELECT …"]'）——这里认下来，
        # 不然它一次想发多条会被当成一条非法 SQL 报"只允许 SELECT 查询"。
        if isinstance(_sql, str) and _sql.strip().startswith('['):
            try:
                _sql = json.loads(_sql)
            except Exception:                      # noqa: BLE001 - 解析不了就按单条 SQL 处理
                pass
        if isinstance(_sql, list):
            import concurrent.futures as _cf
            _items = [str(x) for x in _sql if str(x or '').strip()][:8]
            if not _items:
                return {'error': 'sql 数组是空的'}
            with _cf.ThreadPoolExecutor(max_workers=min(6, len(_items))) as _ex:
                _outs = list(_ex.map(_one_sql, _items))
            return {'multi': True, 'count': len(_outs), 'results': _outs}
        return _one_sql(_sql)
    if name == 'make_file':
        from app.secretary import tools_file
        return tools_file.make_file(args.get('kind', ''), args.get('spec') or {},
                                    self_check=bool(args.get('self_check', True)))
    return {'error': 'unknown tool ' + str(name)}


def describe_table(table):
    """取一张表的字段明细（只允许业务字典里的表）。

    2026-11-11：业务字典为空/没登记这张表时，退回直读 information_schema ——
    否则模型只能靠报错猜列名（实测一次任务连猜 3 个不存在的列、废掉 17 条 SQL）。
    """
    name = semantic.table_name(table or '')
    if not name:
        return {'error': '缺少表名'}
    cols = semantic.table_cols(name)
    if not cols:
        try:
            rows = tools_db._exec(
                'SELECT column_name AS c, data_type AS dt, column_comment AS cm '
                'FROM information_schema.columns WHERE table_schema=%s AND table_name=%s '
                'ORDER BY ordinal_position', [config.SCHEMA, name], limit_rows=300)['rows']
        except Exception as exc:                    # noqa: BLE001 - 查不到就如实说
            return {'error': '字段读不出来：%s: %s' % (type(exc).__name__, str(exc)[:120])}
        if not rows:
            return {'error': '库里没有这张表（或没有权限）：%s' % name,
                    'available': sorted(semantic.allowed_tables())[:20]}
        lines = ['- %s（%s）%s' % (r['c'], r.get('dt') or '',
                                   ('：' + r['cm'][:80]) if r.get('cm') else '')
                 for r in rows]
        return {'table': name, 'fields': len(rows), 'source': 'information_schema',
                'text': '\n'.join(lines)}
    lines = []
    for c in cols.values():
        item = c['column_name']
        if c['column_cn_name']:
            item += '（%s）' % c['column_cn_name']
        desc = (c['business_desc'] or '').strip()
        if desc and desc != c['column_cn_name']:
            item += '：' + desc[:120]
        lines.append('- ' + item)
    return {'table': name, 'fields': len(cols), 'text': '\n'.join(lines)}


def rule_slices(query, top_k=None, limit=700):
    """**已停用**（2026-10-30）：规则不再用向量检索。

    保留这个函数只是为了不让老调用方 ImportError；新代码请用 `rule_doc.get()`。
    """
    return [], 0.0, True


def _one_sql(sql):
    """跑一条查询，结果原样返回（不判定、不劝阻、不汇总）。"""
    return tools_db.run_sql(sql or '')


def _args_of(tc):
    raw = (tc.get('function') or {}).get('arguments') or '{}'
    try:
        out = json.loads(raw)
        return out if isinstance(out, dict) else {}
    except Exception:                                 # noqa: BLE001
        return {}


def _final_answer(messages, model):
    """再要一次正文：关掉工具，只让它写。（空正文时用）"""
    msgs = list(messages) + [{'role': 'user', 'content':
                              '请直接给出最终回答正文，不要调用工具、不要复述过程。'}]
    for think in (False, True):
        resp = _chat(msgs, model, use_tools=False, think=think)
        out = str((resp['choices'][0]['message'] or {}).get('content') or '').strip()
        if out:
            return out
    return ''


def ask(question, model=None, max_steps=None, profile=None, want_page=False, today=None, scope=None,
        page_async=False, session_id=None, user_id=None, user_code=None, client_ip=None, channel=None,
        history_turns=None, doc_context=None, doc_files=None, persona=None, trace=None,
        should_stop=None, force_data=False):
    """问数线一次问答。上游不需要预处理：错字、简称、漏时间都在这里补。

    profile / scope / want_page / page_async / doc_context / persona 收下但不用（接口兼容，产品基线里
    这些不再是回答的输入）。文档与页面的生成能力已下线。
    """
    model = model or config.MODEL
    max_steps = int(max_steps or config.MAX_STEPS)
    trace = trace if trace is not None else []
    try:
        from app.secretary import runtime_cfg as _rc
        _vals = _rc.refresh() or {}
        model = model or config.MODEL
        max_steps = int(max_steps or config.MAX_STEPS)
    except Exception:                                 # noqa: BLE001 - 配置读不到用 .env
        pass
    raw_question = str(question or '')
    question = raw_question
    t_all = time.time()

    # 入口：判成干活线就交出去（要文件 / 带了文件 / 在指会话里的文件）
    try:
        _r = router.route(raw_question, has_files=bool(doc_files))
    except Exception as exc:                          # noqa: BLE001 - 判不了按问数走
        print('[agent] 入口判断失败（按问数走）：%s: %s' % (type(exc).__name__, str(exc)[:120]), flush=True)
        _r = {'lane': 'ask_db', 'product': '', 'why': '判断失败'}
    trace.append({'seq': len(trace) + 1, 'kind': 'route', 'tool': 'router', 'result': _r})
    if _r.get('lane') != 'ask_db' and not force_data:
        from app.secretary import loop as _loop
        return _loop.run(raw_question, lane='office', model=model, session_id=session_id,
                         user_id=user_id, user_code=user_code, client_ip=client_ip,
                         channel=channel, doc_files=list(doc_files or []) or None,
                         doc_context=doc_context, trace=trace, should_stop=should_stop,
                         max_steps=max_steps, today=today, routed=_r)

    # ① 名称与错字纠错
    t_fix = time.time()
    fixed = name_fix.fix(question)
    if fixed.get('used'):
        question = fixed['text']
    namefix_ms = int((time.time() - t_fix) * 1000)
    trace.append({'seq': len(trace) + 1, 'kind': 'namefix', 'tool': 'name_fix', 'ms': namefix_ms,
                  'args': {'input': raw_question},
                  'result': {'used': bool(fixed.get('used')),
                             'fixed': question if fixed.get('used') else '',
                             'word_fixes': fixed.get('word_fixes') or [],
                             'name_fixes': fixed.get('name_fixes') or []}})

    # ② 补全表（命中给参考，不替用户改问题）
    comp = {'changed': False, 'hits': []}
    try:
        comp = preprocess.complete(question) or comp
        if comp.get('changed'):
            question = comp['text']
            trace.append({'seq': len(trace) + 1, 'kind': 'complete', 'tool': 'complete',
                          'result': {'before': raw_question, 'text': question,
                                     'hits': comp.get('hits') or []}})
    except Exception as exc:                          # noqa: BLE001 - 补全失败按原话继续
        print('[agent] 补全失败（按原话继续）：%s' % str(exc)[:120], flush=True)

    # ③ 上下文（只给「问题确定」看）
    turns = config.HISTORY_TURNS if history_turns is None else max(0, int(history_turns))
    history = []
    if turns and session_id:
        try:
            history = qa_log.load_context(session_id, limit=turns)
        except Exception as exc:                      # noqa: BLE001
            print('[agent] 读取上下文失败（按无上下文继续）：%s: %s'
                  % (type(exc).__name__, exc), flush=True)

    # ④ 问题确定
    _now = today or time.strftime('%Y-%m-%d')
    try:
        conf = confirm_run(question, fixed, comp, history, _now, model)
    except Exception as exc:                          # noqa: BLE001 - 定不了就用原话
        print('[agent] 问题确定失败（用原话）：%s: %s' % (type(exc).__name__, str(exc)[:120]), flush=True)
        conf = {'used': False, 'text': '', 'block': '【本轮原话】' + str(question or '')}
    trace.append({'seq': len(trace) + 1, 'kind': 'confirm', 'tool': 'confirm',
                  'result': {'used': bool(conf.get('used')), 'ask_time': _now,
                             'passed': 'model' if conf.get('used') else 'raw',
                             'text': str(conf.get('text') or conf.get('block') or '')[:800]}})
    body = conf['text'] if conf.get('used') else (conf.get('block') or str(question or ''))
    user_content = '【本轮要办的事】\n' + str(body) + '\n\n【今天】' + _now

    # ⑤ 提示词：身份 + 查数口径 + 作答规则（全部来自页面）
    messages = [{'role': 'system', 'content': agent_v2.system(_now)},
                {'role': 'user', 'content': user_content}]
    trace.append({'seq': len(trace) + 1, 'kind': 'system', 'tool': 'system',
                  'result': {'chars': len(messages[0]['content'])}})

    # ⑥ 循环：只有一只 run_sql
    answer = ''
    sql_count = 0
    step = 0
    while True:
        step += 1
        if max_steps and step > max_steps:
            answer = _final_answer(messages, model)
            trace.append({'seq': len(trace) + 1, 'kind': 'final',
                          'content': '已到步数上限 %d' % max_steps})
            break
        if should_stop is not None and should_stop():
            answer = '（已停止生成）'
            trace.append({'seq': len(trace) + 1, 'kind': 'final', 'content': '用户点了停止'})
            break
        t0 = time.time()
        resp = _chat(messages, model, think=False, use_tools=True, tools=TOOLS)
        llm_ms = int((time.time() - t0) * 1000)
        msg = resp['choices'][0]['message'] or {}
        calls = msg.get('tool_calls') or []
        if not calls:
            answer = str(msg.get('content') or '').strip()
            trace.append({'seq': len(trace) + 1, 'kind': 'answer', 'model_ms': llm_ms,
                          'content': answer[:600]})
            break
        messages.append({'role': 'assistant', 'content': msg.get('content') or '',
                         'tool_calls': calls})
        for tc in calls:
            fn = (tc.get('function') or {}).get('name') or ''
            args = _args_of(tc)
            t1 = time.time()
            try:
                out = execute(fn, args)
            except Exception as exc:                  # noqa: BLE001 - 工具报错回给模型自己处理
                out = {'error': '%s: %s' % (type(exc).__name__, str(exc)[:300])}
            if fn == 'run_sql':
                sql_count += 1
            trace.append({'seq': len(trace) + 1, 'kind': 'tool', 'tool': fn, 'args': args,
                          'ms': int((time.time() - t1) * 1000), 'round': step, 'result': out})
            body_out = json.dumps(out, ensure_ascii=False, default=str)
            cap = int(getattr(config, 'TOOL_RESULT_CHARS', 0) or 0)
            if cap and len(body_out) > cap:
                body_out = body_out[:cap]
            messages.append({'role': 'tool', 'tool_call_id': tc.get('id'), 'content': body_out})
        print('[agent] 第 %d 轮：工具 %d 次，本轮模型 %d ms，累计 %.1f s'
              % (step, len(calls), llm_ms, time.time() - t_all), flush=True)

    if not answer:
        answer = _final_answer(messages, model)

    result = {
        'input_question': raw_question,
        'today': _now,
        'fixed_question': question,
        'name_fix_used': bool(fixed.get('used')),
        'answer': answer,
        'trace': trace,
        'model': model,
        'steps': step,
        'db_query_count': sql_count,
        'sql_query_count': sql_count,
        'kb_query_count': 0,
        'no_db_query': sql_count == 0,
        'qtype': '查数',
        'elapsed_ms': int((time.time() - t_all) * 1000),
        'timings': {'namefix_ms': namefix_ms, 'total_ms': int((time.time() - t_all) * 1000)},
        'history_used': len(history),
        'files': list(doc_files or []),
        'page_html': '',
        'page_token': '',
        'artifacts': [],
        'personal': {'rules': [], 'rules_chars': 0, 'overflow': ''},
        'rules_loaded': 0,
        'history_injected': 0,
        'summary_used': False,
        'unverified': False,
    }
    try:
        rec = qa_log.save(result, session_id=session_id, user_id=user_id, user_code=user_code,
                          client_ip=client_ip, channel=channel)
        result['session_id'] = rec['session_id']
        result['qa_id'] = rec['qa_id']
    except Exception as exc:                          # noqa: BLE001 - 落库失败不影响回答
        result['session_id'] = session_id
        result['qa_id'] = None
        print('[qa_log] 落库失败（不影响回答）：%s: %s' % (type(exc).__name__, exc), flush=True)
    try:
        from app.secretary import session_summary
        result['summary_queued'] = session_summary.maybe_enqueue(
            result.get('session_id') or session_id)
    except Exception as exc:                          # noqa: BLE001
        print('[agent] 投递摘要任务失败（不影响回答）：%s' % str(exc)[:120], flush=True)
    return result


def confirm_run(question, fixed, comp, history, ask_time, model):
    """第 ④ 步：交给 confirm（判据在页面 confirm 框里）。"""
    from app.secretary import confirm
    return confirm.run(question, fixed, comp, history, ask_time=ask_time,
                       ask=lambda _s, _u: _ask_short(_s, _u, model))
