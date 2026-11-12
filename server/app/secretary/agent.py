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
import os
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
from app.secretary import tools_result

# 闸门判定（"这句答案能不能直接交出去"）已搬到 evidence_gate.py（2026-11-02）。
# 2026-11-10：代码里最后一个业务词表 _BIZ 与它的装载钩子已删除 —— 业务词只该出现在
# 页面配置与业务字典里，不进代码。

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
        'description': '在业务字典内的表里按关键词搜，返回「表名 + 字段名/表名 + 类型 + 注释 + 中文名 + 示例值」。'
                       '同时匹配：真实列名、列注释、字典里的中文名、表名与表说明。'
                       '当你不确定某个数据在哪个字段/哪张表时先用它（直接用业务名词搜即可）。',
        'parameters': {'type': 'object', 'properties': {'keyword': {'type': 'string'}}, 'required': ['keyword']}}},
    {'type': 'function', 'function': {
        'name': 'list_tables',
        'description': '列出业务字典里登记的全部表：表名 ｜ 中文名 ｜ 一句话说明 ｜ 行数 ｜ 最近更新。'
                       '**不确定系统里有哪些表、或某张表叫什么名字时，先调它**（比瞎猜表名快）。'
                       '按关键词筛选：给了 keyword 就只回名字或说明里含这个词的表。',
        'parameters': {'type': 'object', 'properties': {
            'keyword': {'type': 'string', 'description': '可选，按词筛选'}}, 'required': []}}},
    {'type': 'function', 'function': {
        'name': 'run_sql',
        'description': '在只读业务库执行 SELECT（只允许业务字典里的表；不支持分号与注释，最多返回 200 行）。'
                       '★ 互不依赖的查询**一次全传进 sql 数组**（框架内部并发跑），不要一条一条调：'
                       '同一轮里不同维度、不同表、不同口径的数，都放进同一个数组。'
                       '★ 调工具的那几轮**只给工具调用，不要写任何说明文字**；数据查够后就只回一句「数据已查全」。',
        'parameters': {'type': 'object', 'properties': {
            'sql': {'anyOf': [{'type': 'string'},
                              {'type': 'array', 'items': {'type': 'string'}}],
                    'description': '一条 SQL，或互不依赖的多条 SQL 组成的数组'}},
            'required': ['sql']}}},
    # ★ 2026-11-08：结果太大时不再砍内容 —— 数组会换成「前 k 条 + total + ref」，
    #   剩下的用这只手按 offset 取回（契约同 read_file 的 next_offset）。
    {'type': 'function', 'function': {
        'name': 'read_result',
        'description': '取回上一次查询没给完的明细。只有当工具结果里出现 ref / next_offset 时才有用：'
                       '那次结果只给了前几条，其余落在这里。offset 从结果里的 next_offset 抄，'
                       'next_offset 为 null 就是取完了。要合计 / 极值仍用 SQL 算，别拿取回的行自己加。',
        'parameters': {'type': 'object', 'properties': {
            'ref': {'type': 'string', 'description': '工具结果里那个 ref，如 r3f9a1c2d4e5'},
            'offset': {'type': 'integer', 'description': '从第几条开始，默认 0；接着取就抄 next_offset'},
            'limit': {'type': 'integer', 'description': '一次取几条，默认 20'}},
            'required': ['ref']}}},
    # ★ 2026-10-30：字段明细与规则条目改成"按需取"，不再全塞进 system。
    {'type': 'function', 'function': {
        'name': 'describe_table',
        'description': '取一张表的字段清单（字段名/类型/中文名/说明/示例值/角色）。'
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
    # ★ 2026-11-10 指标契约：口径（必需过滤、算式）全在数据里，模型只报代号。
    #   为什么做成工具而不是写进提示词：写在提示词里既能被绕开、又会和别的段落对撞
    #   （府河所那次"这个除这个"就是这样被证据包的「不要自己算」压掉的）。
    {'type': 'function', 'function': {
        'name': 'list_metrics',
        'description': '列出系统里已定义的指标代号（预算执行率、工单数量这类）。'
                       '**凡是要报「预算/工单/六项指标」的数，先调它看有没有对应代号**；'
                       '有就一律用 metric_query 取，不要自己写 SQL 拼口径。',
        'parameters': {'type': 'object', 'properties': {}, 'required': []}}},
    {'type': 'function', 'function': {
        'name': 'metric_query',
        'description': '按指标代号取数：系统按口径把过滤条件拼全、把派生值（如预算执行率）算好。'
                       '只传代号、年份、范围 id，不要传 SQL、不要自己算。'
                       '代号来自 list_metrics；没有对应代号时才退回 run_sql。',
        'parameters': {'type': 'object', 'properties': {
            'code': {'type': 'string', 'description': '指标代号，如 budget_exec_rate'},
            'year': {'type': 'string', 'description': '年份，如 2026'},
            'scope_id': {'type': 'integer', 'description': '范围 id：供电所 id 或县公司 id'}},
            'required': ['code']}}},
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
        'description': '生成文件并交付下载。版式（字体/字号/行距/页边距/落款/页码）由系统按做法自动套，'
                       '你只给内容。'
                       'kind=docx 给 {title, sections:[[小节标题,正文]], sign_off:[单位,日期], '
                       'tables:[{header:[列],rows:[[值]]}]}；'
                       'kind=pptx 给 {title, slides:[{layout:cover|section|bullets|end,title,bullets}]}；'
                       'kind=xlsx 给 {sheets:[{name,title,header:[列名],rows:[[值]],'
                       'formats:["#,##0.00","0.00%"],widths:[14],total_row:true,freeze:"A3"}]}。'
                       '生成后自动过自检，不通过不给文件。'
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


def _rule_tool():
    """工具表里那一个 get_rule（收尾阶段只开放它）。"""
    for t in TOOLS:
        if ((t.get('function') or {}).get('name')) == 'get_rule':
            return [t]
    return []


def _final_answer(messages, model, max_tokens=None, allow_rule=False, trace=None):
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
                        think=False)
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
                             'content': tools_result.deliver('get_rule', _out)})
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


