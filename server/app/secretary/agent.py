# -*- coding: utf-8 -*-
"""模型自主循环：只给工具与底线，不给流程。

分工：**信息补全由本服务自己做**（字面纠错与名称归一 → 时间与地点补全），
问题补全走知识库的补全规则，再查口径、取数、作答。
上游那个补全应用已不在链路里；时间与地点补全已整体删除（2026-11-03），由补全表配。
全部在一个模型循环里由模型自己排顺序，代码不写业务流程。
"""
import concurrent.futures
import hashlib
import socket
import threading
import uuid
from contextvars import copy_context
import json
import re
import ssl
import time
import urllib.error
import urllib.request

from app.secretary import agent_v2
from app.secretary import config
from app.secretary import evidence_gate
from app.secretary import gaps
from app.secretary import name_fix
from app.secretary import semantic
from app.secretary import tools_db
from app.secretary import tools_file
from app.secretary import tools_kb

# 闸门判定（"这句答案能不能直接交出去"）已搬到 evidence_gate.py（2026-11-02）：
# 原先是「问题侧词表 _BIZ + 答案侧数字 _CLAIM」两条正则，问题侧那半是开集，补不完。
# 下面这个 _BIZ 只留给词表覆盖用（_apply_pack_lexicon），**不再参与闸门判定**。
_BIZ = re.compile(r'供电所|供电公司|台区|工单|预算|审批|线损|停电|缺陷|售电|指标|项目')


TOOLS = [
    {'type': 'function', 'function': {
        'name': 'kb_search',
        'description': '检索业务知识库里的**制度文件、管理办法、流程规定原文**。'
                       '★ 问的是「流程 / 规定 / 标准 / 制度 / 怎么规定的 / 谁审批」这类问题时，'
                       '**答案就在这个工具的返回原文里** —— 直接据此作答；'
                       '不要因为业务库查了 0 行就说「系统里没有」（实测踩过：知识库里明明有'
                       '《请假流程》原文，模型却答「没有请假流程」）。'
                       '★ 问的是**业务数字**时，用它确认术语口径与定义，数字仍以 run_sql 为准。'
                       '返回按相关度排序的文档切片（含所属章节）。',
        'parameters': {'type': 'object', 'properties': {
            'query': {'type': 'string', 'description': '检索意图（用文档里可能出现的词，别用口语缩写）'},
            'top_k': {'type': 'integer', 'description': '返回几条，默认 5'}},
            'required': ['query']}}},
    {'type': 'function', 'function': {
        'name': 'find_column',
        'description': '在业务字典内的表里，按字段名或字段中文注释搜字段，返回「表名 + 字段名 + 类型 + 注释 + 示例值」。'
                       '当你不确定某个数据在哪个字段/哪张表时先用它（例如搜「线损」「执行金额」「台区」）。',
        'parameters': {'type': 'object', 'properties': {'keyword': {'type': 'string'}}, 'required': ['keyword']}}},
    {'type': 'function', 'function': {
        'name': 'run_sql',
        'description': '在只读业务库执行 SELECT（只允许业务字典里的表；不支持分号与注释，最多返回 200 行）。'
                       '★ sql 可以传**字符串或字符串数组**：互不依赖的查询一次全传进来，框架内部并发跑。'
                       '★ 调工具的那几轮**只给工具调用，不要写任何说明文字**；数据查够后就只回一句「数据已查全」。',
        'parameters': {'type': 'object', 'properties': {'sql': {'type': 'string'}}, 'required': ['sql']}}},
    # ★ 2026-10-30：字段明细与规则条目改成"按需取"，不再全塞进 system。
    {'type': 'function', 'function': {
        'name': 'describe_table',
        'description': '取一张表的字段清单（字段名/中文名/说明）。system 里只有表名清单，'
                       '**写 SQL 前不确定字段名就先调它**；同一张表一次问全，不要反复查。',
        'parameters': {'type': 'object', 'properties': {
            'table': {'type': 'string', 'description': '表名，如 t_power_work_order'}},
            'required': ['table']}}},
    {'type': 'function', 'function': {
        'name': 'get_rule',
        'description': '按主题取公司规则原文（判据/口径/阈值）。**要做判断（是否异常/逾期/超阈/'
                       '合理、该先办哪个）之前必须先调它**；取不到就说明「没有这条规则」，'
                       '不许自己编标准，也不许拿别的主题的标准套。',
        'parameters': {'type': 'object', 'properties': {
            'topic': {'type': 'string',
                      'description': '主题名，取自 system 里的「规则目录」，如「八、审批停留」'}},
            'required': ['topic']}}},
    # ★ 收尾信号（2026-10-09 加）：查数轮强制 tool_choice=required，模型不能写散文，
    #   于是用一个空工具表示"到此为止"。数据查够就调它，之后由系统另行要一次正文。
    {'type': 'function', 'function': {
        'name': 'finish',
        'description': '数据已经查够、可以写汇报了，就调用它收尾（不要再写任何文字）。',
        'parameters': {'type': 'object', 'properties': {
            'plan': {'type': 'string', 'description': '一句话说明你打算怎么答'}}, 'required': ['plan']}}},
    # ── 文件类（2026-11-05）：读 / 取知识库 / 出文件 / 跑脚本 ───────────────
    {'type': 'function', 'function': {
        'name': 'read_file',
        'description': '读本轮用户上传的文件。一次读一段，返回块（段落号、章节、正文）；'
                       'next_offset 为 null 表示读完。长文件要接着读，不要只读开头就下结论。',
        'parameters': {'type': 'object', 'properties': {
            'file_id': {'type': 'string', 'description': '文件 id（见【本轮带来的文件】）'},
            'offset': {'type': 'integer', 'description': '从第几块开始，默认 0'},
            'limit': {'type': 'integer', 'description': '读多少块，默认 40'}},
            'required': ['file_id']}}},
    {'type': 'function', 'function': {
        'name': 'kb_read',
        'description': '取知识库里一篇文档的正文（按块顺序，带章节）。'
                       '动手前要先取「做法」「规定」这类原文时用它；kb_search 只回切片，不够。',
        'parameters': {'type': 'object', 'properties': {
            'doc_id': {'type': 'integer', 'description': '文档 id（kb_search 结果里有）'},
            'doc_name': {'type': 'string', 'description': '或按标题模糊找'},
            'offset': {'type': 'integer', 'description': '从第几块开始，默认 0'},
            'limit': {'type': 'integer', 'description': '读多少块，默认 40'}},
            'required': []}}},
    {'type': 'function', 'function': {
        'name': 'make_file',
        'description': '生成文件并交付下载。kind=docx 给 {title, sections:[[小节,正文]]}；'
                       'kind=pptx 给 {title, slides:[{layout:cover|section|bullets|end,title,bullets}]}；'
                       'kind=xlsx 给 {sheets:[{name, rows:[[...]]}]}。生成后自动过自检，不通过不给文件。'
                       '★ 内容要精简：一份文件一次给完，别超过 20 页/12 行；超了分两次调用。',
        'parameters': {'type': 'object', 'properties': {
            'kind': {'type': 'string', 'enum': ['docx', 'pptx', 'xlsx']},
            'spec': {'type': 'object', 'description': '按上面的形状给内容'},
            'self_check': {'type': 'boolean', 'description': '是否过自检，默认 true'}},
            'required': ['kind', 'spec']}}},
    {'type': 'function', 'function': {
        'name': 'run_code',
        'description': '在沙箱里跑一段 python（docx/pptx/openpyxl 可用，产物从 out/ 取）。'
                       '只在结构化参数表达不了时用：能用 make_file 就别写脚本。',
        'parameters': {'type': 'object', 'properties': {
            'code': {'type': 'string', 'description': 'python 源码'},
            'timeout': {'type': 'integer', 'description': '秒，默认 60'}},
            'required': ['code']}}},
]

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE


class ModelUnavailable(RuntimeError):
    """模型服务不可用：网关没这个模型（404/400）、调用失败、或连不上。

    ⚠️ 为什么要单独定义（平台线 2026-10-16 修的真实故障）
    ------------------------------------------------------
    原先 `_chat` 把 `urllib.error.HTTPError` 原样抛出去，用户看到的是
    「服务异常：HTTPError HTTP Error 404」这种看不懂的东西，而且路由层分不清
    「该模型不可用」和「服务内部出错」，这轮问答连历史记录都不留。
    统一成这条异常后，路由层可以照常落库并给人话提示。
    """


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


def _chat(messages, model, use_tools=True, max_tokens=4000, think=True, tool_choice=None,
          tools=None):
    # ★ 默认开思考（**不要**关）：关掉思考时模型没有隐藏草稿纸，过程话术会直接写进 content，
    #   正文里出现「这是对策题。回答要点：…」「让我确认一下」「现在我已经收集了…」——
    #   领导看到的就是思考过程（实测，output/probe_thinking.py、probe_final_turn.py）。
    #   开思考时过程落在 reasoning 字段，content 只剩最终回答。
    # 代价：reasoning 也占 completion token，max_tokens 给小了会把正文挤空 —— 所以放大到 4000。
    # 2026-10-10：模型换百炼 deepseek-v4.1-flash（内网 v4 到期），思考开关用 enable_thinking。
    body = {'model': model, 'messages': _sanitize_messages(messages),
            'temperature': 0, 'max_tokens': max_tokens}
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


def _rule_tool():
    """工具表里那一个 get_rule（收尾阶段只开放它）。"""
    for t in TOOLS:
        if ((t.get('function') or {}).get('name')) == 'get_rule':
            return [t]
    return []


def _final_answer(messages, model, max_tokens=2500, allow_rule=False, trace=None):
    """兜底：关掉工具再明确要一次正文。

    用在 content 为空的场合（实测 Q10「申报有没有不该批的」跑 594s、16 次查询后返回空正文）：
    开着思考时 reasoning 会把 max_tokens 吃满，正文就没了。不能把 reasoning 当答案给领导
    （那是内心独白），更不能给空白 —— 不带工具时它没得可调，只能写正文。

    ★ allow_rule=True（2026-11-02）：先开放**仅 get_rule 一个工具**发一次。原因——
      收尾前的 system 里换了 answer+check 的非必带清单（19 条「每类题怎么答」），
      而原来是 `use_tools=False` 直接要正文：清单给了、工具关了，
      那 19 条规则从来没被读过（实测）。现在给它一轮机会按代号取，最多一轮，防死循环。
      trace 传进来就把这次取用记进去（kind='tool', tool='get_rule'），线上能看见。
    """
    msgs = list(messages) + [{'role': 'user', 'content':
                              '请直接给出给领导的最终回答正文，不要调用工具、不要复述分析过程、不要写思考过程。'}]
    if allow_rule and _rule_tool():
        try:
            _r0 = _chat(msgs, model, use_tools=True, tools=_rule_tool(),
                        max_tokens=600, think=False)
            _msg0 = _r0['choices'][0]['message']
            _calls0 = _msg0.get('tool_calls') or []
        except Exception as exc:                       # noqa: BLE001 - 取规则失败就照旧要正文
            print('[agent] 收尾取规则失败（直接要正文）：%s: %s'
                  % (type(exc).__name__, str(exc)[:120]), flush=True)
            _msg0, _calls0 = {}, []
        if _calls0:
            # 开场那句「不要调用工具」会误导它，这里把真实意图补一句
            msgs[-1] = {'role': 'user', 'content':
                        '要按某类题作答、或要判定标准时，可以先用 get_rule(代号) 取规则原文'
                        '（代号照抄注入块末尾清单里的那个，别自己编名字）；取到之后'
                        '再写出给领导的最终回答正文，不要复述取规则的过程。'}
            msgs.append({'role': 'assistant', 'content': _msg0.get('content') or '',
                         'tool_calls': _calls0})
            for tc in _calls0:
                _fn = (tc.get('function') or {}).get('name') or ''
                try:
                    _args = json.loads((tc.get('function') or {}).get('arguments') or '{}')
                except Exception:                      # noqa: BLE001
                    _args = {}
                _out = {}
                if _fn == 'get_rule':
                    try:
                        from app.secretary import rule_doc as _rd
                        _out = _rd.get(topic=_args.get('topic') or _args.get('key') or '')
                    except Exception as exc:           # noqa: BLE001
                        _out = {'error': str(exc)[:160]}
                msgs.append({'role': 'tool', 'tool_call_id': tc.get('id'),
                             'content': json.dumps(_out, ensure_ascii=False)[:4000]})
                if trace is not None:
                    trace.append({'seq': len(trace) + 1, 'kind': 'tool', 'tool': _fn,
                                  'args': _args, 'phase': 'final',
                                  'result': {'ok': bool(_out.get('text')),
                                             'chars': len(_out.get('text') or ''),
                                             'code': _out.get('code'),
                                             'matched_by': _out.get('matched_by'),
                                             'error': _out.get('error')}})
    # 关思考：实测同一次「写汇报」开着思考要 23s（reasoning 2117 字 + 正文 382 字），
    # 关掉只需几秒；正文由上面的明确口令限定，不缺东西。
    # ★ 偶发空正文（实测 12 题里 1 次：生成 13.6 秒却 content 为空，疑似退化重复被 strip 掉），
    #   这时**开思考重试一次**；空回答绝不能给领导。
    for think in (False, True):
        resp = _chat(msgs, model, use_tools=False, max_tokens=max_tokens, think=think)
        out = (resp['choices'][0]['message'].get('content') or '').strip()
        if out:
            return out
    return ''