def _progress_feed(trace, user_content, limit=8):
    """进度自评的资料：本轮要办的事 + 已经查过的 SQL 和**查回来的值**。

    ★ 2026-11-10：原来只给"SQL 前 100 字 + 行数"，模型看不到任何数值，只能回
      「已查到：无数据」（实测 q1 连报两次，而当时已经查到 276 行 / 1089 次）。
      现在每行带首条结果的 3 个字段值，0 行也写明是空集。
    """
    lines = ['【本轮要办的事】' + str(user_content or '')[:400], '【已经查过的】']
    for t in (trace or []):
        if not isinstance(t, dict) or t.get('kind') != 'tool' or t.get('tool') != 'run_sql':
            continue
        res = t.get('result') or {}
        if not isinstance(res, dict):
            continue
        sql = str((t.get('args') or {}).get('sql') or '')[:80].replace('\n', ' ')
        if res.get('error'):
            lines.append('- %s → 报错：%s' % (sql, str(res['error'])[:80]))
            continue
        if res.get('multi'):
            for i, one in enumerate(res.get('results') or [], start=1):
                lines.append('-（第%d条）%s → %s' % (i, sql, _value_brief(one)))
        else:
            lines.append('- %s → %s' % (sql, _value_brief(res)))
    return '\n'.join(lines[:limit + 2])


def _value_brief(res, k=3):
    """一条查询结果的摘要：几行 + 首行的前几个字段值（0 行就说空集）。"""
    if not isinstance(res, dict):
        return str(res)[:60]
    if res.get('error'):
        return '报错：' + str(res['error'])[:80]
    rows = res.get('rows') or []
    if not rows:
        return '0 行（空集）'
    r0 = rows[0] if isinstance(rows[0], dict) else {}
    parts = ['%s=%s' % (key, r0[key]) for key in list(r0)[:k]]
    return '%d 行：%s' % (len(rows), '，'.join(parts))


_PERIOD_RE = re.compile(r'20(?:2\d|3\d)(?:0[1-9]|1[0-2])')


def _data_time_line():
    """库内各表**最新可用期间**（代码算）。

    ★ 2026-11-10：原来只注入【今天】，"没说时间就用最新一个月"全靠模型自己推：
      用当月 → 撞占位行；自己 MAX(期间) → 可能取到未来月。现在把事实摊给它。
      表名不写死：谁有期间列就列谁（事实来自 semantic.period_facts，带 60 秒缓存）。
    """
    try:
        from app.secretary import semantic
        rows = []
        for t in sorted(semantic.allowed_tables()):
            pf = semantic.period_facts(t)
            if pf.get('col') and pf.get('max'):
                rows.append('%s=%s' % (t, pf['max']))
    except Exception:                              # noqa: BLE001 - 算不出来就不注入
        return ''
    if not rows:
        return ''
    return ('【数据时间】（各表最新可用期间。用户没说时间时按它取，'
            '不要自己取 MAX(期间)、也不要用当月。**用户说的期间晚于这里的最大期间时，'
            '直接答"该期间数据未就绪"——不许拿去年同月或别的月份顶替**）\n'
            + '；'.join(rows[:8]))


def _progress_state(trace) -> str:
    """本轮已查到的**事实**：几条查询、有数几条、空集几条、报错几条、覆盖哪些期间。

    ★ 代码生成，不经模型 —— 这就是"进度"该有的样子：只报事实，不报判断。
    """
    n = ok = empty = err = 0
    periods, last = set(), ''
    for t in (trace or []):
        if not isinstance(t, dict) or t.get('kind') != 'tool' or t.get('tool') != 'run_sql':
            continue
        res = t.get('result') or {}
        if not isinstance(res, dict):
            continue
        for one in (res.get('results') if res.get('multi') else [res]) or []:
            n += 1
            if one.get('error'):
                err += 1
                continue
            if one.get('rows'):
                ok += 1
                last = _value_brief(one)
            else:
                empty += 1
            periods |= set(_PERIOD_RE.findall(str(one.get('sql') or '')))
    if not n:
        return ''
    return ('已跑 %d 条查询：有数 %d、空集 %d、报错 %d；涉及期间 %s；最后一条 %s'
            % (n, ok, empty, err, '、'.join(sorted(periods)) or '未带期间', last or '无'))


_FACTS_RULE = ('把这一轮已经查到的事实列成清单，一条一行，只写〔口径〕=〔数值〕（来自哪张表）；'
               '查过但没数据的那条就写「无数据」。不要写结论、不要写建议、不要写过程。')


def _collect_facts(messages, model, trace=None) -> str:
    """第 7 步收尾：只把查到的事实列成清单，不给工具、不要结论。

    2026-11-04：原来收尾直接拿全部工具结果写回答，数据一多模型就照抄，回答规则管不住。
    改成先落一份清单，第 8 步只看这份清单。
    """
    msgs = list(messages) + [{'role': 'user', 'content': _FACTS_RULE}]
    try:
        resp = _chat(msgs, model, use_tools=False, think=False)
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