def _ask_short(system, user, model, max_tokens=400):
    """一次极短的判断调用（第 1 步「要不要走」、第 6 步「问题确定」用）。

    关思考 + 不给工具 + 低温：要的是"一个词"或"四行定稿"，不要散文。
    失败直接抛 —— 调用方兜底（按"走" / 不归整），不许把整题搞挂。
    """
    resp = _chat([{'role': 'system', 'content': str(system or '')},
                  {'role': 'user', 'content': str(user or '')}],
                 model, use_tools=False, max_tokens=max_tokens, think=False)
    msg = resp['choices'][0]['message'] or {}
    return str(msg.get('content') or '').strip()


def _swap_materials(messages, slots):
    """把 system 里的「规则材料」段换成另一阶段的（换，不叠）。

    2026-11-01：查数循环带 fetch + judge；收尾写正文前换成 answer + check。
    上一阶段的材料整段切掉，中间查到的数据留在对话轮次里 —— 规则退出，结论留下。
    """
    try:
        from app.secretary import prompt_material
        blk = prompt_material.block(slots)
        msgs = list(messages)
        if not msgs or msgs[0].get('role') != 'system':
            return msgs
        base = prompt_material.strip_block(str(msgs[0].get('content') or ''))
        msgs[0] = {'role': 'system', 'content': base + (blk or '')}
        return msgs
    except Exception as exc:                       # noqa: BLE001 - 换材料失败不影响回答
        print('[agent] 换规则材料失败（沿用当前 system）：%s' % str(exc)[:120], flush=True)
        return messages


_FACTS_RULE = ('把这一轮已经查到的事实列成清单，一条一行，只写〔口径〕=〔数值〕（来自哪张表）；'
               '查过但没数据的那条就写「无数据」。不要写结论、不要写建议、不要写过程。')


def _collect_facts(messages, model, trace=None) -> str:
    """第 7 步收尾：只把查到的事实列成清单，不给工具、不要结论。

    2026-11-04：原来收尾直接拿全部工具结果写回答，数据一多模型就照抄，回答规则管不住。
    改成先落一份清单，第 8 步只看这份清单。
    """
    msgs = list(messages) + [{'role': 'user', 'content': _FACTS_RULE}]
    try:
        resp = _chat(msgs, model, use_tools=False, max_tokens=1200, think=False)
        out = str(resp['choices'][0]['message'].get('content') or '').strip()
    except Exception as exc:                       # noqa: BLE001 - 列不出清单就退回原做法
        print('[agent] 列事实清单失败（退回全部工具结果）：%s: %s'
              % (type(exc).__name__, str(exc)[:120]), flush=True)
        return ''
    if trace is not None:
        trace.append({'seq': len(trace) + 1, 'kind': 'facts', 'chars': len(out),
                      'content': out[:800]})
    return out


def _answer_system(messages, slots=('answer',)) -> str:
    """收尾那次的 system：基础提示词 + 本轮动作对应的回答材料。"""
    sys_txt = str((messages[0] or {}).get('content') or '') if messages else ''
    try:
        from app.secretary import prompt_material
        return (prompt_material.strip_block(sys_txt)
                + (prompt_material.block(tuple(slots)) or ''))
    except Exception:                              # noqa: BLE001
        return sys_txt


def _write_answer(messages, model, user_content, trace=None, need_facts=True,
                  slots=('answer',)) -> dict:
    """第 8 步：查数轮先列事实清单再写正文；读文件/出文件轮直接按清单规则写。

    列不出清单（模型不可用等）就退回原做法：把全部工具结果交给它。
    """
    facts = _collect_facts(messages, model, trace=trace) if need_facts else ''
    if facts:
        msgs = [{'role': 'system', 'content': _answer_system(messages, slots)},
                {'role': 'user', 'content': user_content},
                {'role': 'user', 'content': '【这一轮查到的事实（数字只用这些）】\n' + facts}]
        return {'answer': _final_answer(msgs, model, allow_rule=True, trace=trace),
                'facts': facts}
    return {'answer': _final_answer(_swap_materials(messages, tuple(slots)), model,
                                    allow_rule=True, trace=trace),
            'facts': ''}


def _facts_pool(text):
    """事实清单里的数值池（万元 + 两两和差比），对账用。清单一条一行，好抽。"""
    vals = set()
    body = str(text or '')
    for m in re.finditer(r'(-?\d[\d,]*(?:\.\d+)?)\s*万元', body):
        try:
            vals.add(round(float(m.group(1).replace(',', '')), 2))
        except ValueError:
            continue
    for m in re.finditer(r'(-?\d[\d,]*(?:\.\d+)?)', body):
        try:
            raw = float(m.group(1).replace(',', ''))
        except ValueError:
            continue
        if abs(raw) >= 10000:
            vals.add(round(raw / 10000.0, 2))
    base = sorted(vals)
    for i, a in enumerate(base):
        for b in base[i + 1:]:
            vals.add(round(a + b, 2))
            vals.add(round(abs(a - b), 2))
            if b:
                vals.add(round(a / b, 4))
    return vals


_TOOL_RESULT_CHARS = 4000      # 单个工具结果给模型的字符上限（可用 config 覆盖）


def _dump(obj):
    return json.dumps(obj, ensure_ascii=False, default=str)


# 按 create_time 分期的表：漏了时间条件就查成了「历年累计」，实测会被拿去和当年数比（报出 44 个所超支）
_TIME_TABLES = ('t_power_work_order',)


def _time_guard(sql, scope):
    """本轮口径有时间、这条 SQL 却没带时间条件 → 在结果后面直接点破它是历年累计。

    ★ 为什么在**工具返回值**上下手：规则是**按问句检索**注入的，这类通用条不一定被检索到；
      而「查历年数当当年」恰恰是在这一轮查询里犯的，就地提醒才拦得住。
    """
    s = ' '.join(str(sql or '').split()).lower()
    if not s or not any(t in s for t in _TIME_TABLES):
        return ''
    if 'create_time' in s or 'year(' in s:
        return ''
    t = (scope or {}).get('time') or {}
    if t.get('period_type') not in ('yearToDate', 'month', 'year'):
        return ''
    return ('\n\n⚠️ 本条 SQL **没有时间条件** —— 查出来的是**历年累计**，不是本轮口径「%s」。'
            '历年数不能和当年分配额 / 当年已审批放一起比（比出来的「超支 / 缺口」是假的）：'
            '要跟当年数对齐就补上 create_time 条件重查，否则只能单独作为「含往年」的背景说明。'
            % t.get('time_text'))


# 每张表里「范围」落在哪个字段上：SQL 里一个都没出现 → 它查的是全市
_SCOPE_COLUMNS = {
    't_power_work_order': ('power_company_name', 'power_company_id'),
    't_power_company_budget_result': ('power_station_name', 'county_company_name', 'power_station_id'),
    't_power_ai_metric_snapshot': ('scope_name',),
    't_power_ai_metric_comparison': ('scope_name',),
    't_power_push_station_score': ('obj_name',),
}


def _scope_guard(sql, scope):
    """本轮范围不是全市、而这条 SQL 没真正带上范围 → 就地提醒它查的是全市。

    ★ 实测两类：① `scope=随县供电公司` 那轮直接查全市（把已审批 19 件 / 112.11 万当随县数）；
      ② 写了 `power_company_name IN (SELECT name FROM t_power_company WHERE deleted_flag=0)`
      —— 看着像筛了，其实把 47 个所全选进来。所以判断标准是**范围有没有真的被表达出来**：
      出现范围名、parent_id、scope_key 之一才算带上了。
    """
    sql = ' '.join(str(sql or '').split())
    low = sql.lower()
    place = ((scope or {}).get('place') or '').strip()
    if not low or not place or place == '全市':
        return ''
    if not any(t in low for t in _SCOPE_COLUMNS):
        return ''
    head = place
    for suf in ('供电服务站', '供电公司', '供电中心', '供电所'):
        if head.endswith(suf):
            head = head[:-len(suf)]
            break
    if place in sql or (head and head in sql) or 'parent_id' in low or 'scope_key' in low:
        return ''
    return ('\n\n⚠️ 这条 SQL **没有真正带范围条件**，查出来的是**全市**数据，而本轮口径是「%s」——'
            '不能把全市数当成本范围的数。按所取：power_company_name / power_station_name 等值；'
            '按县公司取：power_company_id IN (该公司的所 id 列表，见【统计范围】里给的名单)。'
            % place)


def _scope_detail(place):
    """公司级范围：把该公司下辖的所名与所 id 直接列出来。

    ★ 为什么必须由系统给：实测模型要么写成 `power_company_name IN (SELECT name FROM t_power_company
      WHERE deleted_flag=0)`（等于没筛，把全市当本县），要么干脆不带范围条件。名单由系统查一次，
      模型只剩「按等值筛」这一件事。
    """
    p = (place or '').strip()
    if not p or p == '全市' or not (p.endswith('公司') or p.endswith('中心')):
        return ''
    try:
        r = tools_db.run_sql('SELECT pc.id, pc.name FROM t_power_company pc '
                             'JOIN t_power_supply_company sc ON sc.id = pc.parent_id '
                             "WHERE sc.name = %r AND pc.deleted_flag=0" % p)
        rows = r.get('rows') or []
    except Exception:                                          # noqa: BLE001
        return ''
    if not rows:
        return ''
    names = '、'.join(str(x.get('name')) for x in rows[:40])
    ids = ','.join(str(x.get('id')) for x in rows[:40])
    return ('\n      **该范围下辖 %d 个所**：%s\n'
            '      按所筛：power_company_name / power_station_name 等值，或 power_company_id / power_station_id 在 [%s] 之内；'
            '**不要写成 t_power_company 的整表子查询**。' % (len(rows), names, ids))


def _unit_guard(sql):
    """查了指标表就把原始单位说清楚 —— 实测把「0.18%」写成「1.85%」（亿/万混算）。"""
    low = ' '.join(str(sql or '').split()).lower()
    if 't_power_ai_metric' not in low:
        return ''
    return ('\n\n📏 单位：本表 metric_value / current_value 是**原始值** —— 售电量=**千瓦时**'
            '（所/县报万千瓦时＝值÷10000，全市报亿千瓦时＝值÷100000000）、线损率=%、'
            '停电时长=小时/户、工单与跳闸=件/次；change_rate 已是百分数。'
            '**算占比/倍数/同比之前先把两边化成同一单位**，算完自查量级。')