def _verify_or_rewrite(msgs, model, answer, ev, trace=None) -> str:
    """闸3（2026-11-10）：正文里的数必须能在证据包里找到；对不上就带着差异重写一次。

    为什么要有：证据包只保证"能看到的数是对的"，管不住模型写一个"没看到的数"
    （府河所那次 63 件、1197846.27 元就是这么来的）。这一步机器可判，不靠模型自觉。
    """
    try:
        from app.secretary import caliber
        v = caliber.verify(answer, ev)
    except Exception as exc:                       # noqa: BLE001 - 对账坏了不阻断作答
        print('[agent] 交卷对账失败（跳过）：%s: %s' % (type(exc).__name__, str(exc)[:100]),
              flush=True)
        return answer
    if v['ok']:
        if trace is not None:
            trace.append({'seq': len(trace) + 1, 'kind': 'verify', 'result': {'ok': True}})
        return answer
    bad = '、'.join(x['text'] for x in v['missing'][:8])
    retry = list(msgs) + [
        {'role': 'assistant', 'content': answer},
        {'role': 'user', 'content':
            '上面正文里这几个数在证据里找不到出处：%s。只改这几处，其余原样保留：'
            '有出处的换成证据里的数，没出处的删掉。不要重写整段，不要补时间、前缀、背景句；'
            '拿不准就少写一句。' % bad}]
    ans2 = _final_answer(retry, model, allow_rule=True, trace=trace)
    v2 = caliber.verify(ans2, ev)
    if trace is not None:
        trace.append({'seq': len(trace) + 1, 'kind': 'verify',
                      'result': {'ok': v2['ok'], 'missing': v2['missing'][:8], 'retried': True}})
    return ans2


def _caliber_echo() -> str:
    """每一步执行前，把查数口径原文再贴一次（2026-11-10）。

    为什么放这里而不是只放 system：system 那份在长对话里会被稀释，模型后面几轮
    实际上是在按上一轮的报错反馈写 SQL（府河所那次 seq8 全错 → seq9 靠报错里的
    字段清单改 → seq10 才写对）。贴到当前轮才拦得住这条路。
    """
    try:
        from app.secretary import prompt_material as pm
        body = (pm.text('fetch') or '').strip()
    except Exception:                              # noqa: BLE001 - 口径读不到就不贴
        return ''
    if not body:
        return ''
    return ('【查数口径（这一步按它写；口径里没有的表和字段不要编，编了会被拦）】\n' + body)


def _write_answer(messages, model, user_content, trace=None, need_facts=True,
                  slots=('answer',)) -> dict:
    """第 8 步：正文只按**证据包**写（代码摊出的原始结果），不再让模型转述事实清单。

    ★ 2026-11-10：原来先让模型把查询结果写成"〔口径〕=〔数值〕"清单，再只给清单写正文。
      实测 22,116 字的工具结果被换成 6,585 字二手转述，单位/期间/基期/方向全丢
      （见 output/test_results/2026-11-10-六项指标根因复现/）。现改为 evidence.text(trace)
      直接内联权威结果；模型只负责组织语言。拿不到证据包时退回原做法。
    """
    from app.secretary import evidence
    ev = evidence.text(trace) if trace else ''
    if ev:
        if trace is not None:
            trace.append({'seq': len(trace) + 1, 'kind': 'evidence',
                          'chars': len(ev), 'content': ev[:1200]})
        msgs = [{'role': 'system', 'content': _answer_system(messages, slots)},
                {'role': 'user', 'content': user_content},
                {'role': 'user', 'content': '【这一轮查到的事实（数字只用这些）】\n' + ev}]
        if trace is not None:
            _sys = str(msgs[0].get('content') or '')
            trace.append({'seq': len(trace) + 1, 'kind': 'answer_system',
                          'result': {'chars': len(_sys),
                                     'has_answer_rule': '任务：基于已执行' in _sys,
                                     'has_fetch': '表说明' in _sys}})
        ans = _final_answer(msgs, model, allow_rule=True, trace=trace)
        return {'answer': _verify_or_rewrite(msgs, model, ans, ev, trace=trace),
                'facts': ev}
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


def _dump(obj):
    return json.dumps(obj, ensure_ascii=False, default=str)


# 按 create_time 分期的表：漏了时间条件就查成了「历年累计」，实测会被拿去和当年数比（报出 44 个所超支）


# 每张表里「范围」落在哪个字段上：SQL 里一个都没出现 → 它查的是全市


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
    """跑一条查询；0 行时先判"这是什么空"，再决定要不要说"空集就是答案"。"""
    r = tools_db.run_sql(sql or '')
    try:
        if not r.get('error') and not (r.get('rows') or []):
            v = tools_db.empty_verdict(sql)
            r = dict(r)
            r['判定'] = v['code']
            r['判定说明'] = v['note']
            if v['code'] == 'EMPTY_OK':
                r['空集提示'] = ('这是 0 行 = 空集，空集就是答案。不许摘掉时间 / 状态 / 范围条件再查一遍凑数字；'
                                '确实要改条件，必须在回答里写明改了什么、为什么。')
            else:
                r['空集提示'] = ('这是 0 行，但**不是"没有数据"**：' + v['note']
                                + '。要在回答里写成"该期间数据未就绪/还没到"，不许写成"没有数据"。')
    except Exception:                              # noqa: BLE001
        pass
    return r


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
    if name == 'get_rule':
        # 2026-10-30：规则不再靠向量检索《判据规则》，改成按主题/别名精确取。
        return get_rule(args.get('topic', ''), args.get('key', ''))
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
    if name == 'read_result':
        # 2026-11-08：取回交付层落盘的分片（工具结果太大时只管给前几条，其余放这里）。
        return tools_result.read_result(args.get('ref', ''), args.get('offset') or 0,
                                        args.get('limit') or 20)
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


def render_page_async(content, extra='', model=None, max_tokens=None, cache_key=None):
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