# 回答里的金额对账（数字闸）：金额必须在**本轮** SQL 结果里找得到出处，否则重问一次。
# ★ 为什么需要：汇总数已经改成由库算（summary），但实测仍有「76.26 万写成 762.62 万」这类
#   抄写错；这类错看一眼回答发现不了，只能拿本轮结果去对。
_MONEY_RE = re.compile(r'(\d+(?:\.\d+)?)\s*万元')


def _num_pool(trace):
    """本轮所有 SQL 结果构成的可追溯数值池（单位：万元）。

    池子 = 行值 + summary 的 sum/mean/min/max，再补上两两的「和 / 差 / 比值」——
    余额、缺口、占比、两所合计这些合规的再计算才不会误报。
    """
    vals = set()
    for t in (trace or []):
        if not isinstance(t, dict) or t.get('kind') != 'tool':
            continue
        res = t.get('result') or {}
        raw = []
        for row in (res.get('rows') or []):
            if isinstance(row, dict):
                raw.extend(row.values())
        for col in (res.get('summary') or {}).values():
            if isinstance(col, dict):
                raw.extend([col.get('sum'), col.get('mean'), col.get('min'), col.get('max')])
        for v in raw:
            try:
                x = float(str(v))
            except (TypeError, ValueError):
                continue
            vals.add(round(x, 2))
            vals.add(round(x / 10000.0, 2))
    out = set(vals)
    base = sorted(v for v in vals if abs(v) < 1e7)
    for a in base:
        for b in base:
            out.add(round(a + b, 2))
            out.add(round(abs(a - b), 2))
            if b:
                out.add(round(a / b, 4))
    return out


def _untraceable_money(answer, pool, floor=10.0):
    """回答里 >= floor 万元的金额，哪些在池子里找不到（容差 0.06 万或 0.2%）。"""
    bad = []
    for m in _MONEY_RE.finditer(answer or ''):
        try:
            x = float(m.group(1))
        except ValueError:
            continue
        if x < floor:
            continue
        if any(abs(x - s) <= max(0.06, abs(s) * 0.002) for s in pool):
            continue
        if x not in bad:
            bad.append(x)
    return bad


# 内部系数（0.6 达标系数那类）不许写成人看的「基准线 / 进度要求」—— 实测模型会写
# 「41 个所预算执行率低于 45% 的进度要求」。命中就重问一次。
_COEF_RE = re.compile(r'\d+(?:\.\d+)?\s*%\s*(?:的)?\s*(基准线|达标线|考核线|进度线|进度要求)')
_COEF_LOOSE_RE = re.compile(r'(?:低于|不低于|高于|达到)\s*\d+(?:\.\d+)?\s*%')
_COEF_CONTEXT = ('执行率', '进度', '达标', '完成率')


def _coefficient_talk(text):
    """回答里有没有把内部系数当标准讲 —— 有就返回那句（没有返回空串）。"""
    for ln in (text or '').splitlines():
        if _COEF_RE.search(ln):
            return ln.strip()[:80]
        if _COEF_LOOSE_RE.search(ln) and any(w in ln for w in _COEF_CONTEXT):
            return ln.strip()[:80]
    return ''


def _trim(name, res):
    """工具结果 → 给模型的 JSON 文本；超上限就**少列明细行**，绝不把 JSON 从中间切断。

    ★ 这里原来是 `json.dumps(res)[:4000]` —— 实测 47 行的分配结果就切在数字中间，
      模型拿到的是**坏 JSON**（Unterminated string），既看不到整份、还可能自己把可见行相加。
      改成整行进出之后：JSON 始终合法，row_count 保留真实行数，明细被丢这件事写在 note 里，
      并明确告诉它汇总去取 summary（tools_db 已随结果一起给）。
    """
    cap = int(getattr(config, 'TOOL_RESULT_CHARS', _TOOL_RESULT_CHARS) or _TOOL_RESULT_CHARS)
    text = _dump(res)
    if len(text) <= cap:
        return text
    obj = dict(res) if isinstance(res, dict) else {'value': res}
    # 最占地方的明细列表：整行地丢，直到放得下
    list_key = ''
    for k, v in obj.items():
        if isinstance(v, list) and v and (not list_key or len(_dump(v)) > len(_dump(obj[list_key]))):
            list_key = k
    if list_key:
        rows = obj[list_key]

        def build(n):
            o = dict(obj)
            o[list_key] = rows[:n]
            o['note'] = ('%s 共 %d 条，为省篇幅只列出前 %d 条（其余未列出）。'
                         '**合计 / 均值 / 最大最小一律取本结果的 summary —— 不要拿看得见的这几行自己相加**。'
                         % (list_key, len(rows), n))
            return o

        lo, hi = 0, len(rows)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if len(_dump(build(mid))) <= cap:
                lo = mid
            else:
                hi = mid - 1
        while lo > 0 and len(_dump(build(lo))) > cap:
            lo -= 1
        return _dump(build(lo))
    # 没有明细列表：截断最长的字符串值（仍保证是合法 JSON）
    for k in sorted([k for k, v in obj.items() if isinstance(v, str)], key=lambda k: -len(obj[k])):
        if len(_dump(obj)) <= cap:
            break
        over = len(_dump(obj)) - cap
        obj[k] = obj[k][:max(0, len(obj[k]) - over - 40)] + '……（本条已截断，需要就去查更具体的对象）'
    return _dump(obj)


# ---------------------------------------------------------------------------
# 输出清洗：把模型漏进正文的「过程话术 / Markdown 符号 / 英文思考句」洗掉。
#
# 为什么放在代码里而不是只写进提示词：实测 dsv4 即使被明确禁止，仍会把思考句
# （"I have all the data. Let me compile the report."）和 ** 加粗漏进正文；
# 提示词对它约束力不够。清洗是确定性的，且对所有模型都受益。
# 原则：**只洗格式与过程话术，不动内容与数字**；洗空了就退回原文，绝不返回空。
_MD_RULES = (
    (re.compile(r'\*\*(.+?)\*\*', re.S), r'\1'),                 # **粗体**
    (re.compile(r'^\s{0,3}#{1,6}\s*', re.M), ''),                  # # 标题
    (re.compile(r'^\s{0,3}>\s?', re.M), ''),                       # > 引用
    (re.compile(r'^\s{0,3}[-*+]\s+', re.M), ''),                   # - 项目符号
    (re.compile(r'^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*$', re.M), ''),  # 表格分隔行
)
_PROCESS_PATTERNS = (
    re.compile(r'(?:I have|I now have|Let me|Now I|Now let|Based on the|Okay,?|Sure,?)[^。\n]{0,140}[.\n]', re.I),
    re.compile(r'(?:我(?:已经|现在)?(?:掌握|拿到|获得|获取)了?[^。\n]{0,30}(?:数据|信息|指标|结果)[^。\n]{0,20}[。：:]\s*)'),
    re.compile(r'(?:让|接下来)?我(?:们)?(?:来)?(?:综合|组织|整理|汇总|判断|分析|回答|汇报|梳理)[^。\n]{0,30}[。：:]\s*'),
    re.compile(r'^(?:好的?|收到)[，,。]?\s*'),
    # 内部故障/过程话术（模型把"检索失败"也当正文写了）
    re.compile(r'[^。\n]{0,10}(?:知识库)?检索(?:持续)?失败[^。\n]{0,40}[。\n]?'),
    re.compile(r'[^。\n]{0,10}无法(?:获取|读取|访问)(?:到)?(?:判据)?规则[^。\n]{0,30}[。\n]?'),
    re.compile(r'但(?:我)?根据业务规则[^。\n]{0,30}(?:判断|判断标准)[^。\n]{0,20}[。\n]?'),
    # 数据确认句 / 收尾过渡句（实测 dsv4 常带）
    re.compile(r'[^。\n]{0,12}数据(?:已|都)?(?:确认|核对)?(?:已)?(?:齐|齐全|完毕|完整)[^。\n]{0,10}[。，]?\s*'),
    re.compile(r'(?:现在|接下来)(?:我)?(?:给出|开始|整理出|输出)?(?:给出)?(?:最终)?回答[。：:]?\s*'),
    re.compile(r'^(?:现在|接下来)(?:我)?(?:来)?(?:给出|说说|回答)[^。\n]{0,10}[。：:]\s*'),
)


def _clean_answer(text):
    """清洗模型漏进正文的格式符号与过程话术。只洗格式，不动内容；洗空就退回原文。"""
    if not text:
        return text
    out = text
    for pat, rep in _MD_RULES:
        out = pat.sub(rep, out)
    # 表格行：| a | b |  ->  a：b（一行一条，保留信息，不留竖线）
    def _row(m):
        cells = [c.strip() for c in m.group(1).split('|')]
        cells = [c for c in cells if c]
        return cells[0] + '：' + '，'.join(cells[1:]) if len(cells) >= 2 else (cells[0] if cells else '')
    out = re.sub(r'^\s*\|(.+?)\|\s*$', _row, out, flags=re.M)
    out = out.replace('|', '，')                            # 残留竖线
    for _ in range(3):                                   # 开头可能连着几句过程话术
        before = out
        for pat in _PROCESS_PATTERNS:
            out = pat.sub('', out, count=1)
        out = out.lstrip()
        if out == before:
            break
    for pat in _PROCESS_PATTERNS:                        # 正文中间漏的英文思考句也删
        out = pat.sub('', out)
    out = re.sub(r'[ \t]+\n', '\n', out)
    out = re.sub(r'\n{3,}', '\n\n', out).strip()
    return out if len(re.sub(r'\s', '', out)) >= 10 else text



def _one_sql(sql):
    """跑一条查询；0 行时把「空集就是答案」说死（模型会摘条件凑数，见下）。"""
    r = tools_db.run_sql(sql or '')
    try:
        if not r.get('error') and not (r.get('rows') or []):
            r = dict(r)
            r['空集提示'] = ('这是 0 行 = 空集，空集就是答案。不许摘掉时间 / 状态 / 范围条件再查一遍凑数字；'
                            '确实要改条件，必须在回答里写明改了什么、为什么。')
    except Exception:                              # noqa: BLE001
        pass
    return r


def execute(name, args):
    if name == 'kb_search':
        return tools_kb.kb_search(args.get('query', ''), int(args.get('top_k') or 3))
    if name == 'find_column':
        return tools_db.find_column(args.get('keyword', ''))
    if name == 'describe_table':
        # 2026-10-30：字段明细从"常驻提示词"改成"按需取"（system 里只留表清单）。
        return describe_table(args.get('table', ''))
    if name == 'get_rule':
        # 2026-10-30：规则不再靠向量检索《判据规则》，改成按主题/别名精确取。
        return get_rule(args.get('topic', ''), args.get('key', ''))
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
    # ── 文件类（2026-11-05）────────────────────────────────────────────
    if name == 'read_file':
        from app.secretary import tools_file
        return tools_file.read_file(args.get('file_id', ''), args.get('offset') or 0,
                                    args.get('limit') or 40)
    if name == 'kb_read':
        from app.secretary import tools_file
        return tools_file.kb_read(args.get('doc_id'), args.get('doc_name') or '',
                                  args.get('offset') or 0, args.get('limit') or 40)
    if name == 'make_file':
        from app.secretary import tools_file
        return tools_file.make_file(args.get('kind', ''), args.get('spec') or {},
                                    self_check=bool(args.get('self_check', True)))
    if name == 'run_code':
        from app.secretary import tools_file
        return tools_file.run_code(args.get('code', ''), args.get('timeout') or 60)
    return {'error': 'unknown tool ' + str(name)}