def render_page(content, extra='', model=None, max_tokens=None, cache_key=None):
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
        doc_context=None, doc_files=None, persona=None, trace=None, should_stop=None):
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
    # ★ 2026-11-08：trace 可以由调用方传进来（同一个 list 边跑边读），
    #   用于「实时运行步骤」——接口那边轮询这个 list 就能看到每一步。
    trace = trace if trace is not None else []
    raw_question = question
    # ── 入口路由（2026-11-11）：只分线，不改任何线的内部 ────────────────────
    #    办事题去办公线（做法 → 计划 → 工具循环 → 产物），读件去读件线，闲聊零工具；
    #    查数线（下面第 2–6 步预处理 + 证据包 + 写正文）**原样保留、一行不动**。
    #    关掉：SECRETARY_ROUTER=0（整条链路回退到 2026-11-11 之前）。
    _lane = None
    # 本会话此前的附件（2026-11-12）：用户上一轮传过文件、这一轮说「这个文章」时，
    # 请求里没有 file_ids。从会话历史把 file_id 找回来，只有这句话在指代文件时才算数，
    # 免得传过一次文件之后所有问数题都被拉进读件线。
    _hist_files = []
    _all_files = list(doc_files or [])
    if session_id:
        try:
            from app.secretary import qa_log as _qa_mod
            # 只回看最近几轮：不然几个月前传过的文件会一直把后面所有问题都拉进办公线
            _hist_files = _qa_mod.session_files(session_id, limit=6) or []
        except Exception:                           # noqa: BLE001 - 读不到按无附件
            _hist_files = []
        if _hist_files:
            _cur_ids = {str(f.get('file_id')) for f in _all_files}
            _all_files += [f for f in _hist_files if str(f.get('file_id')) not in _cur_ids]
    if (os.environ.get('SECRETARY_ROUTER') or '1').strip().lower() not in ('0', 'false', 'off'):
        try:
            from app.secretary import router as _router
            _lane = _router.attach_howto(
                _router.route(question, has_files=bool(_all_files),
                              ask=lambda _s, _u: _ask_short(_s, _u, model)), question)
        except Exception as _exc:                   # noqa: BLE001 - 路由坏了回退原链路
            print('[agent] 入口路由失败（回退查数线）：%s: %s'
                  % (type(_exc).__name__, str(_exc)[:120]), flush=True)
            _lane = None
    if _lane:
        trace.append({'seq': len(trace) + 1, 'kind': 'route', 'tool': 'router',
                      'result': _lane})
    if _lane and _lane.get('lane') in ('office', 'read', 'chat'):
        from app.secretary import agent_office
        trace.append({'seq': len(trace) + 1, 'kind': 'files', 'tool': 'files',
                      'result': {'本轮': len(doc_files or []),
                                 '会话此前': max(0, len(_all_files) - len(doc_files or [])),
                                 '清单': [{'name': f.get('name'), 'file_id': f.get('file_id'),
                                          'prev': bool(f.get('prev'))} for f in _all_files]}})
        return agent_office.ask_office(
            question, _lane, lane=_lane['lane'], model=model, session_id=session_id,
            user_id=user_id, user_code=user_code, client_ip=client_ip, channel=channel,
            doc_files=_all_files or None, doc_context=doc_context, trace=trace,
            should_stop=should_stop, max_steps=max_steps, today=today)
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
    # ★ 2026-11-08：连空骨架一起删了（原为兼容内网接口保留 result['scope']/trace，实测没人读）。
    # **不预检索、不预注入判据**：按问题检索判据挑"该用哪条标准"实测不稳（会把相邻技能的标准当成本题的标准，
    # 例如把「项目执行进度异常」当成「审批积压」）。判据改成让模型在循环里用 kb_search 工具自己查 ——
    # 需要判断就去查，查不到就不下判断（见回答契约第 7 条）。
    # 问题不改写：原话 + 纠错 + 补问法 + 补全表命中，一路往下传。
    t_loop = time.time()
    # 提问时间：接口传的 `today` / `business_date`，没传就系统当天。
    # ★ 2026-11-11：第 5 步「问题确定」要靠它默认补「上一个自然月」，所以先算好再传进去。
    _now = today or time.strftime('%Y-%m-%d')
    # ── ⑥ 问题确定：把前面几步揉成一句问题，第 7 步只拿这一句 ────────────────
    #    判据在页面框 `confirm`；框空 = 用 confirm.settle() 收敛成一句（不再下传资料）。
    #    ★ 2026-11-03 定稿：主循环不再吃「原话 + 最近几轮 + 【统计范围】」那一堆 ——
    #      取舍只在这一处，改一处就生效。第 1 步判"不走"时（闲聊 / 明确要求）
    #      原话直接进第 7 步，缺信息由它自己反问用户。
    if allow_refine:
        _conf = confirm.run(question, fixed, comp, history, ask_time=_now,
                            ask=lambda _s, _u: _ask_short(_s, _u, model))
    else:
        _conf = {'used': False, 'text': '', 'block': '【本轮原话】' + str(question or '')}
    trace.append({'seq': len(trace) + 1, 'kind': 'confirm', 'tool': 'confirm',
                  'result': {'used': bool(_conf.get('used')), 'ask_time': _now,
                             'passed': 'model' if _conf.get('used') else 'raw',
                             'text': str(_conf.get('text') or _conf.get('block') or '')[:800]}})
    user_content = ('【本轮要办的事】（按它办）\n'
                    + (_conf['text'] if _conf.get('used') else
                       (_conf.get('block') or str(question or ''))))
    # 只给一行日期：第 6 步框空着时，"上个月"要靠它自己推。这是信息，不是纪律。
    user_content += '\n\n【今天】%s' % _now
    # ★ 2026-11-10：再给一行**库内数据时间**（代码算，不让模型猜哪个是最新月）。
    #   原来只说"今天"，模型要么用当月（未结算）要么自己 MAX(期间)（可能取到未来月）。
    _dt = _data_time_line()
    if _dt:
        user_content += '\n' + _dt
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
        _want += ['kb_search', 'list_metrics', 'metric_query', 'run_sql',
                  'read_result', 'finish']
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
    seen_sig: set = set()
    noprogress = 0

    def _wrap_up(why, tail):
        """收尾：查数轮先落事实清单再写正文。

        ★ 2026-11-08：撞上限 / 原地打转 也走这里（原来只有模型自己停手才列清单）。
          清单里写"无数据"的那几项就是没查清楚的 —— 它会暴露在回答里，而不是被掩盖。
        """
        if _route['use_db'] and any(isinstance(t, dict) and t.get('kind') == 'tool' for t in trace):
            _w = _write_answer(messages, model, user_content + '\n\n' + tail,
                               trace=trace, need_facts=True, slots=_slots)
            return _w['answer'], _w['facts']
        return _final_answer(_swap_materials(
            list(messages) + [{'role': 'user', 'content': tail}], ('answer',)),
            model, allow_rule=True, trace=trace), ''

    _TAIL_BUDGET = ('时间/步数预算到了，请用已查到的数据给出结论；'
                    '如果还缺关键前提（例如具体是哪张单、哪个所），'
                    '就在回答里一句话说清需要补什么，不要再查。')
    _TAIL_STUCK = ('连续两轮查回来的结果和之前一样，没有新数据了。'
                   '请用已查到的数据给出结论；缺的那几项在回答里一句话说清缺什么，不要再查。')
    while True:
        step += 1
        _over_step = bool(max_steps) and step > max_steps
        _budget = float(getattr(config, 'QUESTION_BUDGET_S', 0) or 0)
        _over_time = _budget > 0 and (time.time() - t_all) > _budget
        # ★ 2026-11-08：用户点了「停止生成」→ 立刻收手，用已有的东西回一句，
        #   不再继续调模型/工具。should_stop 由接口层传（轮询那个 task 的状态）。
        if should_stop is not None and should_stop():
            answer = '（已停止生成）'
            trace.append({'seq': len(trace) + 1, 'kind': 'final', 'content': '用户点了停止'})
            break
        if _over_step or _over_time:
            _why = ('已到查询步数上限 %d' % max_steps) if _over_step else ('已到单题时间预算 %.0f 秒' % config.QUESTION_BUDGET_S)
            answer, facts_text = _wrap_up(_why, _TAIL_BUDGET)
            trace.append({'seq': len(trace) + 1, 'kind': 'final', 'content': _why + '，强制出正文'})
            break
        if noprogress >= 2:
            answer, facts_text = _wrap_up('原地打转', _TAIL_STUCK)
            trace.append({'seq': len(trace) + 1, 'kind': 'final',
                          'content': '连续 %d 轮无新数据，强制收尾' % noprogress})
            break
        t0 = time.time()
        # ★ 2026-11-10：每一步都把查数口径贴到当前轮（只进这一次调用，不累积进 messages）。
        _echo = _caliber_echo() if (tools and _route['use_db']) else ''
        _msgs = list(messages) + [{'role': 'user', 'content': _echo}] if _echo else messages
        # 第 7 步 · 干活循环。工具按分流子集给；没给工具就直接要正文。
        # 查数轮关思考：实测同一轮开思考会写 240~3400 字过程散文（一轮 10~17s）。
        # ★ 2026-11-08：原来按动作写死额度（查数 600 / 出文件 4000）——600 那档把正文
        #   截在半句、工具调用发不出来，答案是一段"查数计划"。现在不写死（config.output_tokens）。
        resp = _chat((_msgs if _echo else messages), model, think=False,
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
            _body = tools_result.deliver(o['name'], o['result'], session_id=session_id)
            messages.append({'role': 'tool', 'tool_call_id': tc['id'], 'content': _body})
        # 周期性软提醒：防止一直翻表不收手。
        # ⚠️ 必须放在**全部 tool 回填之后**：OpenAI 兼容协议要求 assistant(tool_calls) 后
        #    紧跟每个 tool_call_id 的 tool 消息。原来插在两者之间，qwen 能容忍，
        #    deepseek-v4.1 直接 400（"insufficient tool messages following tool_calls message"）。
        # ★ 2026-11-03：这里原来每 2 轮塞一句「对照【各类问题的必答项】逐项点一遍」——
        #   必答项随规则一起删了，这句在指空；代码也不再替模型管节奏。
        # ★ 2026-11-08（Fred）：本轮结果指纹 —— 连续两轮一模一样，说明它在原地打转，
        #   下一轮开头就收尾（不按轮数砍，按"有没有新数据"砍）。
        # ★ 2026-11-10：**报错不算"新数据"、也不计入指纹**。
        #   原来一个崩掉的工具（同一条 AttributeError）两轮就凑成"连续无新数据"，
        #   直接把"工具坏了"翻译成"查不到"（实测 q2：17 步 0 条 SQL 就收尾了）。
        _sigs = set()
        _n_err = 0
        for _o in outs:
            if isinstance(_o.get('result'), dict) and _o['result'].get('error'):
                _n_err += 1
                continue
            try:
                _sigs.add(json.dumps(_o.get('result'), ensure_ascii=False,
                                     sort_keys=True, default=str)[:1500])
            except Exception:                      # noqa: BLE001
                _sigs.add(str(_o.get('result'))[:1500])
        if _sigs:
            noprogress = (noprogress + 1) if not (_sigs - seen_sig) else 0
            seen_sig |= _sigs
        elif _n_err and not _sigs:
            # 整轮全是报错：不算打转，但要告诉它换个手段，别继续瞎试
            noprogress = 0
        # ★ 2026-11-10：改成**代码生成**的进度事实，不再另调一次模型自评。
        #   原来那次自评是让模型"看 SQL 前 100 字猜有没有数据"，实测 q06、q11 已经查到
        #   几百行、仍回「已查到：无数据」，这条假信号还被塞回主循环。现在只报事实：
        #   查了几次、成功几次、空集几条、报错几条、覆盖了哪些期间。
        if step % 3 == 0 and not fins and _route['use_db']:
            _prog = _progress_state(trace)
            if _prog:
                messages.append({'role': 'user', 'content': '【进度】' + _prog})
                trace.append({'seq': len(trace) + 1, 'kind': 'progress', 'round': step,
                              'content': _prog[:600]})
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
    # 产物记账（2026-11-08）：make_file / run_code 出的文件单独列成一张清单。
    # 为什么：产物原来只躺在 trace 里，接口调用方要自己翻；现在 result['artifacts']
    # 直接给名字、字节数、下载地址、自检结论。
    artifacts = []
    for _t in trace:
        if _t.get('kind') != 'tool' or _t.get('tool') not in ('make_file', 'run_code'):
            continue
        _r = _t.get('result') or {}
        if _r.get('url'):
            artifacts.append({'kind': _r.get('kind') or str(_r.get('name') or '').rsplit('.', 1)[-1].lower(),
                              'name': _r.get('name'), 'bytes': _r.get('bytes'),
                              'file_id': _r.get('file_id'), 'url': _r.get('url'),
                              'self_check': (_r.get('self_check') or {}).get('verdict') or '未自检'})
        for _o in (_r.get('outputs') or []):
            if _o.get('url'):
                artifacts.append({'kind': str(_o.get('name') or '').rsplit('.', 1)[-1].lower(),
                                  'name': _o.get('name'), 'file_id': _o.get('file_id'),
                                  'url': _o.get('url'), 'self_check': '未自检'})
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
        'unverified': blocked or any(
            t.get('kind') == 'verify' and not (t.get('result') or {}).get('ok')
            for t in trace if isinstance(t, dict)),
        # 闸门留痕（2026-11-02）：为什么被拦 / 为什么放行、那一轮给没给工具。
        # 内网只看得到最终 JSON，没有这一段就分不清"模型不老实"和"没给工具"。
        'evidence_gate': gate_log,
        'steps': len(trace),
        'history_used': len(history),
        'history_injected': len(hist_detail),
        # 带文件提问时：本轮用了哪些文件、各用了多少字、有没有节选（前端据此显示"已读 2.1 万字"）
        'files': list(doc_files or []),
        'artifacts': artifacts,
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