_ANALYSIS_WORDS = ('为什么', '为啥', '原因', '怎么降', '怎么提', '怎么办', '怎么治',
                   '建议', '该不该', '该干', '先干', '先治', '够不够', '花不完', '能不能',
                   '会到', '预测', '趋势', '对比', '合理', '异常', '风险', '方案', '情况', '怎么样')


def _wants_analysis(q):
    """这题要不要按"分析层"答（只用于 qtype 落库 / 页面生成判据，不再切换提示词模板）。"""
    return any(w in (q or '') for w in _ANALYSIS_WORDS)



# ── 按需工具：表字段明细 / 规则条目（2026-10-30）──────────────────────
# 为什么做成工具而不是常驻提示词：表目录 26 张表 = 22.6K 字符（占 system 72%），
# 规则靠 0.18 分的向量检索命中。改成"目录常驻 + 按需精确取"后，system 从 3.1 万字
# 降到 1 万以内，且规则命中从"看运气"变成"有确定答案"。
def describe_table(table):
    """取一张表的字段明细（只允许业务字典里的表）。"""
    name = semantic.table_name(table or '')
    if not name:
        return {'error': '缺少表名'}
    cols = semantic.table_cols(name)
    if not cols:
        return {'error': '表不在业务字典里：%s' % name,
                'available': sorted(semantic.allowed_tables())[:30]}
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


def get_rule(topic='', key=''):
    """按主题取规则条目（规则文档 = 知识库里 doc_type='rule' 的文档）。"""
    from app.secretary import rule_doc
    return rule_doc.get(topic, key)


def rule_slices(query, top_k=None, limit=700):
    """**已停用**（2026-10-30）：规则不再用向量检索。

    保留这个函数只是为了不让老调用方 ImportError；新代码请用 `rule_doc.get()`。
    """
    return [], 0.0, True


# 兜底：字典/提示词读不到时才用（正常情况下系统提示全部来自数据库）
_FALLBACK = '你是随州供电公司的项目管理秘书，负责回答业务问题。'

# ★ 2026-11-03（Fred）：RULES_PRIORITY（S1 谁说了算）常量已删除 ——
#   它每轮都进 system、页面又改不了，属于「我不填也生效的字」。
#   要恢复就在页面 `priority` 这个 key 里写（原文在
#   docs/08-历史归档/2026-11-03-删除的提示词规则/红线-优先级-装配说明-原文.md）。


def _system(today=None):
    """**兜底**系统提示：只在站点包装载失败时用。

    2026-10-30：删掉旧的 `ai_data.ai_prompt` 读取通道。
    那张表和站点包 md 是同一个东西的两份来源，线上又被 V2 装配完全绕过
    （表里改了不生效）—— 留着只会让人以为改了它有作用。现在提示词正文
    只有一处来源：站点包文件；这里只剩"身份 + 日期 + 码表 + 表目录"。
    """
    head = _FALLBACK
    # ★ 2026-11-03：兜底装配里的「今天的日期是…」也去掉（要日期在页面上自己写）。
    try:
        _codes = semantic.codes_menu()
        if _codes:
            head += '\n\n' + _codes
    except Exception:                               # noqa: BLE001 - 字典读不到不影响回答
        pass
    try:
        return head + '\n\n【可用数据表（只能查这些，字典外的表一律不可用）】\n' + semantic.table_menu()
    except Exception:
        return head


def _one_call(tc):
    name = tc['function']['name']
    _raw = tc['function'].get('arguments') or '{}'
    try:
        args = json.loads(_raw)
    except Exception:
        # 参数被截断（出文件时 spec 太长最常见）—— 必须回给模型一句人话，
        # 否则它只看到「缺参数」，会一遍遍重发同一份大参数。
        return {'name': name, 'args': {}, 'ms': 0,
                'result': {'error': '工具参数不是合法 JSON（多半是被长度截断）：%s。'
                                    '请把内容压缩后重发（出文件时分两次、每次不超过 20 页）。'
                                    % str(_raw)[-120:]}}
    t1 = time.time()
    try:
        res = execute(name, args)
    except Exception as e:
        res = {'error': type(e).__name__ + ': ' + str(e)[:300]}
    return {'name': name, 'args': args, 'result': res, 'ms': int((time.time() - t1) * 1000)}


def _exec_calls(calls):
    """同一轮的多个工具调用并发执行；返回顺序与 calls 一一对应。

    ★ 2026-10-20 修的真实缺陷（「知识库检索不用于回答」的根因之一）
    ------------------------------------------------------------------
    `kb_search` 的检索范围与登录身份**不是通过参数传的**，而是路由层
    用 ContextVar 登记的（`kb_adapter.set_scope / set_actor`）——因为工具是
    模型自主调用的，调用点不在我们的代码栈里，没法层层传参。

    contextvars 的语义是「按执行上下文隔离」：`anyio.to_thread`（路由层用的
    `run_in_threadpool`）会**复制**上下文，所以走进线程池那一刻还是对的；
    但 `ThreadPoolExecutor` **不会**——它用的是全新的空上下文。

    后果（实测：同一轮同时发 run_sql + kb_search）：
      - `current_scope()` 变 None ⇒ 检索范围退回 `config.KB_IDS`（常常是空）
        ⇒ `_search(kb_ids=[])` 命中 0 条 ⇒ 模型如实回答「知识库里没有相关记录」，
        可检索测试页明明能命中 —— 用户看到的就是「检索不用于回答」；
      - `current_actor_key()` 变 "service" ⇒ 只能看公开文档，且缓存按 service 分桶。

    而**单个**工具调用走下面的 fast path（同线程），完全不复现 ——
    这正是它一直没被发现的原因：现象时有时无，取决于模型这一轮发了几个工具。

    修法：提交任务时在**当前线程**复制上下文（`copy_context()` 必须在提交线程调用），
    再交给工作线程 run。每个任务各复制一份，避免同一个 Context 被并发 enter。
    """
    if len(calls) == 1:
        return [_one_call(calls[0])]
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, len(calls))) as ex:
        futs = [ex.submit(copy_context().run, _one_call, c) for c in calls]
        return [f.result() for f in futs]


# 「分析页面」用的系统提示：**独立的一次模型调用**，只产出一个单页 HTML 数据大屏。
PAGE_SYSTEM = (
    '你是HTML数据大屏生成助手小智。根据用户提供的内容，生成一个可直接运行的单页 HTML 页面。\n'
    '\n## 特殊规则\n'
    '- 如果用户内容中包含“澄清话术”，直接返回空字符串，不输出任何内容。\n'
    '- 只有在用户要求“生成列表”，生成的html页面不能生成echarts图表，必须输出表格\n'
    '- 只有在用户要求“编写报告”，生成的html页面不能生成echarts图表，也不用输出表格\n'
    '- 其他情况都要输出echarts表\n'
    '\n## 输出规范\n'
    '- 只能输出 HTML 源码本身。\n- 不要解释。\n- 不要使用 Markdown。\n- 不要使用代码块。\n'
    '- 不要输出除 HTML 以外的任何内容。\n- `body` 可滚动，但必须隐藏滚动条。\n'
    '\n## 页面要求\n'
    '- 必须包含 `<!DOCTYPE html>`、`<html>`、`<head>`、`<body>`。\n'
    '- 页面宽度 `100%`，高度 `100%`。\n- 表格平铺全部展示，不能用固定高度\n- 不能有滚动条\n'
    '- 不要生成页面标题。\n- 整体风格为科技风数据大屏。\n'
    '- 页面背景使用：`linear-gradient(145deg,#1e293b,#0f172a)`。\n'
    '- 全局字体使用：`font-family: cursive;`\n- 全局字号不得小于 `24px`。\n'
    '- 表格当中字体大小必须是20px\n'
    '\n## 技术要求\n'
    '- 使用 TailwindCSS CDN。\n- 使用 ECharts CDN。\n'
    '- 所有图表必须在 DOM 加载完成后再初始化，例如放在 `DOMContentLoaded` 中执行。\n'
    '- ECharts 必须基于已存在的 DOM 元素初始化，避免获取不到容器。\n'
    '- 每个图表都必须放在固定高度容器中。\n- ECharts 图表必须自适应父容器宽高。\n'
    '- 必须监听窗口变化并调用图表 `resize`。\n- 图表样式要有区分，不要全部使用同一种图表风格。\n'
    '\n## 内容规则\n'
    '- 只根据用户提供的内容进行布局和展示。\n- 不新增任何模块。\n- 不补充不存在的信息。\n- 不模拟数据。\n'
    '- 如果用户内容不足以生成图表，则使用文本、表格、分区块等方式展示，但仍然不能虚构内容。\n'
    '- 如果用户内容中包含“澄清话术”，直接返回空字符串，不输出任何内容。\n'
    '- 如果用户要求“生成列表”，生成的html页面不能生成echarts图表，必须输出表格\n'
    '- 如果用户要求“生成报告”，生成的html页面不能生成echarts图表，必须输出表格\n'
    '\n## 代码约束\n'
    '- 优先使用 Tailwind 工具类，尽量少写自定义 CSS。\n- `style` 标签内容控制在 10 行以内。\n'
    '- HTML 总行数控制在 240 行以内。\n- 代码必须可直接保存并运行。\n'
    '\n## 生成策略\n'
    '- 优先保证结构清晰、渲染稳定、代码简洁。\n- 减少冗余标签、重复样式和无意义包装层。\n'
    '- 仅输出最终 HTML 结果。\n'
    '- 图必须有数据：某类图**没有数据时不要保留空图容器**，改用卡片或表格展示 —— 空图比没有图更糟。'
)


def _strip_html(out):
    """从模型输出里取出 HTML 本体：去掉 ``` 包裹，丢掉 HTML 之前的任何说明文字。

    取不到 HTML（模型没照做）就返回空串 —— 宁可不要页面，也不要往接口里塞一段散文。
    """
    t = (out or '').strip()
    if t.startswith('```'):
        t = t.strip('`').strip()
        if t[:4].lower() == 'html':
            t = t[4:].lstrip()
    low = t.lower()
    # 整页优先；没有根标签就退一步认**片段**（实测模型常只给 div/table 片段，
    # 以前一律返回空 → 分析题白丢一张页面）。片段交前端照样能渲染。
    for tag in ('<!doctype', '<html', '<body', '<div', '<table', '<section', '<main'):
        i = low.find(tag)
        if i >= 0:
            return t[i:].strip()
    return ''


_PAGE_CACHE = {}          # md5(答案+数据) -> 已生成的页面 HTML
_PAGE_CACHE_MAX = 50
_PAGES = {}               # token -> {'html':…, 'done':bool}  异步生成的页面


def _page_key(content, extra, cache_key=None):
    # 键优先用「问题 + 业务日期 + 范围」：模型每次写的 SQL 与措辞会有出入，
    # 拿答案文字当键几乎永远不命中；而同一业务日、同一个问题，页面本来就该是同一张。
    if cache_key:
        return hashlib.md5(cache_key.encode('utf-8')).hexdigest()
    return hashlib.md5((content + '\x00' + (extra or '')).encode('utf-8')).hexdigest()


def page_get(token):
    """异步取页面：{'done':False} 表示还在生成。"""
    return _PAGES.get(token)


def render_page_async(content, extra='', model=None, max_tokens=6000, cache_key=None):
    """后台线程生成页面，立刻返回 token。取用见 page_get()。

    ★ 2026-10-19 起**整体下线**（Fred：「正常流程先把页面生成关了，这个工具先注释」）：
      config.PAGE_ON 默认已改为 0，这里再兜一道 —— 就算有人把开关打开也生成不出来。
      要恢复：删掉下面三行 + 把 config.PAGE_ON 的默认值改回 '1'。
    """
    return ''                                   # ← 页面生成已注释下线，见上面说明
    token = uuid.uuid4().hex
    _PAGES[token] = {'done': False, 'html': ''}

    def work():
        try:
            _PAGES[token] = {'done': True,
                             'html': render_page(content, extra, model, max_tokens, cache_key)}
        except Exception as e:                      # noqa: BLE001
            _PAGES[token] = {'done': True, 'html': '', 'error': str(e)[:200]}

    threading.Thread(target=work, daemon=True, name='page-%s' % token[:6]).start()
    return token


def render_page(content, extra='', model=None, max_tokens=6000, cache_key=None):
    """分析结论 -> 单页 HTML 数据大屏。**独立的一次模型调用，与作答那次不共用。**

    它是附属产物：任何异常都只返回空串，绝不能因为它把回答搞丢。

    ★ 2026-10-19 起整体下线（同 render_page_async）：直接返回空串，不再调模型。
    """
    return ''                                   # ← 页面生成已注释下线
    text = (content or '').strip()
    if not text or '澄清话术' in text:
        return ''
    key = _page_key(text, extra, cache_key)
    if key in _PAGE_CACHE:            # 同样的答案+同样的数据 = 同一张页面，不必再花一次模型调用
        return _PAGE_CACHE[key]
    if extra:
        text += ('\n\n【这次查到的原始数据（可直接用来作图，数字不要改、不要新增）】\n' + extra)
    out = ''
    try:
        # ★ 开思考时 reasoning 会吃掉 completion token，**正文可能是空的**（实测同一批里 3/7 轮页面
        #   就这样白白丢掉）。空正文就换 thinking 再来一次 —— 与 _final_answer 同一套路。
        for think in (True, False):
            resp = _chat([{'role': 'system', 'content': PAGE_SYSTEM},
                          {'role': 'user', 'content': text}],
                         model or config.PAGE_MODEL, use_tools=False, max_tokens=max_tokens, think=think)
            out = (resp['choices'][0]['message'].get('content') or '').strip()
            if out:
                break
            print('[agent] 页面模型返回空正文（thinking=%s），换一种再来一次' % think, flush=True)
    except Exception as e:
        print('[agent] 分析页面渲染失败（不影响回答）：%s: %s' % (type(e).__name__, e), flush=True)
        return ''
    html = _strip_html(out)
    # 薄页面重试一次：实测有 11% 的页面不到 1.5 KB（只有一两行结论，图/表都没画），
    # 与「空正文」同一类问题 —— 明确告诉它要几个板块就有，再要一次。
    if 0 < len(html) < 1200:
        try:
            _r2 = _chat([{'role': 'system', 'content': PAGE_SYSTEM},
                         {'role': 'user', 'content': text +
                          '\n\n【这次生成的页面太单薄（不到 1.5KB）】请至少给出 4 个板块：'
                          '① 一句话结论卡；② 3–5 个关键数字卡；③ 一张按对象排序的明细表（没有数据就不放图）；'
                          '④ 一句下一步动作。数字只能来自上面给的数据。'}],
                        model or config.PAGE_MODEL, use_tools=False, max_tokens=max_tokens, think=False)
            _out2 = (_r2['choices'][0]['message'].get('content') or '').strip()
            _h2 = _strip_html(_out2)
            if len(_h2) > len(html):
                print('[agent] 页面偏薄（%d 字节），重生成后 %d 字节' % (len(html), len(_h2)), flush=True)
                html = _h2
        except Exception as e:                              # noqa: BLE001
            print('[agent] 薄页面重生成失败（不影响回答）：%s: %s' % (type(e).__name__, e), flush=True)
    if not html:                                # 页面失败必须留痕：以前只写「未生成」，查不出原因
        print('[agent] 页面模型没给出 HTML（%d 字，前 120 字）：%s' % (len(out), out[:120].replace('\n', ' ')), flush=True)
    if html:
        if len(_PAGE_CACHE) >= _PAGE_CACHE_MAX:
            _PAGE_CACHE.clear()
        _PAGE_CACHE[key] = html
    return html


def _summary_worth_it(session_id):
    """这场会话够不够长，值不值得把【本场会话摘要】注进提示。

    为什么需要这道闸：`HISTORY_TURNS` 默认 5，**短会话的 history 本来就把全场带上了** ——
    再注入一段摘要纯属重复。实测在短会话里，摘要和【对话历史】的内容几乎完全重叠。
    阈值由 `config.SUMMARY_MIN_TURNS` 控制（默认 6，即"比 history 能覆盖的还多"才注入）。

    返回 (是否注入, 不注入的原因)；统计失败时**倾向于注入**（宁可多带一点记忆，
    也不要因为一次计数失败就把记忆丢掉）。
    """
    floor = int(getattr(config, 'SUMMARY_MIN_TURNS', 0) or 0)
    if floor <= 0:
        return True, ''
    try:
        from app.secretary import qa_log
        n = qa_log.count_turns(session_id)
    except Exception as e:                          # noqa: BLE001
        print('[agent] 统计会话轮数失败（按"该注入"继续）：%s: %s'
              % (type(e).__name__, e), flush=True)
        return True, ''
    if n < floor:
        return False, '会话共 %d 轮 < SUMMARY_MIN_TURNS=%d' % (n, floor)
    return True, ''


def ask(question, model=None, max_steps=None, profile=None, want_page=False, today=None, scope=None,
        page_async=False,
        session_id=None, user_id=None, user_code=None, client_ip=None, channel=None,
        history_turns=None,
        doc_context=None, doc_files=None, persona=None):
    """question = 用户的原话（不需要上游预处理）。

    本服务依次做：**载入多轮上下文** → 字面纠错与名称归一 → 时间与地点补全 →
    问题补全 → 模型循环查数作答 → 落库（问答/会话/消息/执行明细写进 agent_data）。

    profile = 用户画像（可选）：补全阶段不使用；与补全后的问题一起交给模型拆解任务。

    session_id / user_id / user_code / client_ip / channel = 记录用（可选）：
      - session_id 不传则服务端生成一个（UUID），无论落库成败都会回在返回值里
      - user_code 传业务用户ID（如 t_user.uid），落库时换成内部主键
      - **落库失败绝不影响问答** —— 只打日志

    多轮上下文：
      - 传了 session_id 就会自动读同一场会话最近的 N 轮（N = history_turns
        或 config.HISTORY_TURNS），既交给模型当上下文，也用于「本轮没说时间/地点时沿用上一轮」
      - history_turns=0 可单次关掉上下文；SECRETARY_HISTORY_TURNS=0 全局关掉
    """
    model = model or config.MODEL
    max_steps = max_steps or config.MAX_STEPS
    t_all = time.time()
    trace = []
    raw_question = question
    # 上游（业务系统）指定的数据范围：提问人是谁、在哪个页面只有上游知道，所以它**强制**。
    # 上游强制的数据范围（业务系统传的，只记录、不再注入提示词）
    # （曾经的 bug：传了范围却全按「全市」作答，证据 output/test_results/…/EV-0003）。
    force_scope = (scope or '').strip() or None
    # 检索范围**只走 kb_adapter 的线程上下文**（路由层 set_scope 登记）。
    # 2026-10-19 曾在这里接 kb_ids 形参兜底兼容，2026-10-20 cce1d3e 改为只走上下文；
    # 2026-10-21 合流时形参又被带回来，现已删除（两条路并存是冗余，且测试按原设计断言）。
    from app.secretary import preprocess as _pre
    from app.secretary import confirm
    # ── ① 要不要走预处理（纠错 + 补全 + 补时间地点）—— 2026-11-03 定稿 ──
    #    判据在页面框 `intent`（Fred 自己写、随时改）。只认「不走」这个词；
    #    框空 / 判不出 / 调用失败 → 一律走（多补一句的代价，远小于该补没补）。
    #    判断要看"最近几轮"，所以历史必须先载入（原来在补全之后，已前移）。
    turns = config.HISTORY_TURNS if history_turns is None else max(0, int(history_turns))
    history = []
    if turns and session_id:
        try:
            from app.secretary import qa_log
            history = qa_log.load_context(session_id, limit=turns)
        except Exception as e:                      # noqa: BLE001
            print('[agent] 读取多轮上下文失败（按无上下文继续）：%s: %s'
                  % (type(e).__name__, e), flush=True)
    # ★ 2026-11-03：`prev_scope`（上一轮的时间地点）**不再需要** —— 时间地点补全整块删除，
    #   沿用与否由第 6 步「问题确定」按上下文判断。
    # ── ① 分流（2026-11-05）：三个是/否 → 第 7 步按它给工具子集 ────────────
    #    查库 / 读文件 / 出文件。物理规则：本轮带了文件 → 读文件一律为是。
    _route = _pre.route(question, history, has_files=bool(doc_files),
                        ask=lambda _s, _u: _ask_short(_s, _u, model))
    tools_file.set_ctx(user_id=user_id,
                       out_dir='data/agent_out/%s' % (session_id or 'anon'))
    trace.append({'seq': len(trace) + 1, 'kind': 'intent', 'tool': 'intent',
                  'result': {'use_db': _route['use_db'], 'use_file': _route['use_file'],
                             'make_file': _route['make_file'], 'reason': _route['reason'],
                             'history_turns': len(history)}})
    # 只有「要查库」才进 2–6 预处理块；聊天和文件处理直接跳到第 7 步。
    allow_refine = bool(_route['use_db'])    # ⓪ 字面纠错 + 名称归一（阶段①的「字面」部分，在问题补全之前）。
    #    必须先做：字没改对，后面检索知识库、补全、写 SQL 全是白费。
    #    ★ 闸放行之外还有一条路：**问题里有业务对象名称**（「两水」「环潭」这种光杆所名）
    #      也算业务问题 —— 「两水」两个字里一个业务词都没有，光看词表会被判成闲聊
    #      （线上 2026-11-02 实测：问「两水」不补全、直接当通用问题答）。
    t_fix = time.time()
    fixed = name_fix.fix(question)
    # ★ 2026-11-03 定稿：名字改对了也**不再**把"不走"翻成"走" —— 走不走只由第 1 步定。
    if not allow_refine:
        fixed = {'used': False, 'text': question, 'word_fixes': [], 'name_fixes': [],
                 'names': [], 'areas': [], 'hint': '', 'unresolved': [], 'ms': 0}
    if fixed['used']:
        question = fixed['text']
    # 2026-11-04：改没改都记一条，带 used —— 否则页面上看不出这一步跑没跑、动不动手。
    trace.append({'seq': len(trace) + 1, 'kind': 'namefix', 'tool': 'name_fix',
                  'ms': fixed['ms'],
                  'args': {'input': raw_question},
                  'result': {'used': bool(fixed['used']),
                             'fixed': fixed['text'] if fixed['used'] else '',
                             'word_fixes': fixed['word_fixes'],
                             'name_fixes': fixed['name_fixes'],
                             'names': fixed['names'],
                             'areas': fixed['areas'],
                             'unresolved': fixed.get('unresolved', []),
                             'hint': fixed['hint']}})
    namefix_ms = int((time.time() - t_fix) * 1000)
    # ⓪·3 补全（2026-11-01）：只有像业务问题才补 —— 只说了对象没问啥，补默认问法。
    comp = {'changed': False, 'hits': []}
    if allow_refine:
        try:
            comp = _pre.complete(question) or comp
            if comp.get('changed'):
                question = comp['text']
                trace.append({'seq': len(trace) + 1, 'kind': 'complete', 'tool': 'complete',
                              'result': {'before': raw_question, 'text': question,
                                         'hits': comp.get('hits') or []}})
        except Exception as _exc:                  # noqa: BLE001 - 补全失败按原话继续
            print('[agent] 补全失败（按原话继续）：%s' % str(_exc)[:120], flush=True)
    # ⓪·4 多轮上下文已在第 1 步之前载入（判断要看最近几轮），这里只补 L2 会话摘要。
    # ⓪·4b L2 会话摘要：更早轮次的浓缩，让「翻出旧会话继续聊」时不至于只知道最近几轮。
    #       ★ 它**只用于注入提示**，不参与下面的「沿用上一轮口径」——
    #         那条链路必须能看到**紧邻的上一轮**，哪怕它已经被摘要覆盖了
    #         （否则一轮被摘要进去之后，时间/地点继承就会突然失效）。
    #       ★ 两道闸（2026-09-22 加，都是踩过的坑）：
    #         ① `history_turns=0` 表示"本轮完全不要记忆" —— 摘要**不能绕过这个开关溜进来**。
    #            之前是个 bug：读摘要是无条件执行的，history 关了摘要照进。
    #         ② 会话太短（轮数 < SUMMARY_MIN_TURNS）时 history 已覆盖全场，注入纯属重复。
    sess_sum = {'summary': '', 'upto_seq': 0}
    sum_skip = ''
    if not turns:
        sum_skip = 'history_turns=0（本轮不要记忆）'
    elif not session_id:
        sum_skip = '未传 session_id'
    else:
        _worth, _why = _summary_worth_it(session_id)
        if not _worth:
            sum_skip = _why
        else:
            try:
                from app.secretary import session_summary
                sess_sum = session_summary.get(session_id)
            except Exception as e:                  # noqa: BLE001
                print('[agent] 读取会话摘要失败（按无摘要继续）：%s: %s'
                      % (type(e).__name__, e), flush=True)
                sum_skip = '读取摘要出错'
    sum_upto = int(sess_sum.get('upto_seq') or 0)
    # ★ 2026-11-03（Fred）：**时间地点补全整块删除** —— 不由代码换算、不补默认年月和全市。
    #   要补什么由 Fred 在「补全表」里自己配（第 4 步命中后交给第 6 步判断）。
    #   这里只留一个空骨架，让 result['scope'] / trace 的结构对内网接口保持兼容。
    scope = {'question': question,
             'time': {'matched': '', 'time_text': '', 'period_type': '', 'period_key': '',
                      'is_default': False, 'note': ''},
             'place': '', 'place_default': False,
             'inherited': {'time': False, 'place': False},
             'scope_text': '', 'place_confirm': False}
    trace.append({'seq': len(trace) + 1, 'kind': 'scope', 'tool': 'scope_removed',
                  'result': {'skipped': True,
                             'reason': '时间地点补全已删除，改由补全表配置'}})
    # **不预检索、不预注入判据**：按问题检索判据挑"该用哪条标准"实测不稳（会把相邻技能的标准当成本题的标准，
    # 例如把「项目执行进度异常」当成「审批积压」）。判据改成让模型在循环里用 kb_search 工具自己查 ——
    # 需要判断就去查，查不到就不下判断（见回答契约第 7 条）。
    # 问题不改写：原话 + 纠错 + 补问法 + 补全表命中，一路往下传。
    t_loop = time.time()
    # ── ⑥ 问题确定：把前面几步揉成一份定稿，第 7 步只拿这份 ────────────────
    #    判据在页面框 `confirm`（空 = 不归整，参考资料原样下传）。
    #    ★ 2026-11-03 定稿：主循环不再吃「原话 + 最近几轮 + 【统计范围】」那一堆 ——
    #      取舍只在这一处，改一处就生效。第 1 步判"不走"时（闲聊 / 明确要求）
    #      原话直接进第 7 步，缺信息由它自己反问用户。
    if allow_refine:
        _conf = confirm.run(question, fixed, comp, scope, history,
                            ask=lambda _s, _u: _ask_short(_s, _u, model))
    else:
        _conf = {'used': False, 'text': '', 'block': ''}
    trace.append({'seq': len(trace) + 1, 'kind': 'confirm', 'tool': 'confirm',
                  'result': {'used': bool(_conf.get('used')),
                             'passed': 'model' if _conf.get('used') else 'raw',
                             'text': str(_conf.get('text') or '')[:800]}})
    if _conf.get('used'):
        user_content = ('【本轮要办的事】（第 6 步归整后的定稿，按它办）\n' + _conf['text'])
    elif _conf.get('block'):
        user_content = _conf['block']
    else:
        user_content = question
    # 只给一行日期：第 6 步框空着时，"上个月"要靠它自己推。这是信息，不是纪律。
    user_content += '\n\n【今天】%s' % (today or time.strftime('%Y-%m-%d'))
    # 摘要与最近几轮**不再拼进主循环**（第 6 步已经把该用的信息归整进定稿了）。
    # hist_detail 仍要算：落库结果里的 history_injected 靠它，排障要看。
    keep = max(0, int(getattr(config, 'SUMMARY_KEEP_RECENT', 2) or 0))
    always = history[-keep:] if (history and keep) else []
    hist_detail = [h for h in history
                   if int(h.get('seq_no') or 0) > sum_upto or h in always]
    # ⓪·6 规则：**目录常驻 + 按需精确取**（2026-10-30 改）
    #     原来是"拿问题去向量检索《判据规则》切片，只留 4 片"，线上实测那 4 片的相似度
    #     只有 0.17~0.19 —— 是不是该用的那条规则全看运气，而且还会把相邻题的标准套错。
    #     现在：system 里常驻「规则目录」（主题+一句话），要判定时模型自己 get_rule(主题)。
    rule_hits, rule_best, rule_ok, rule_query = [], 0.0, True, question
    rule_topics = []
    if config.RULES_ON:
        _t_rule = time.time()
        try:
            from app.secretary import rule_doc as _rule_doc
            rule_topics = _rule_doc.topics()
        except Exception as _exc:                  # noqa: BLE001 - 规则读不到不影响问答
            print('[agent] 规则目录读取失败（按无规则继续）：%s: %s'
                  % (type(_exc).__name__, str(_exc)[:120]), flush=True)
            rule_topics = []
        trace.append({'seq': len(trace) + 1, 'kind': 'rules', 'tool': 'rule_menu',
                      'ms': int((time.time() - _t_rule) * 1000),
                      'args': {'source': 'document.doc_type=rule'},
                      'result': {'topics': len(rule_topics),
                                 'titles': [x['topic'][:60] for x in rule_topics[:8]]}})

    # ★ 2026-11-03 定稿：【统计范围】守卫段和【名称归一说明】都不再注入。
    #   时间 / 范围由第 6 步写进定稿（第 5 步算好的结果在那里），改一处就生效；
    #   名称改成什么，在定稿的"更正后"一行里已经写清楚了。
    # ★ 2026-11-03（Fred）：COMMON（汇报口吻/分层）与 CHECK（交卷前 5 条）已下线，
    #   不再往 system 或 user 消息里注入。`_is_analysis` 只用于 qtype 落库，不再切换提示词。
    _is_analysis = _wants_analysis(question)
    # ★ 2026-10-30：COMMON/CHECK 从**用户消息**搬进 **system**（见下方 agent_v2.system 调用）。
    #   它们是"每次都适用的纪律"，性质与红线相同；塞在用户消息里等于每轮都当用户输入，
    #   而且和 system 里的通用规则分成两处、谁也不管谁。
    # ★ 规则不再预注入切片：目录在 system（S6），要判定就让模型 get_rule 取那一条。
    # ★ 2026-11-03：原来这里塞了一段「判断标准从哪来…先用 get_rule 取规则」。
    #   规则材料已从链路删除，这句话只会让模型去取一个不存在的东西 —— 去掉。
    if profile:
        user_content += ('\n\n【提问人身份与数据范围】（判断标准与关注重点按它来，不要因此扩大问题范围）：\n' + profile)
    # ⓪·7 用户上传的文件（2026-10-19 加）：这是本轮回答的**事实来源**。
    #      省时间的使用场景就是"读 20 页文件" —— 所以纪律要写死在这里：
    #      文件里没有的就写「文件中未提及」，**不许用外部常识或业务库补**（领导要的是文件说了什么）。
    if doc_files:
        # ★ 2026-11-05：不再把文件正文/节选灌进 user 消息（长文只给 8 段，第 9 页往后看不见）。
        #   只给清单 + file_id，正文由模型自己 read_file 分页读。
        _fl = []
        for _i, _f in enumerate(doc_files, start=1):
            _fl.append('【文件%d】《%s》 约 %s 字，%s，file_id=%s'
                       % (_i, _f.get('name'), _f.get('chars'), _f.get('mode') or '',
                          _f.get('file_id')))
            _secs = _f.get('cited_sections') or []
            if _secs:
                _fl.append('  目录：' + ' / '.join(str(s) for s in _secs[:12]))
        user_content += (
            '\n\n【本轮带来的文件】（正文用 read_file(file_id) 分页读；读完再用，不要凭印象）\n'
            + '\n'.join(_fl) +
            '\n\n纪律：只依据读到的文件内容回答；文件里没有的写「文件中未提及」，不许用常识或业务库补。')
    elif doc_context:
        user_content += (
            '\n\n【用户上传的文件】（本轮回答的**事实来源**，优先级高于知识库与业务库）\n'
            + doc_context +
            '\n\n关于这些文件的四条纪律：'
            '\n① 只依据上面的文件内容回答；文件里没有的，明确写「文件中未提及」，'
            '**不许用外部常识、经验或业务库数据补**。'
            '\n② 回答里出现的时限、金额、比例、责任单位，必须能在文件里找到原句；'
            '引用时用「（见：章节路径）」标出位置。'
            '\n③ 如果给的是节选（上面标了「全文约 N 字，节选 M/K 段」），'
            '不要声称"全文只有这些"；需要别的位置就说"需要再查"并说明查哪一节。'
            '\n④ 本次不需要查业务库；只有用户明确要求"和系统里的数据比对"时才调 run_sql。')
    # 上游强制范围不在这里再注入一遍：需要它的时候它已经在第 6 步的定稿里。
    # ★ 2026-11-03 定稿：不再用 `modes.judge` 那套词表判"这题属于哪种模式"来开关工具 ——
    #   工具一律给（该查数还是该写文档由模型自己判断），模式固定 general（基础流程那段）。
    #   这是链路里最后一张写死的业务词表，删掉之后不再有词表匹配。
    _mode = 'general'
    _persona = persona
    if _persona is None and session_id:
        try:
            from app.secretary import persona as _persona_mod
            _persona = (_persona_mod.get_persona(session_id) or {}).get('prompt')
        except Exception:
            _persona = None
    _prefs = None
    if user_id:
        try:
            from app.secretary import prefs as _prefs_mod
            _prefs = _prefs_mod.get(user_id) or None
        except Exception:
            _prefs = None
    # 个人规则（本人资料库里 doc_type='rule' 的文档）：每轮注入，限量 1500 字
    _pkb = {'text': '', 'files': [], 'chars': 0, 'overflow': ''}
    if user_id:
        try:
            from app.secretary import personal_kb as _pkb_mod
            _pkb = _pkb_mod.personal_rules(user_id) or _pkb
        except Exception as _exc:                  # noqa: BLE001 - 个人规则读不到不影响问答
            print('[agent] 个人规则读取失败（忽略）：%s: %s'
                  % (type(_exc).__name__, str(_exc)[:120]), flush=True)
    try:
        from app.secretary import prompt_text as _pt
        _priority = _pt.get('priority', '') or ''
        # 工具说明：页面给了用页面的，没给用默认段（2026-11-05）
        _tool_guide = _pt.get('tools', '') or _pt.DEFAULT_TOOLS_GUIDE
    except Exception:                              # noqa: BLE001
        _priority = ''
        _tool_guide = ''
    messages = [{'role': 'system',
                 'content': agent_v2.system(
                     _mode, _system, today,
                     persona=_persona, prefs=_prefs,
                     # S1「谁说了算」：2026-11-03 起从页面取（prompt_text.priority），
                     # 没填就是空 —— 空壳里不再有代码自带的优先级声明。
                     priority=_priority,
                     # ★ 2026-11-03（Fred）：S4「作答格式」（汇报口吻 + 交卷检查）已下线，
                     #   不再注入。原文备份见 docs/08-历史归档/2026-11-03-删除的提示词规则/。
                     answer_format='',
                     # S7 我的规则（个人库 type=rule）
                     personal_rules=(_pkb.get('text') or ''),
                     # 查数口径只在「要查业务库」时注入（2026-11-05）
                     with_rules=bool(_route['use_db']),
                     # 工具说明：给工具时才给（聊天 0 只手，给了只会诱发乱调）
                     tool_guide=(_tool_guide if (_route['use_db'] or _route['use_file']
                                                 or _route['make_file']) else ''))},
                {'role': 'user', 'content': user_content}]
    # 装配结果记一条：system 多少字、带没带查数口径（测试与排障要看这个）
    trace.append({'seq': len(trace) + 1, 'kind': 'system', 'tool': 'system',
                  'result': {'chars': len(messages[0]['content']),
                             'has_caliber': '===== 规则材料' in messages[0]['content']}})
    answer = ''
    # ── 第 7 步装配（2026-11-05）：按三个布尔给工具子集 ──────────────────
    # 规则：有 file_id → read_file 必给；选了知识库 → kb_search 必给；
    #      查数口径与查数手只在 use_db 时给；改库的手还没接（P2）。
    _want = []
    if _route['use_db']:
        _want += ['kb_search', 'describe_table', 'find_column', 'run_sql', 'finish']
    if _route['use_file']:
        _want += ['kb_search', 'kb_read', 'read_file', 'run_code']
    if _route['make_file']:
        _want += ['kb_search', 'make_file', 'run_code']
    _want = list(dict.fromkeys(_want))
    tools = [t for t in TOOLS if t['function']['name'] in _want]
    trace.append({'seq': len(trace) + 1, 'kind': 'tools', 'tool': 'toolset',
                  'result': {'tools': [t['function']['name'] for t in tools],
                             'use_db': _route['use_db'], 'use_file': _route['use_file'],
                             'make_file': _route['make_file']}})
    # 第 8 步材料：按本轮动作拼（查数→作答规则；读文件→清单规则；出文件→产物规则）
    _slots = []
    if _route['use_db'] or not (_route['use_file'] or _route['make_file']):
        _slots.append('answer')
    if _route['use_file']:
        _slots.append('list')
    if _route['make_file']:
        _slots.append('file')
    _slots = tuple(_slots)
    # ★ 2026-11-03 定稿「守卫先全关」：不再有"催一次""锁死"两种处置。
    #   模型没调工具也一样放行；出问题再按现象一个个开回来（旧逻辑见 git 历史）。
    blocked = False
    gate_log: list = []     # 保留字段（落库结果里内网在用），本轮恒为空
    step = 0
    facts_text = ''
    while True:
        step += 1
        _over_step = bool(max_steps) and step > max_steps
        _budget = float(getattr(config, 'QUESTION_BUDGET_S', 0) or 0)
        _over_time = _budget > 0 and (time.time() - t_all) > _budget
        if _over_step or _over_time:
            # 到顶不再空转：关掉工具直接要正文。原来回一句「已超过最大步骤」等于把这题废了。
            # 时间预算同理：实测「没给对象」的问题（「这单钱都花哪了」）会一直翻表到客户端 600 s 超时，
            # 什么都拿不到；宁可给一份「已查到什么 + 还缺什么」的回答。
            _why = ('已到查询步数上限 %d' % max_steps) if _over_step else ('已到单题时间预算 %.0f 秒' % config.QUESTION_BUDGET_S)
            answer = _final_answer(
                _swap_materials(
                    (list(messages) + [{'role': 'user', 'content':
                                        '时间/步数预算到了，请**用已查到的数据**给出结论；'
                                        '如果还缺关键前提（例如具体是哪张单、哪个所），'
                                        '就在回答里一句话说清需要补什么，不要再查。'}]
                     if _over_time else messages),
                    ('answer',)),
                    model, allow_rule=True, trace=trace)
            trace.append({'seq': len(trace) + 1, 'kind': 'final', 'content': _why + '，强制出正文'})
            break
        t0 = time.time()
        # 第 7 步 · 干活循环。工具按分流子集给；没给工具就直接要正文。
        # 查数轮关思考：实测同一轮开思考会写 240~3400 字过程散文（一轮 10~17s）。
        # 出文件那轮要给足输出额度 —— make_file 的 spec 是整份内容，600 token 会被截断成空参数。
        _mt = 4000 if 'make_file' in _want else 600
        resp = _chat(messages, model, think=False, max_tokens=_mt,
                     use_tools=bool(tools), tools=tools or None)
        llm_ms = int((time.time() - t0) * 1000)
        msg = resp['choices'][0]['message']
        calls = msg.get('tool_calls') or []
        if not calls:
            # 模型不再发工具了 = 它认为信息够了（也可能它要反问用户，答案就是那句反问）。
            # ★ 2026-11-03 定稿「守卫先全关」：不催、不锁、不判"有没有出处"，直接进第 8 步。
            answer = (msg.get('content') or '').strip()
            used_tool = any(t.get('kind') == 'tool' for t in trace)
            if not used_tool:
                if not answer:
                    # 空正文：另起一次要正文。不许把空白交给用户。
                    answer = _final_answer(_swap_materials(messages, ('answer',)), model,
                                           trace=trace)
                    llm_ms = int((time.time() - t0) * 1000)
                trace.append({'seq': len(trace) + 1, 'kind': 'answer', 'model_ms': llm_ms,
                              'content': answer[:600], 'note': '未查业务库'})
                break
            # 查过了：先落事实清单，再只拿清单写正文（不让它继续查，也不让过程话术当答案）。
            _wa = _write_answer(messages, model, user_content, trace=trace,
                                need_facts=_route['use_db'], slots=_slots)
            answer = _wa['answer']
            facts_text = _wa['facts']
            llm_ms = int((time.time() - t0) * 1000)
            trace.append({'seq': len(trace) + 1, 'kind': 'answer', 'model_ms': llm_ms,
                          'content': answer[:600]})
            break
        keep = {'role': 'assistant', 'content': msg.get('content') or '', 'tool_calls': calls}
        messages.append(keep)
        # 不设步数上限，改用周期性软提醒，防止一直翻表不收手
        # ★ 2026-11-03：删掉每 2 轮一句的「够了就收手」催促 —— 那是代码替模型管节奏。
        # finish 是收尾信号：同轮里真实查询先跑完，再另起一次「写汇报」调用
        fins = [c for c in calls if ((c.get('function') or {}).get('name')) == 'finish']
        live = [c for c in calls if ((c.get('function') or {}).get('name')) != 'finish']
        outs = _exec_calls(live) if live else []
        # 每轮一行日志：单题跑超 10 分钟时，只有这里能看出是「轮次太多」还是「某次调用卡住」
        # （实测场景 17 第 1 问、场景 22 前两问都出现过 600 s 无响应，日志里什么都没有）。
        print('[agent] 第 %d 轮：工具 %d 次，本轮模型 %d ms，累计 %.1f s'
              % (step, len(live), llm_ms, time.time() - t_all), flush=True)
        # ⚠️ 按**原始 calls 顺序**逐个回填 tool 消息（不只是 live）：
        #    OpenAI 兼容协议要求每个 tool_call_id 都必须有对应 tool 消息。
        #    finish 是控制信号，没有真实结果，但同样要回一条，否则严格网关（deepseek-v4.1）
        #    会报 "insufficient tool messages following tool_calls message"（qwen 宽容，掩盖了这个 bug）。
        _out_by_id = {tc.get('id'): o for tc, o in zip(live, outs)}
        for tc in calls:
            if ((tc.get('function') or {}).get('name')) == 'finish':
                messages.append({'role': 'tool', 'tool_call_id': tc.get('id'),
                                 'content': '{"ok":true,"note":"已收到收尾信号"}'})
                continue
            o = _out_by_id.get(tc.get('id'))
            if o is None:
                continue
            trace.append({'seq': len(trace) + 1, 'kind': 'tool', 'tool': o['name'], 'args': o['args'],
                          'ms': o['ms'], 'model_ms': llm_ms, 'round': step, 'result': o['result']})
            _body = _trim(o['name'], o['result'])
            if o['name'] == 'run_sql':
                _sql = (o.get('args') or {}).get('sql')
                # ★ 2026-11-03 定稿：三条"就地提醒"（没带时间 / 没带范围 / 没带单位）全部停用。
                #   它们是预设式守卫；需要约束就把规矩写进页面的「查数口径」。
                #   旧代码保留：_time_guard / _scope_guard / _unit_guard。
                pass
            messages.append({'role': 'tool', 'tool_call_id': tc['id'], 'content': _body})
        # 周期性软提醒：防止一直翻表不收手。
        # ⚠️ 必须放在**全部 tool 回填之后**：OpenAI 兼容协议要求 assistant(tool_calls) 后
        #    紧跟每个 tool_call_id 的 tool 消息。原来插在两者之间，qwen 能容忍，
        #    deepseek-v4.1 直接 400（"insufficient tool messages following tool_calls message"）。
        # ★ 2026-11-03：这里原来每 2 轮塞一句「对照【各类问题的必答项】逐项点一遍」——
        #   必答项随规则一起删了，这句在指空；代码也不再替模型管节奏。
        if fins:
            # 模型自己说「数据够了」→ 先落事实清单，再由系统按清单写正文
            _wa = _write_answer(messages, model, user_content, trace=trace,
                                need_facts=_route['use_db'], slots=_slots)
            answer = _wa['answer']
            facts_text = _wa['facts']
            llm_ms = int((time.time() - t0) * 1000)
            trace.append({'seq': len(trace) + 1, 'kind': 'answer', 'model_ms': llm_ms,
                          'content': answer[:600], 'note': '模型调用 finish，已另起一次写汇报'})
            break
    # 输出清洗：把模型漏进正文的 Markdown 符号、过程话术、英文思考句洗掉
    # （实测 dsv4 禁不掉，只能代码层兜）。**必须在 qtype 与页面之前** —— 页面是从 answer 生成的，
    # 洗晚了页面里还是脏的。
    answer = _clean_answer(answer)
    # 简洁闸（2026-11-05）：问数轮按「一两句话汇报」的规则落地 —— 超长就压一次。
    # 规则在页面上（answer 材料），但实测模型会无视长度要求，这里兜一道。
    if _route['use_db'] and len(re.sub(r'\s', '', answer or '')) > 200:
        _bmsgs = [{'role': 'system', 'content': _answer_system(messages, _slots)},
                  {'role': 'assistant', 'content': answer},
                  {'role': 'user', 'content':
                   '太长了。按汇报格式压到 120 字以内：先用一句话给结论（带关键数字），'
                   '最多再补一句提醒；不要分节、不要编号、不要复述口径。只输出正文。'}]
        try:
            _short = _final_answer(_bmsgs, model, max_tokens=400, trace=None)
        except Exception:                                  # noqa: BLE001
            _short = ''
        if _short and len(re.sub(r'\s', '', _short)) < len(re.sub(r'\s', '', answer)):
            answer = _clean_answer(_short)
            trace.append({'seq': len(trace) + 1, 'kind': 'brevity_guard',
                          'result': {'kept': 'rewrite', 'chars': len(answer)}})
    # 数字闸（对账）：回答里的金额必须能在**本轮** SQL 结果或其确定性派生里找到出处。
    # 找不到就带着清单重问一次；第二次仍对不上就保留「错得更少」的那版，并把对账结果记进 trace。
    _queried = any(isinstance(t, dict) and t.get('tool') == 'run_sql' for t in trace)
    if config.NUMBER_GUARD_ON and _queried and not blocked:
        # ★ 2026-11-04：有事实清单就以清单为准（第 8 步只看清单，重写也不许把工具原文塞回来）。
        _pool = _facts_pool(facts_text) if facts_text else _num_pool(trace)
        # ★ 2026-11-03：上一轮查到的金额也算有出处（否则复用历史结论会被逼着重写）。
        for _h in (history or []):
            for _m in _MONEY_RE.finditer(str(_h.get('answer') or '')):
                try:
                    _x = float(_m.group(1))
                except (TypeError, ValueError):
                    continue
                _pool.add(round(_x, 2))
                _pool.add(round(_x / 10000.0, 2))
        _bad1 = _untraceable_money(answer, _pool)
        if _bad1:
            if facts_text:
                _base = [{'role': 'system', 'content': _answer_system(messages)},
                         {'role': 'user', 'content': user_content},
                         {'role': 'user',
                          'content': '【这一轮查到的事实（数字只用这些）】\n' + facts_text}]
            else:
                _base = list(messages)
            _msgs = _base + [
                {'role': 'assistant', 'content': answer},
                {'role': 'user', 'content':
                 '对账不通过：回答里这些金额在你本轮查询结果里找不到出处 —— %s 万元。'
                 '请只使用上面事实清单里的数，逐条改正后重写正文；'
                 '不要改口径、不要新增没查过的数，也不要提这条提示。' % '、'.join(str(x) for x in _bad1[:8])}]
            try:
                _ans2 = _final_answer(_swap_materials(_msgs, ('answer',)), model,
                                      allow_rule=True, trace=trace)
            except Exception:                                  # noqa: BLE001
                _ans2 = ''
            _bad2 = _untraceable_money(_ans2, _pool)
            if _ans2 and len(_bad2) < len(_bad1):
                answer = _ans2
            trace.append({'seq': len(trace) + 1, 'kind': 'number_guard',
                          'before': _bad1[:8], 'after': _bad2[:8],
                          'kept': 'rewrite' if _ans2 and len(_bad2) < len(_bad1) else 'original'})
    # 内部系数闸：把 0.6 达标系数换算成「XX% 基准线 / 进度要求」写给人看是明令禁止的。
    # 与数字闸同一套路：点出原句、重写一次；第二次还有就保留更干净的那版。
    if config.NUMBER_GUARD_ON and answer:
        _coef1 = _coefficient_talk(answer)
        if _coef1:
            _cmsgs = list(messages) + [
                {'role': 'assistant', 'content': answer},
                {'role': 'user', 'content':
                 '回答里出现了「%s」—— 把内部系数写成标准是禁止的。请重写：要么只给「已过时间比例」和「当前占比」两个数让人自己看，要么只说「还差多少、还需完成多少」，不要出现任何「XX%% 基准线 / 达标线 / 进度要求」。' % _coef1}],
            try:
                _cans2 = _final_answer(_swap_materials(_cmsgs, ('answer',)), model,
                                       allow_rule=True, trace=trace)
            except Exception:                                  # noqa: BLE001
                _cans2 = ''
            _coef2 = _coefficient_talk(_cans2)
            if _cans2 and not _coef2:
                answer = _cans2
            trace.append({'seq': len(trace) + 1, 'kind': 'coef_guard', 'before': _coef1,
                          'after': _coef2,
                          'kept': 'rewrite' if (_cans2 and not _coef2) else 'original'})
    # qtype：**只用来决定要不要出分析页面**（前端要页面可以直接传 want_page）。
    # 真正怎么写回答由 COMMON 里的分层纪律管，不在这里分流。
    # 这里取两个"实际发生的事"的信号，任一成立即算分析：
    #   ① 问句本身就是分析型（为什么 / 怎么样 / 够不够 / 怎么办…）；
    #   ② 循环里自己查过《规则》（kb_search）—— 说明它认定这题要按规则判。
    qtype = '分析' if (
        _is_analysis
        or any(t.get('tool') == 'kb_search' and t.get('kind') == 'tool' for t in trace)
    ) else '查数'
    loop_end = time.time()
    # 分析题的附加产物：**另一次模型调用**把结论渲染成单页 HTML 数据大屏。
    # 与作答那次不共用；同步生成、同步随本响应返回。页面失败只返回空串，回答照旧。
    page_html = ''
    page_token = ''
    if answer and config.PAGE_ON and (qtype == '分析' or want_page):
        # 页面只按「回答文字」生成时，文字里没有明细就画不出图（会出现空图区）。
        # 所以把这次 run_sql 查到的行一起给页面模型 —— 它有数据可画，才画得出。
        rows_ctx = []
        for _t in trace:
            if _t.get('kind') == 'tool' and _t.get('tool') == 'run_sql':
                _res = _t.get('result') or {}
                if _res.get('rows'):
                    rows_ctx.append({'rows': _res['rows'][:40]})
        extra = json.dumps(rows_ctx, ensure_ascii=False, default=str)[:6000] if rows_ctx else ''
        t_page = time.time()
        page_token = ''
        # 页面缓存键：同一业务日 + 同一个问题 + 同一个范围 = 同一张页面
        pkey = 'q=%s|d=%s|s=%s' % (question, today or time.strftime('%Y-%m-%d'),
                                   force_scope or '')
        if page_async:
            # 异步：先把答案返回，页面在后台生成，避免 30–55s 的页面把回答堵住。
            page_token = render_page_async(answer, extra=extra, model=model, cache_key=pkey)
            trace.append({'seq': len(trace) + 1, 'kind': 'page', 'tool': 'render_page',
                          'ms': int((time.time() - t_page) * 1000),
                          'result': {'ok': True, 'async': True, 'token': page_token,
                                     'reason': '页面转到后台生成，用 GET /api/page?token= 取'}})
        else:
            page_html = render_page(answer, extra=extra, model=model, cache_key=pkey)
            trace.append({'seq': len(trace) + 1, 'kind': 'page', 'tool': 'render_page',
                          'ms': int((time.time() - t_page) * 1000),
                          'result': {'ok': bool(page_html), 'chars': len(page_html),
                                     'reason': '已生成分析页面' if page_html else '未生成（模型未给出 HTML 或调用失败）'}})
    DB_TOOLS = ('run_sql',)
    db_calls = [t for t in trace if t.get('tool') in DB_TOOLS]
    sql_calls = [t for t in trace if t.get('tool') == 'run_sql']
    kb_calls = [t for t in trace if t.get('tool') == 'kb_search']
    result = {
        'input_question': raw_question,
        'today': today or time.strftime('%Y-%m-%d'),
        'rules_loaded': len(rule_topics),    # 规则目录里有几个主题（0 = 没配规则文档）
        # 个人层可观测（2026-10-30）：本人规则注入了没、注入了多少、超限没
        'personal': {'rules': list(_pkb.get('files') or []),
                     'rules_chars': int(_pkb.get('chars') or 0),
                     'overflow': _pkb.get('overflow') or ''},
        'fixed_question': question,
        'name_fix_used': fixed['used'],
        'scope': {
            'time': scope['time']['time_text'],
            'period_type': scope['time']['period_type'],
            'period_key': scope['time']['period_key'],
            'time_is_default': scope['time']['is_default'],
            'place': scope['place'],
            'place_is_default': scope['place_default'],
            # 这两个为 true 说明该值**不是用户这轮说的**，而是从上一轮沿用来的
            'time_inherited': scope['inherited']['time'],
            'place_inherited': scope['inherited']['place'],
            # 本轮地点没认出来 → 范围未定，排障时一眼能看到（2026-11-02）
            'place_confirm': bool(scope.get('place_confirm')),
        },
        'user_profile': profile or '',
        'completion_used': False,      # 已取消「问题改写」，这个字段保留只为兼容历史记录
        'qtype': qtype,
        'analysis_rules_hit': len([t for t in trace if t.get('tool') == 'kb_search' and t.get('kind') == 'tool']),
        'page_html': page_html,
        'page_token': page_token,        # 异步模式下用它取页面：GET /api/page?token=…
        'completed_question': question,   # 不再改写问题：就是用户原话（只做过地名/时间补全）
        'answer': answer,
        'trace': trace,
        'model': model,
        'elapsed_ms': int((time.time() - t_all) * 1000),
        'timings': {
            'namefix_ms': namefix_ms,
            'completion_ms': 0,          # 问题改写已取消；判据检索的时间在 trace 的 rules 步里
            'loop_ms': int((loop_end - t_loop) * 1000),
            'page_ms': int((time.time() - loop_end) * 1000) if page_html else 0,
            'total_ms': int((time.time() - t_all) * 1000),
        },
        'db_query_count': len(db_calls),
        'sql_query_count': len(sql_calls),
        'kb_query_count': len(kb_calls),
        'no_db_query': len(db_calls) == 0,
        'unverified': blocked,
        # 闸门留痕（2026-11-02）：为什么被拦 / 为什么放行、那一轮给没给工具。
        # 内网只看得到最终 JSON，没有这一段就分不清"模型不老实"和"没给工具"。
        'evidence_gate': gate_log,
        'steps': len(trace),
        'history_used': len(history),
        'history_injected': len(hist_detail),
        # 带文件提问时：本轮用了哪些文件、各用了多少字、有没有节选（前端据此显示"已读 2.1 万字"）
        'files': list(doc_files or []),
        'summary_used': bool(sess_sum.get('summary')),
        'summary_chars': len(sess_sum.get('summary') or ''),
        'summary_upto_seq': sum_upto,
        'summary_skip': sum_skip or None,       # 非空 = 本轮为什么"没注入"摘要（排障用）
    }
    try:
        if gaps.is_gap(answer, blocked):
            gaps.record(question, question, answer, model=model,
                        db_count=result['db_query_count'], kb_count=result['kb_query_count'],
                        unverified=blocked)
            result['recorded_as_gap'] = True
    except Exception:
        pass
    # 落库：问答/会话/消息/执行明细写进 agent_data 库。
    # **这一段的任何异常都必须吞掉** —— 记录是附属能力，不能因为它把回答搞丢。
    try:
        from app.secretary import qa_log
        rec = qa_log.save(result, session_id=session_id, user_id=user_id, user_code=user_code,
                          client_ip=client_ip, channel=channel)
        result['session_id'] = rec['session_id']
        result['qa_id'] = rec['qa_id']
    except Exception as e:                          # noqa: BLE001
        result['session_id'] = session_id
        result['qa_id'] = None
        # flush：不加的话日志重定向到文件时会被缓冲，等于没有输出
        print('[qa_log] 落库失败（不影响回答）：%s: %s' % (type(e).__name__, e), flush=True)
    # L2 会话摘要：**异步、每 N 轮才做一次**（见 config.SUMMARY_EVERY）。
    # 必须放在落库之后 —— 摘要读的就是刚写进去的这几轮。
    # 投递失败绝不影响本次回答。
    try:
        from app.secretary import session_summary
        result['summary_queued'] = session_summary.maybe_enqueue(
            result.get('session_id') or session_id)
    except Exception as e:                          # noqa: BLE001
        print('[agent] 投递摘要任务失败（不影响回答）：%s: %s' % (type(e).__name__, e), flush=True)
    return result


# ── 业务词表补充（原站点包 lexicon，2026-10-30 内联进代码）────────────
def _apply_pack_lexicon() -> None:
    try:
        from app.secretary import lexicon_defaults
        lx = (lexicon_defaults.BUSINESS_TERMS or {}).get('business_terms') or {}
    except Exception:                              # noqa: BLE001
        return
    patterns = {"station_hint": "_STATION_HINT", "biz_words": "_BIZ"}
    for key, gname in patterns.items():
        pat = (lx or {}).get(key)
        if not pat or gname not in globals():
            continue
        try:
            globals()[gname] = re.compile(pat)
        except Exception:                          # noqa: BLE001
            print("[lexicon] bad pattern, keep default:", key)


_apply_pack_lexicon()


# ── 兜底身份（2026-10-30：正文内联进 prompts_data.IDENTITY）────────────
try:
    from app.secretary import prompts_data as _pd_identity
    _lines = [ln for ln in (_pd_identity.IDENTITY or '').strip().splitlines()
              if not ln.lstrip().startswith('#')]
    _clean = '\n'.join(_lines).strip()
    if _clean:
        _FALLBACK = _clean
except Exception:                                  # noqa: BLE001
    pass

