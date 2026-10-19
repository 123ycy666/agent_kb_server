# -*- coding: utf-8 -*-
"""模型自主循环：只给工具与底线，不给流程。

分工：**信息补全由本服务自己做**（字面纠错与名称归一 → 时间与地点补全），
问题补全走知识库的补全规则，再查口径、取数、作答。
上游那个补全应用已不在链路里，时间与地点同样由本服务补（见 time_scope.py）。
全部在一个模型循环里由模型自己排顺序，代码不写业务流程。
"""
import concurrent.futures
import hashlib
import socket
import threading
import uuid
import json
import re
import ssl
import time
import urllib.error
import urllib.request

from app.secretary import config
from app.secretary import gaps
from app.secretary import name_fix
from app.secretary import semantic
from app.secretary import time_scope
from app.secretary import tools_db
from app.secretary import tools_kb

# 数字断言：出现「数字 + 单位」就认为它在给业务数字（这类答案必须有工具出处）
_CLAIM = re.compile(r'\d+(?:\.\d+)?\s*(?:万|亿|%|％|件|个|条|户|千瓦时|千瓦|kWh|元|次|小时|倍)')


TOOLS = [
    {'type': 'function', 'function': {
        'name': 'kb_search',
        'description': '检索业务知识库，用于弄清术语、指标口径、业务背景。返回按相关度排序的文档切片。',
        'parameters': {'type': 'object', 'properties': {
            'query': {'type': 'string', 'description': '检索意图'},
            'top_k': {'type': 'integer', 'description': '返回几条，默认 5'}},
            'required': ['query']}}},
    {'type': 'function', 'function': {
        'name': 'find_column',
        'description': '在业务字典内的表里，按字段名或字段中文注释搜字段，返回「表名 + 字段名 + 类型 + 注释 + 示例值」。'
                       '当你不确定某个数据在哪个字段/哪张表时先用它（例如搜「线损」「执行金额」「台区」）。',
        'parameters': {'type': 'object', 'properties': {'keyword': {'type': 'string'}}, 'required': ['keyword']}}},
    {'type': 'function', 'function': {
        'name': 'run_sql',
        'description': '在只读业务库执行一条 SELECT（只允许业务字典里的表；不支持分号与注释，最多返回 200 行）。'
                       '★ 要查多个互不依赖的数时，请在**同一次回复里一次发出多条 run_sql**'
                       '（系统会并发执行，一轮就全拿到）；不要一条一条分多轮来 —— 每多一轮就多花几秒。'
                       '★ 调工具的那几轮**只给工具调用，不要写任何说明文字**（不复述在查什么、不解释下一步、'
                       '不描述结果）。**数据查够后就不要再写了，只回一句「数据已查全」** —— '
                       '最终汇报会另外让你写；自己在这轮写汇报会白烧十几秒，而且系统不用那段文字。',
        'parameters': {'type': 'object', 'properties': {'sql': {'type': 'string'}}, 'required': ['sql']}}},
    # ★ 收尾信号（2026-10-09 加）：查数轮强制 tool_choice=required，模型不能写散文，
    #   于是用一个空工具表示"到此为止"。数据查够就调它，之后由系统另行要一次正文。
    {'type': 'function', 'function': {
        'name': 'finish',
        'description': '数据已经查够、可以写汇报了，就调用它收尾（不要再写任何文字）。',
        'parameters': {'type': 'object', 'properties': {
            'plan': {'type': 'string', 'description': '一句话说明你打算怎么答'}}, 'required': ['plan']}}},
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


def _model_error_text(model, exc):
    """把 urllib 的异常翻成一句人话（路由层直接回给用户）。"""
    if isinstance(exc, urllib.error.HTTPError):
        return ('模型「%s」在当前模型网关不可用（HTTP %d）。'
                '请到「模型」下拉框换一个再问，或联系管理员确认该模型是否已部署。'
                % (model, exc.code))
    return ('无法连接模型服务（%s）。请确认模型地址与网络是否正常。'
            % (getattr(exc, 'reason', exc),))


def _chat(messages, model, use_tools=True, max_tokens=4000, think=True, tool_choice=None):
    # ★ 默认开思考（**不要**关）：关掉思考时模型没有隐藏草稿纸，过程话术会直接写进 content，
    #   正文里出现「这是对策题。回答要点：…」「让我确认一下」「现在我已经收集了…」——
    #   领导看到的就是思考过程（实测，output/probe_thinking.py、probe_final_turn.py）。
    #   开思考时过程落在 reasoning 字段，content 只剩最终回答。
    # 代价：reasoning 也占 completion token，max_tokens 给小了会把正文挤空 —— 所以放大到 4000。
    # 2026-10-10：模型换百炼 deepseek-v4.1-flash（内网 v4 到期），思考开关用 enable_thinking。
    body = {'model': model, 'messages': messages,
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
        body['tools'] = TOOLS
        if tool_choice:
            body['tool_choice'] = tool_choice
    body = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(config.LLM_BASE + '/chat/completions', data=body, headers={
        'Authorization': 'Bearer ' + config.LLM_KEY, 'Content-Type': 'application/json'})
    # ★ 重推理题偶发单次调用超过 HTTP_TIMEOUT（实测问「今年能达标吗」那次跑到 137s），
    #   直接抛出去就变成「服务异常：TimeoutError」这种用户看不懂的东西 —— 等于这题废了。
    #   所以**超时重试一次**并把等待放宽：多等一会儿，换回一个答案。
    last = None
    for tmo in (config.HTTP_TIMEOUT, config.HTTP_TIMEOUT + 90):
        try:
            raw = urllib.request.urlopen(req, timeout=tmo, context=_ctx).read().decode('utf-8', 'replace')
            return json.loads(raw)
        except urllib.error.HTTPError as e:
            # 429/5xx 是**端点侧暂时不可用**：实测 3 并发时页面调用被 429 打回，
            # 一次不重试就白丢一张页面（日志证据见 output/test_results/…/EV-0005）。
            # 4xx（除 429）是请求本身的问题，重试没意义，照样抛。
            if e.code not in (429, 500, 502, 503, 504):
                raise ModelUnavailable(_model_error_text(model, e)) from e
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
    raise ModelUnavailable(_model_error_text(model, last)) from last


def _final_answer(messages, model, max_tokens=2500):
    """兜底：**关掉工具**再明确要一次正文。

    用在 content 为空的场合（实测 Q10「申报有没有不该批的」跑 594s、16 次查询后返回空正文）：
    开着思考时 reasoning 会把 max_tokens 吃满，正文就没了。不能把 reasoning 当答案给领导
    （那是内心独白），更不能给空白 —— 不带工具时它没得可调，只能写正文。
    """
    msgs = list(messages) + [{'role': 'user', 'content':
                              '请直接给出给领导的最终回答正文，不要调用工具、不要复述分析过程、不要写思考过程。'}]
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



def execute(name, args):
    if name == 'kb_search':
        return tools_kb.kb_search(args.get('query', ''), int(args.get('top_k') or 3))
    if name == 'find_column':
        return tools_db.find_column(args.get('keyword', ''))
    if name == 'run_sql':
        r = tools_db.run_sql(args.get('sql', ''))
        # 0 行 = 空集。模型拿到空结果会忍不住摘掉时间/状态条件再查一遍凑数
        # （实测：带年份查出 0 行 → 去掉年份 → 148.07 万 → 负余额）。
        # 在它**正想放宽的那一刻**把话说死，比写在几百行外的规则文档里管用。
        try:
            if not r.get('error') and not (r.get('rows') or []):
                r = dict(r)
                r['空集提示'] = ('这是 0 行 = 空集，空集就是答案。不许摘掉时间 / 状态 / 范围条件再查一遍凑数字；'
                                '确实要改条件，必须在回答里写明改了什么、为什么。')
        except Exception:                           # noqa: BLE001
            pass
        return r
    return {'error': 'unknown tool ' + str(name)}


# 固定口径（2026-09-24 业务确认）：**每次都带，不靠检索**。
# 这里只放「业务定死的取值口径」——算错了就是错数，且每题都要用：
#   状态分组、实际投入口径、分配预算取值、年度对齐、空集、结论三选一、重复申报、编号翻中文。
# ★ **分析流程不在这里**（四个视角 / 同期对比 / 多数据拼接 / 建议怎么给）——
#   那些写在 skills/判据规则.md 零节，**走知识库检索**注入。要改分析规矩改文档，不改这里。
CALIBER = (
    '【固定口径】（每次都必须遵守，不用去检索）\n'
    '1. **已审批 = 工单状态 3、6、7**（3＝审核通过待施工；6＝施工中；7＝施工完成待验收）。'
    '**状态 2 不算** —— 它在审批流里（按项目类型分别送县公司运检部或营销部审核，没审完就不是通过）。'
    '**不算** 0 草稿、1 提交审核、2 审核中、4 已驳回、5 已取消、9 驳回已修改。\n'
    '1.1 **「未审批」= 状态 0 草稿、1 提交审核、9 驳回已修改**（不含已驳回、已取消）。'
    '报未审批数量时要写明含哪几类；**一次回答里只用一个口径**，不许一会儿按 0/1/9、一会儿按 0/1/2 报两个数。\n'
    '2. **实际投入 = 已审批工单的预算金额**（budget_total）。库里的实际发生金额字段实测基本为空'
    '（**用前先 COUNT 一次确认，不要把零星有值的几条当全部**），**一律不用** —— 包括「钱花了没动」这类题，'
    '也一律用预算金额（判据见《规则》第七节第 4 条）。\n'
    '3. **执行率 = 当年已审批工单预算合计 ÷ 当年分配预算**。分配预算**只取供电所级分配结果**'
    '（t_power_company_budget_result，条件 = 该所 + 问的年度 + deleted_flag=0 的唯一有效行）；'
    '**全市 = 各所分配额之和（现查）**，**不要用公司级总预算表当分配额**（那是分配计算的输入）。\n'
    '4. 分配、已审批、未审批**必须同年度、同层级**。对齐后占比 >100% 或余额为负 → **如实报**'
    '「已审批已超出当年分配预算 X 万元」并提示需核实（分配额未更新 / 工单跨年 / 重复申报）；'
    '**不许用「含往年结转」解释掉，也不许静默当成正常**。\n'
    '4.1 **不许把内部的 0.6 达标系数换算成「基准线」写给人看** —— 回答里不许出现「XX% 基准线」这类内部换算；'
    '要么直接给「已过时间比例」和「当前占比」两个数让人自己看，要么只说「要达标还需再完成多少」。\n'
    '5. **空集就是答案**：第一次查到 0 行就按 0 行答（写清按什么口径没有记录）；'
    '**不许摘掉时间 / 状态 / 范围条件再查一遍凑数字**。\n'
    '6. 问「实际花了多少 / 决算金额 / 实际与申报差多少」→ 写「系统未采集实际投入数据，只能用预算金额」。\n'
    '7. 状态与缺陷类型编号**一律翻中文**；查不到要写清口径，**不要回「未找到相关信息」**。'
    '**翻出来的中文如果和字段名一模一样**（例如「缺陷类型：缺陷类型」「项目类型：项目类型」），'
    '说明这条码值没登记 —— 要写「未登记（编号 X）」，**不许把字段名当值写**。\n'
    '8.1 **指代词要有对象**：问句里的「这单 / 这条线 / 这个台区 / 那几张」如果在本场会话里'
    '找不到明确对象（没提过编号、名称，也没有上一轮的对象），**先问清是哪一张 / 哪一条**，'
    '不要自己挑一个来凑答。**宁可反问，不要猜。**\n'
    '8. **「能不能批 / 能不能报」类问题：第一句给结论，结论只能是「符合 / 不符合 / 无法判定」三种**，'
    '而且**必须与后面的依据一致** —— 依据里写了"超出余额 X 万元"，就**不许**写"可以批 / 符合"。\n'
    '9. **「预算装得下」只对还没批的单判**（状态 0/1/2）。状态已是 3/6/7 的单，'
    '它自己的金额**已经计入「已审批」**，直接答「该单已审核通过（待施工 / 待验收），无需再批」，'
    '**不要再拿剩余余额去比它**（重复扣一次必然算出"超余额"）。\n'
    '10. 发现同一所、同名（或只差标点）、同金额的多条工单 → 判「**疑似重复申报**」，'
    '报出条数与 ID 提示核实，**不许静默当成两笔累加进已审批**。\n'
    # --- 以下是「分层应答纪律」：每题都要用，所以固定注入；

    #     **怎么答准**（数据地图、各层取数与坑、建议算法、阈值判据）写在知识库文档里检索。

    '11. **领导是分多轮问的，你只答被问的那一层**：\n'
    '    · **L1 情况**（「XX 怎么样 / 有多少 / 情况如何」）→ 给真实数据：条数 / 金额 / 状态构成 + 关键指标本期值；'
    '问成效时带同期。**不要给对策**。\n'
    '    · **L2 原因**（「为什么 / 怎么差这么多」）→ **至少两个数据源拼起来**说清成因与它们的关系。\n'
    '    · **L3 对策**（「怎么办 / 钱往哪投 / 先干哪个」）→ 往年投入 + 指标偏重 → 优先级 + 金额 + 指向的指标。\n'
    '    · **L4 预测**（「能达标吗 / 年底到多少」）→ 按当前速度外推 + 达成需要什么条件。\n'
    '    **没问的不铺**（L1 不许把原因和对策一起倒出来）；下一层承接上一层结论但不重复。\n'
    '    · **正文按必答项的 ①②③… 序号分段写**：一项目一段、每段一句话（30~60 字）、带数字，'
    '不合并、不跳号；同一件事只说一次（不许在开头概况里说一遍、分条里再说一遍）。'
    '问「有哪些」的逐条清单不受此限（一行一条）。\n'
    '    · **追问必须接上文**：像「为什么」「那怎么办」「这样能达标吗」这种省略主语的追问，'
    '**一律从同一场会话的上一轮继承对象、范围、口径**（上一轮问的是哪个所，这几轮就还是哪个所），'
    '**不许回「指代不明 / 请补充说明」**。\n'
    '    · **同一场会话口径不许漂**：上一轮按所、这一轮不能悄悄变全市；要变必须写明。\n'
    '    · **问「全市 / 所有 / 都 / 有哪些」时，必须扫全域并先给总数**（N 个），再逐条列；'
    '**不许只挑一个所、一张单当样本就收尾** —— 那是抽样，不是回答。\n'
    '12. **先拆再答**：单点（一个数 / 一份清单）直接查直接答；多点（要拼 2+ 数据源、或跨期跨表）'
    '**先拆成 2~4 个子问题**，把互不依赖的查询**一次发出**，再汇总成一段话。\n'
    '13. **拿到问题先自己拆**（不管见没见过这类问题）：\n'
    '    · 他到底要什么答案（判断？对比？预测？办法？）→ 要答这个，需要哪几块事实 →\n'
    '      每块去哪个数据源、跟谁比、怎么算 → 推出什么结论 / 建议。\n'
    '    · **先给答案的重点，再摆支撑它的数据**；不要先罗列数据再等别人自己得结论。\n'
    '    · 结论、原因、建议都要有依据；说不出依据的判断不许写。\n'
    '    · 没见过的问法也要这么拆，不要因为"没有先例"就答得浅。\n'
    '【回答模板】（按问题类型选一套，**不是固定句式**）\n'
    '· 综合研判型（"X 整体怎么样""哪个最重""最该抓什么"）→ 结论一句话 + 最突出的 2~3 个问题（各带一个数）'
    '+ **先抓什么** + 预计效果\n'
    '· 情况型（"有多少""执行得怎么样"）→ **先给判断**（快/慢、多/少、卡不卡）+ 支撑的数据；'
    '问"怎么样"只报数等于没答\n'
    '· 原因型（"为什么""怎么降的"）→ 至少两个数据源拼起来 + 说清它们的关系\n'
    '· 对策型（"钱怎么花""先干哪个"）→ 方向 + **为什么是这个方向（依据以往数据）** + 做完预计到什么程度\n'
    '· 预测型（"能达标吗""年底到多少"）→ 按当前速度外推 + 达成需要什么条件\n'
    '· **任何分析题：结论、原因、建议都要有依据；建议必须落到"谁做什么"，禁止"加强管理""提高重视"**\n'
)


# 公共契约：**所有题都带**（口吻 / 格式 / 出处 / 清单纪律），但**不含"怎么分析"**。
# 为什么要拆开：分析那套（分层、四视角、回答模板、拆解方法）以前是每题都注入，
# 结果问「全市有多少个供电所」也被灌一遍分析要求（答得又长又像小报告）。
# 现在按题型分流：分析题 = 公共 + 分析契约；查数题 = 只带公共。
COMMON = (
    '【汇报口吻与格式】（所有回答都适用）\n'
    '· 像秘书向领导汇报：直接说事、说重点；**不要出现"我已经获得足够信息""让我组织回答"'
    '"接下来我将""根据查询结果"这类过程话术**。\n'
    '· 你只负责回答领导的问题：**不要复述你的思考过程、判断过程或工作步骤**'
    '（例如"这是分析题""这是对策题""数据已齐""现在给出回答""让我组织一下"），也不要把中间推理写成英文句子。\n'
    '\u00b7 纯文本、一行一句：**正文里不要出现 **、#、|、>、反引号 这些符号**，也不要小标题；要分条就用"1. 2. 3."。\n'
    '\u00b7 **长度随题型**：查数题（只问一个数/差值/名单/排第几）**1–3 行、只给数 + 口径**，不许加建议/成效/做到什么程度/需审定；分析题与预测题不超过 8 行，方案题不超过 10 行 —— 超了砍明细，不要重复。\n'
    '\u00b7 不提 SQL、表名、字段名、数据库、工具过程。\n'
    '\u00b7 数字必须来自本次查询，或本轮**写清依据的推算**：不许无依据估算、不许补 0、不许拿别的年份顶；'
    '**预测 / 推荐类问题（投完会怎样、年底到多少、明年能多拿、该做什么）必须推算并标注「推算」**。\n'
    '\u00b7 查到 0 行就是答案：写清"按什么口径没有记录"，不要回"未找到相关信息"，也不要放宽条件重查。\n'
    '\u00b7 问"有哪些"要逐条列全（一行一条），说 N 条就得正好 N 条。\n'
)


# 回答契约：**固定在提示词里，不靠知识库检索**。
#
# 为什么固定：检索按相关度取切片，可能只命中「阈值」而漏掉「怎么答」。
# 所以「从哪些角度答、答到什么粒度、多长」永远随请求带上；
# 知识库只负责「判断标准是多少、口径怎么写、取数用哪张表」。
ANALYSIS_FRAME = (
    '【回答契约】（每次都按它走，不依赖检索到的内容）\n'
    '1. 直接按问题要的答案来答：问「有哪些」就逐条列、问「多少」就给数、问「为什么」就给成因、'
    '问「怎么办」就给办法。\n'
    '   **不要说出你在判断题型、也不要说出你打算怎么组织回答** —— 那是你自己的事。\n'
    '2. 问「有哪些 / 哪些 / 是哪些」→ **必须逐条把对象列出来**（一行一条），'
    '每条给题目问到的字段（名字、所属、金额、状态、天数…）；**只回一个数就是答非所问**。\n'
    '3. 问「有多少 / 几个」→ 给数，并说明这个数是怎么算的（范围 + 状态 + 时间口径）。\n'
    '4. 问「为什么 / 原因」→ 业务库里通常**没有原因字段**：先把能确定的事实给全'
    '（状态、停留天数、金额、卡在哪个节点、责任人），再明确写「原因需业务核实，系统里没有原因字段」。'
    '**不许编原因，也不许回一句「未找到相关信息」。**\n'
    '5. 问「怎么办 / 先办哪几个 / 给建议」→ 给排序 + 排序依据 + 该谁动。\n'
    '6. 只问一个数 → 只给数，**不要附加「偏高/偏低/异常」这类判断**。\n'
    '7. 判断标准只认下面【本次的规则】里给的；没给标准就不下判断、不编标准。\n'
    '8. **正文就按【各类问题的必答项】里 ① ② ③…的顺序分段写**：一项目一段，段首写编号，'
    '每段一句话（30~60 字）、带数字；不合并、不跳号、不重复。不设字数上限，但每项只准写一句；'
    '问「有哪些」的逐条清单一行一条。\n'
    '9. 数量必须与清单一致：说「N 条」，清单就得正好 N 条；概览与明细不能互相矛盾。\n'
    '10. 要下判断（比大小、找原因、看趋势、判是否异常/是否合理）之前，**先用 kb_search 查一次《'
    + config.RULE_DOC + '》**，把最相关的那一节取全，再严格按它判断；查不到标准就只报事实、不编标准。\n'
    '【怎么一步步深入】（做题方法；每一类问题该看哪些维度、答案必须出现什么，'
    '**先用 kb_search 把《' + config.RULE_DOC + '》里对应那一节取回来**，按它逐项检查）\n'
    '1. 先想清楚：领导要的是哪种答案 —— 判断（好/差、达标/不达标）、对比（跟谁比、差多少）、'
    '预测（会变成什么样）、还是办法（谁做什么）。\n'
    '2. 再想：要给出这个答案，需要哪几块事实？每块去哪张表、按什么口径算、跟谁比。\n'
    '3. 动手查：互不依赖的查询**一次发出**；一块一块把事实凑齐，**发现缺哪块就补查哪块**，'
    '不要拿「大概齐」的数据下结论。\n'
    '4. 拿到事实之后按顺序走：先比（跟全市/同类/同期比，算出差多少）→ 再算（比例、倍数、占比）→ '
    '再归因（是哪几个数共同造成的）→ 最后给办法（谁做什么、做完到什么程度）。\n'
    '5. 下笔前把《判据规则》里那一类的「必答/易漏」和角度库逐项对一遍：缺哪项就补哪项；'
    '要明细的必须逐条写出对象名字，要预计的必须给出具体数值或月份，不许写空话。\n'
    '6. 查不到就写清口径和「系统里没有这类数据」，不许略过、不许编。\n'
)


def rule_slices(query, top_k=None, limit=700):
    """检索**唯一规则文档**的切片（全系统只有一篇，见 config.RULE_DOC）。返回 (切片, 最高分)。

    ★ 只认这一篇：以前是"拿问题去知识库检索判据"，会把相邻技能的标准当成本题的标准
      （实测把「项目执行进度异常」的标准套到「审批积压」上）。限定成一篇之后，
      这个错法从根上不可能再发生；技能文档也不再进知识库跟它抢名额。
    检索失败按「没规则」处理：宁可只报事实，也不许拿别的东西当标准。
    """
    q = (query or '').strip()
    if not q:
        return [], 0.0
    want = top_k or config.RULE_TOP_K
    # 只认一篇 → 必须多取候选再过滤（kb_search 返回前会先截到 top_k 条）
    try:
        r = tools_kb.kb_search(q, top_k=max(want * 3, 9), limit=limit)
    except Exception:                               # noqa: BLE001
        return [], 0.0
    out = []
    for n in (r.get('nodes') or []):
        if config.RULE_DOC not in (n.get('doc_name') or ''):
            continue
        out.append(n)
        if len(out) >= want:
            break
    best = max([float(n.get('score') or 0) for n in out], default=0.0)
    return out, best


# 兜底：字典/提示词读不到时才用（正常情况下系统提示全部来自数据库）
_FALLBACK = '你是随州供电公司的项目管理秘书，负责回答业务问题。'

# ---------------------------------------------------------------------------
# 规则优先级声明（2026-09-22 加，纯新增，不删任何原有规则）
#
# 为什么需要：system 里的通用规则会和 user 段【统计范围】撞车 —— 典型是
# `business_rules【时间处理】`写着"未指定年份时默认使用当前年份"，而代码在多轮会话里
# 会按"沿用上一轮"给出**别的**年份。原文任何地方都没声明谁优先，模型只能自己猜，
# 表现就是"回答被带偏"。
#
# ★ 措辞必须与真实链路一致：user 段里**没有**"用户原话"！第一行是**代码做过
#   名称纠错 + 时间地点补全之后**的问题（见 ask() 开头的 raw_question → question）。
#   写成"以用户原话为准"会让模型去找一个**提示词里根本不存在**的参照物。
#   这也是刻意不为原话单独开一段的原因：那会造出"同一件事两个版本"，正是要避免的病。
#
# 可用 SECRETARY_RULES_PRIORITY=0 关掉。
# ---------------------------------------------------------------------------
RULES_PRIORITY = (
    '========== 规则优先级（冲突时一律按此顺序，前面覆盖后面） ==========\n'
    '① 本轮问题 +【统计范围】+【名称归一说明】—— 这三样都由代码确定，**是唯一权威**：\n'
    '   问题里的时间、地点、名称都不要再改动，不要自己推算日期，不要另立一套口径。\n'
    '   （问题已按词典纠正过名称，请直接用它的写法，不要再改回去）\n'
    '② 本轮检索到的业务规则切片（【补全参考】里那些）—— 它**只是参考**。\n'
    '③ 本 system 里的各节通用规则（下面所有【】开头的小节）—— 与前两条冲突时让位。\n'
    '④ 【本场会话摘要】与【对话历史】—— 只用来理解"本轮在问什么"，\n'
    '   **既不是数据来源、也不是结论来源**，里面的数字和结论一律不得直接引用。\n'
    '\n'
    '冲突时**直接按前者执行**：不要"综合两者"，也不要在回答里出现两个版本。\n'
)


def _system(today=None):
    """系统提示 = 他们在 ai_prompt 里维护的提示词（config.PROMPTS 指定的 key）
    + 业务字典的表目录。**提示词不在代码里写死**。

    最前面会加一段**规则优先级声明**（见 `RULES_PRIORITY`，可用
    `SECRETARY_RULES_PRIORITY=0` 关掉）—— 因为 system 里的通用规则和 user 段的
    【统计范围】会撞车，而原文任何地方都没声明谁优先。
    """
    parts = []
    try:
        pool = semantic.prompts()
        for k in (config.PROMPTS or []):
            if pool.get(k):
                parts.append('===== ' + k + ' =====\n' + pool[k])
    except Exception:
        pass
    head = '\n\n'.join(parts).strip() or _FALLBACK
    if config.RULES_PRIORITY:
        head = RULES_PRIORITY + '\n' + head
    # 必须给「今天」：停留天数、同比、"截至今天"全靠它。不给，同一条单两次会算出不同天数。
    head += ('\n\n今天的日期是 %s。所有「停留天数 / 截至今天」一律按这一天算，'
             '不要拿数据里的时间当今天。' % (today or time.strftime('%Y-%m-%d')))
    try:
        return head + '\n\n【可用数据表（只能查这些，字典外的表一律不可用）】\n' + semantic.table_menu()
    except Exception:
        return head


def _one_call(tc):
    name = tc['function']['name']
    try:
        args = json.loads(tc['function'].get('arguments') or '{}')
    except Exception:
        args = {}
    t1 = time.time()
    try:
        res = execute(name, args)
    except Exception as e:
        res = {'error': type(e).__name__ + ': ' + str(e)[:300]}
    return {'name': name, 'args': args, 'result': res, 'ms': int((time.time() - t1) * 1000)}


def _exec_calls(calls):
    """同一轮的多个工具调用并发执行；返回顺序与 calls 一一对应。"""
    if len(calls) == 1:
        return [_one_call(calls[0])]
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, len(calls))) as ex:
        return list(ex.map(_one_call, calls))


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
        history_turns=None):
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
    # 必须先存下来：下面 time_scope 的结果也叫 scope，直接 scope = … 会把形参吞掉
    # （曾经的 bug：传了范围却全按「全市」作答，证据 output/test_results/…/EV-0003）。
    force_scope = (scope or '').strip() or None
    # ⓪ 字面纠错 + 名称归一（阶段①的「字面」部分，在问题补全之前）。
    #    必须先做：字没改对，后面检索知识库、补全、写 SQL 全是白费。
    t_fix = time.time()
    fixed = name_fix.fix(question)
    if fixed['used']:
        question = fixed['text']
        trace.append({'seq': len(trace) + 1, 'kind': 'namefix', 'tool': 'name_fix',
                      'ms': fixed['ms'],
                      'args': {'input': raw_question},
                      'result': {'fixed': fixed['text'],
                                 'word_fixes': fixed['word_fixes'],
                                 'name_fixes': fixed['name_fixes'],
                                 'names': fixed['names'],
                                 'areas': fixed['areas'],
                                 'unresolved': fixed.get('unresolved', []),
                                 'hint': fixed['hint']}})
    namefix_ms = int((time.time() - t_fix) * 1000)
    # ⓪·4 载入多轮上下文（同一场会话最近的几轮问答）。
    #      **必须放在时间地点补全之前** —— 本轮没提时间/地点时要沿用上一轮的口径。
    #      载入失败按「无上下文」处理，绝不影响本轮问答。
    turns = config.HISTORY_TURNS if history_turns is None else max(0, int(history_turns))
    history = []
    if turns and session_id:
        try:
            from app.secretary import qa_log
            history = qa_log.load_context(session_id, limit=turns)
        except Exception as e:                      # noqa: BLE001
            print('[agent] 读取多轮上下文失败（按无上下文继续）：%s: %s'
                  % (type(e).__name__, e), flush=True)
    prev_scope = None
    if history:
        last = history[-1]
        prev_scope = {'period_type': last.get('period_type'),
                      'period_key': last.get('period_key'),
                      'time_text': last.get('time_text'),
                      'place': last.get('place')}
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
    # ⓪·5 时间与范围补全：相对时间词换成具体期间；没说的优先沿用上一轮，再没有才补默认值。
    #       这种换算是确定性的（「上月」是几月取决于今天），交给代码，不留给模型去猜日期。
    t_scope = time.time()
    scope = time_scope.describe(question, fixed, prev=prev_scope, today=today,
                               force_place=force_scope)
    if scope['question'] and scope['question'] != question:
        question = scope['question']
    trace.append({'seq': len(trace) + 1, 'kind': 'scope', 'tool': 'time_scope',
                  'ms': int((time.time() - t_scope) * 1000),
                  'result': {'matched': scope['time']['matched'],
                             'time': scope['time']['time_text'],
                             'period_type': scope['time']['period_type'],
                             'period_key': scope['time']['period_key'],
                             'time_is_default': scope['time']['is_default'],
                             'place': scope['place'],
                             'place_is_default': scope['place_default'],
                             'history_turns': len(history),
                             'time_inherited': scope['inherited']['time'],
                             'place_inherited': scope['inherited']['place'],
                             'hint': scope['time']['note']}})
    # **不预检索、不预注入判据**：按问题检索判据挑"该用哪条标准"实测不稳（会把相邻技能的标准当成本题的标准，
    # 例如把「项目执行进度异常」当成「审批积压」）。判据改成让模型在循环里用 kb_search 工具自己查 ——
    # 需要判断就去查，查不到就不下判断（见回答契约第 7 条）。
    # 问题也不改写：补全只补地名/名称/时间（上面的 name_fix 与 time_scope）。
    t_loop = time.time()
    # 问题为准；补全结果只作参考，由模型自己判断适不适用
    user_content = question
    # 已摘要覆盖的轮次由【本场会话摘要】代表，明细里就不再重复带 ——
    # 否则同一件事讲两遍，既白烧 token，又可能让摘要与明细互相打架。
    # ★ 但**最近 N 轮例外**：紧跟上一轮的追问（「那售电量呢」）看原话比看摘要可靠，
    #   实测只靠摘要时 history_injected 会是 0，追问的精细度下降。
    keep = max(0, int(getattr(config, 'SUMMARY_KEEP_RECENT', 2) or 0))
    always = history[-keep:] if (history and keep) else []
    hist_detail = [h for h in history
                   if int(h.get('seq_no') or 0) > sum_upto or h in always]
    if sess_sum.get('summary'):
        user_content += (
            '\n\n【本场会话摘要】（这场会话**更早轮次**的浓缩，仅作背景）\n'
            + sess_sum['summary'] + '\n'
            '注意三点：① 这是背景信息，**既不是数据来源、也不是结论来源** —— 回答里的任何数字，'
            '以及任何结论（包括「未查到」「未生成工单」「处于草稿或审核状态」「数据缺失、已改用累计」'
            '这类状态判断），都必须以本轮 run_sql 的结果为准；**摘要里写过的也要重新查一遍**。'
            '② 若与本轮原话或下面【统计范围】冲突，**一律以本轮原话和【统计范围】为准**；'
            '③ 不要复述这段摘要。')
    if hist_detail:
        hl = []
        for i, h in enumerate(hist_detail, 1):
            hl.append('%d) 用户：%s' % (i, h.get('raw_question') or ''))
            hl.append('   系统：%s' % (h.get('answer') or '').replace('\n', ' '))
            hl.append('   当时口径：%s / %s' % (h.get('time_text') or '-', h.get('place') or '-'))
        user_content += (
            '\n\n【对话历史】（同一场会话最近的 %d 轮，**只用来判断本轮问题在问什么、省略了什么**）\n%s\n'
            '注意三点：① 历史里的数字是按**当时的口径**算出来的，不能当成本轮答案；'
            '本轮必须按下面【统计范围】重新查数。'
            '② 本轮的统计口径已在上面的【统计范围】里定好，**不要**因为历史而改变它。'
            '③ 不要在回答里复述或提及这段历史。' % (len(hist_detail), '\n'.join(hl)))
    # ⓪·6 检索**唯一规则文档**：本轮问句 + 上一轮的原始问句一起查。
    #      「是哪些项目？」这类跟在提醒后面的追问，光看问句没有线索，拼上上一轮才有
    #      （实测：只拿问句检索会跑偏，拼上上一轮标题后正确规则回到第一）。
    #      命中的切片随后当本次的判断标准与回答方向。
    rule_hits, rule_best, rule_query = [], 0.0, question
    if config.RULES_ON:
        if history:
            _prev_q = (history[-1].get('raw_question') or '').strip()
            if _prev_q:
                rule_query = question + ' ' + _prev_q
        _t_rule = time.time()
        rule_hits, rule_best = rule_slices(rule_query)
        trace.append({'seq': len(trace) + 1, 'kind': 'rules', 'tool': 'rule_search',
                      'ms': int((time.time() - _t_rule) * 1000),
                      'args': {'query': rule_query, 'doc': config.RULE_DOC},
                      'result': {'hits': len(rule_hits), 'best_score': round(rule_best, 4),
                                 'titles': [(n.get('title') or '')[:60] for n in rule_hits]}})

    # 统计范围必须明确交给模型：六项指标是按「范围 + 期间」预计算的，
    # 不给这一句，它就得自己猜该取 month 还是 yearToDate、该取哪个月。
    user_content += ('\n\n【统计范围】（已按今天日期算好，直接采用，不要自行推算日期）\n'
                     + scope['scope_text'] + _scope_detail(scope['place']))
    if fixed['hint']:
        # 名称被归到「地名层」时（库里没有这个精确名），必须告诉模型用前缀查，
        # 否则它很可能拿合成名做等值匹配，一条都查不到还以为是空数据。
        user_content += ('\n\n【名称归一说明】' + fixed['hint'])
    # 三层注入，按题型分流：
    #   ① COMMON         —— 口吻 / 格式 / 出处 / 清单纪律，**所有题都带**
    #   ② ANALYSIS_FRAME —— 怎么分析（分层、拆解方法、回答模板），**只有分析题带**
    #   ③ CALIBER        —— 业务口径铁律（状态分组、实际投入…），**所有题都带**
    # 分流判据 = 规则文档检索相关度（业务问题的"分析性"能从规则命中里看出来）。
    # 检索失败（百炼不通，内网/代理环境常见）时 rule_hits 为空 —— 这时**不能当查数题**，
    # 否则一场基础设施故障会把所有分析题静默降级成“念数据”（实测就是这样）。默认按分析注入。
    _is_analysis = (not rule_hits) or (rule_best >= config.ANALYSIS_SCORE)
    user_content += '\n\n' + COMMON
    if _is_analysis:
        user_content += '\n' + ANALYSIS_FRAME
    user_content += '\n' + CALIBER
    if _is_analysis:
        # 交付前自检（放在提问的最后一屏，最贴近生成点）：
        # 这是"分析问答"的质量闸 —— 实测缺的从来不是数据，而是"建议 / 依据 / 预计效果"三段。
        user_content += (
            '\n\n【交卷前自检一遍，缺哪段就补哪段】\n'
            '0. **这题是哪一类？**（查数 / 分析 / 预测 / 方案）—— 先判准题型，再决定写哪几段：'
            '**查数题（只问一个数 / 差值 / 名单 / 排第几）就只给「数 + 口径」1–3 行**，'
            '**不要**再加建议、成效、下一步、做到什么程度、需审定这些段落（多写一段就是错）；'
            '分析 / 预测一般不超过 8 行、方案不超过 10 行，超了砍明细（见《规则》20.0）。\n'
            '1. 结论有没有？第一句就要给判断（好/差、达标/不达标、是/不是、哪几个）。\n'
            '2. 依据有没有？每条结论后面跟着数字，并说清"跟谁比、怎么算出来的"。\n'
            '3. 原因有没有？说清是哪几个数据共同造成的，不要只罗列。\n'
            '4. **建议有没有？必须落到「谁 · 做什么 · 多少 · 什么时候」** —— 四样缺一样就是没答完；'
            '**不许**写「要加强」「需关注」「尽快审批」这类没有对象、没有量、没有做法的话。\n'
            '5. **做完能达到什么程度？必须给数**（指标到 X、额度到 Y 万元、排名到第 Z、风险降到什么档）。'
            '要推算就**先去找参照**（往年 / 同类投了多少、投完指标动了多少），按单位投入的效果推；'
            '**不许一句「系统里没有记录」就收尾** —— 放宽一档也要找（同类所 → 同县 → 全市）。标「推算」。\n'
            '6. 有没有夹带思考过程、英文句、Markdown 符号？有就删掉。\n'
            '7. 要点里要求的「差多少 / 是几倍 / 占百分之几」要**自己算出来写在正文里**，'
            '不能只把两头的数字摆着让领导自己算。\n'
            '8. 有没有重复？同一件事不要在开头说一遍、分条里再说一遍。\n'
            '9. **本轮查的所有 SQL，时间 / 范围 / 状态条件是不是同一套**（含 JOIN 的子查询）？'
        '有没有一条带年份、一条不带，然后把**历年累计**当成当年数比出来的「超支 / 缺口」？有就统一条件重查。\n'
        '10. **问句里问了不止一件事吗**（「分了多少还剩多少」「新增几个、批了几个」）？'
        '**逐问逐答**，一件一件回答，不许只答前半句。\n'
        '11. **有没有出现「XX% 基准线 / 达标线」这类内部换算**？有就删掉 —— '
        '要么给「已过时间比例」和「当前占比」两个数让人自己看，要么只说「还差多少」（这是硬规则）。\n'
        '12. **本轮用到的数字是不是上一轮查的**？如果是（本轮没再查），要么本轮重查一次，'
        '要么在回答里写明「沿用上一轮口径」—— 不许默认读者知道这个数从哪来。\n'
        '14. **把回答里的数字全删掉，剩下的话还有信息量吗？** 如果只剩「建议尽快审批 / 加快花钱 / 加强治理」，'
        '说明你在复述数据 —— 重写：给出该做什么、怎么做、做完到什么程度（见《规则》二十二节）。\n'
        '15. **有没有废话、重复、套话？** 一个事实只说一遍；结论 3 行内说清；'
        '「综上所述」「需进一步关注」「持续跟踪」这类一律删掉。\n'
        '13. **回到【各类问题的必答项】，找到你这类问题的那一行，把 ①②③… 逐项对一遍**'
            '（不要整体感觉"都有"，要一项一项确认），缺哪项补哪项 —— 这是交卷前最后一道关。\n')
    if rule_hits:
        parts = []
        for _n in rule_hits:
            _t = (_n.get('title') or '').strip()
            _c = (_n.get('content') or '').strip()
            parts.append(('【' + _t + '】\n' if _t else '') + _c)
        user_content += ('\n\n【本次的规则】（从《%s》检索到的**候选**切片。'
                         '若本题要下判断 / 比大小 / 找原因 / 看趋势（分析题），'
                         '**必须先用 kb_search 把你要用的那一节原文取全再作答**；'
                         '只问一个数或一份清单（查数题）直接答。'
                         '标准只认《%s》，里面没写的不许自己编。）\n'
                         % (config.RULE_DOC, config.RULE_DOC) + '\n---\n'.join(parts))
    else:
        user_content += ('\n\n【判断标准从哪来】要做判断（是否异常/逾期/超阈/合理、该先办哪个）之前，'
                         '**先用 kb_search 查《%s》**；查到了就按它判，查不到就只报事实并说明'
                         '「系统里没有这类判定标准」。**不许拿别的标准套到本题上，也不许自己编标准。**'
                         % config.RULE_DOC)
    if profile:
        user_content += ('\n\n【提问人身份与数据范围】（判断标准与关注重点按它来，不要因此增减问题里已经要求的必答项）：\n' + profile)
    # 上游强制范围不在这里再注入一遍：它已经在【统计范围】的地点行里写明「强制按此范围取数」，
    # 口径只留一个出处（以前这里注入的是 scope 这个 dict 的 repr，纯属噪声）。
    messages = [{'role': 'system', 'content': _system(today)}, {'role': 'user', 'content': user_content}]
    answer = ''
    nudged = False
    blocked = False
    step = 0
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
                list(messages) + [{'role': 'user', 'content':
                                  '时间/步数预算到了，请**用已查到的数据**给出结论；'
                                  '如果还缺关键前提（例如具体是哪张单、哪个所），'
                                  '就在回答里一句话说清需要补什么，不要再查。'}] if _over_time else messages,
                model)
            trace.append({'seq': len(trace) + 1, 'kind': 'final', 'content': _why + '，强制出正文'})
            break
        t0 = time.time()
        # 查数轮：关思考（省内心独白）+ tool_choice=required（不许写散文，只准发工具调用）。
        # 实测同一轮：auto 会写 240~3400 字过程散文（一轮 10~17s），required 只有 56 token / 1.7s。
        # 收尾靠 finish 工具，不靠"模型自己停下来顺手写答案"。
        resp = _chat(messages, model, think=False, max_tokens=600)
        llm_ms = int((time.time() - t0) * 1000)
        msg = resp['choices'][0]['message']
        calls = msg.get('tool_calls') or []
        if not calls:
            # 这一轮的正文只用于「有没有瞎下结论」的判定；**不当最终答案**：
            # 查数轮是关思考跑的，它的 content 里可能混着"我已经收集了…让我汇报"这类过程话术
            #（那正是领导看到思考过程的原因）。真正的答案由下面 _final_answer 另起一次调用写。
            answer = (msg.get('content') or '').strip()
            used_tool = any(t.get('kind') == 'tool' for t in trace)
            # 硬约束：一个工具都没调就下结论，先要求它核实一次；仍不调则直接拦下，不当答案返回。
            if not used_tool and not nudged:
                nudged = True
                trace.append({'seq': len(trace) + 1, 'kind': 'nudge', 'model_ms': llm_ms,
                              'content': '模型未调用任何工具就想作答，已要求其先核实'})
                messages.append({'role': 'assistant', 'content': answer})
                messages.append({'role': 'user', 'content':
                                 '你这一轮没有调用任何工具。请先用工具核实事实（查知识库口径 + 查业务库数据），再给出结论；'
                                 '如果确实不需要任何工具，也请先说明理由并至少调用一次工具确认。'
                                 '另外：需要查多个互不依赖的数时，请把要用到的 run_sql **一次发出**，'
                                 '不要一条一条分多轮来。'
                                 '不要在回答里提到这条提示，也不要复述你的工具调用过程。'})
                continue
            if not used_tool:
                # 提醒过一次后仍不调工具：只要它给的不是业务数字（纯拒答），就放行；给了数字才拦。
                if nudged and not _CLAIM.search(answer):
                    note = '本轮未调用任何工具，以下为模型基于业务字典的判断'
                    answer = '（' + note + '）' + answer
                    trace.append({'seq': len(trace) + 1, 'kind': 'answer', 'model_ms': llm_ms,
                                  'content': answer[:600], 'note': note})
                    break
                blocked = True
                trace.append({'seq': len(trace) + 1, 'kind': 'blocked', 'model_ms': llm_ms,
                              'content': '未调用任何工具即下结论，已拦下。模型原文：' + answer[:300]})
                answer = ('【未核实，不予作答】这一轮没有调用任何工具去核对事实，结论不可信，已拦下。'
                          '请重试，或把问题问得更具体一些。')
                break
            # 数据查完了：**另起一次「写汇报」调用**（不给工具、明确要求写正文）。
            # 关思考的查数轮省下的时间，全花在这一步上——只让它写正文，不让它继续想。
            answer = _final_answer(messages, model)
            llm_ms = int((time.time() - t0) * 1000)
            trace.append({'seq': len(trace) + 1, 'kind': 'answer', 'model_ms': llm_ms,
                          'content': answer[:600]})
            break
        keep = {'role': 'assistant', 'content': msg.get('content') or '', 'tool_calls': calls}
        messages.append(keep)
        # 不设步数上限，改用周期性软提醒，防止一直翻表不收手
        if step in (2, 4, 6, 8):
            messages.append({'role': 'user', 'content':
                             '第 %d 轮了。**现在对照【各类问题的必答项】逐项点一遍手上的数据**：'
                             '要分类计数的（各类各几个、分别是谁）、要每类判定数据的（条数/卡在哪个节点/停留几天）、'
                             '要多期数值或同期对比的、要预计完工时间或预计指标的 —— **缺哪一项就再查哪一项**，'
                             '不要拿"大概齐"的数据去凑。都齐了就直接给结论（不要再重复查已经查过的数）。'
                             '不要在回答里提到本条提示。' % step})
        # finish 是收尾信号：同轮里真实查询先跑完，再另起一次「写汇报」调用
        fins = [c for c in calls if ((c.get('function') or {}).get('name')) == 'finish']
        live = [c for c in calls if ((c.get('function') or {}).get('name')) != 'finish']
        outs = _exec_calls(live) if live else []
        # 每轮一行日志：单题跑超 10 分钟时，只有这里能看出是「轮次太多」还是「某次调用卡住」
        # （实测场景 17 第 1 问、场景 22 前两问都出现过 600 s 无响应，日志里什么都没有）。
        print('[agent] 第 %d 轮：工具 %d 次，本轮模型 %d ms，累计 %.1f s'
              % (step, len(live), llm_ms, time.time() - t_all), flush=True)
        for tc, o in zip(live, outs):
            trace.append({'seq': len(trace) + 1, 'kind': 'tool', 'tool': o['name'], 'args': o['args'],
                          'ms': o['ms'], 'model_ms': llm_ms, 'round': step, 'result': o['result']})
            _body = _trim(o['name'], o['result'])
            if o['name'] == 'run_sql':
                _sql = (o.get('args') or {}).get('sql')
                _body += _time_guard(_sql, scope) + _scope_guard(_sql, scope) + _unit_guard(_sql)
            messages.append({'role': 'tool', 'tool_call_id': tc['id'], 'content': _body})
        if fins:
            # 模型自己说「数据够了」→ 由系统另起一次调用写正文（不用它这轮的 content，那是散文草稿）
            answer = _final_answer(messages, model)
            llm_ms = int((time.time() - t0) * 1000)
            trace.append({'seq': len(trace) + 1, 'kind': 'answer', 'model_ms': llm_ms,
                          'content': answer[:600], 'note': '模型调用 finish，已另起一次写汇报'})
            break
    # 输出清洗：把模型漏进正文的 Markdown 符号、过程话术、英文思考句洗掉
    # （实测 dsv4 禁不掉，只能代码层兜）。**必须在 qtype 与页面之前** —— 页面是从 answer 生成的，
    # 洗晚了页面里还是脏的。
    answer = _clean_answer(answer)
    # 数字闸（对账）：回答里的金额必须能在**本轮** SQL 结果或其确定性派生里找到出处。
    # 找不到就带着清单重问一次；第二次仍对不上就保留「错得更少」的那版，并把对账结果记进 trace。
    _queried = any(isinstance(t, dict) and t.get('tool') == 'run_sql' for t in trace)
    if config.NUMBER_GUARD_ON and _queried and not blocked:
        _pool = _num_pool(trace)
        _bad1 = _untraceable_money(answer, _pool)
        if _bad1:
            _msgs = list(messages) + [
                {'role': 'assistant', 'content': answer},
                {'role': 'user', 'content':
                 '对账不通过：回答里这些金额在你本轮查询结果里找不到出处 —— %s 万元。'
                 '请只使用你本轮查到的数（合计/均值取结果里的 summary），逐条改正后重写正文；'
                 '不要改口径、不要新增没查过的数，也不要提这条提示。' % '、'.join(str(x) for x in _bad1[:8])}]
            try:
                _ans2 = _final_answer(_msgs, model)
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
                _cans2 = _final_answer(_cmsgs, model)
            except Exception:                                  # noqa: BLE001
                _cans2 = ''
            _coef2 = _coefficient_talk(_cans2)
            if _cans2 and not _coef2:
                answer = _cans2
            trace.append({'seq': len(trace) + 1, 'kind': 'coef_guard', 'before': _coef1,
                          'after': _coef2,
                          'kept': 'rewrite' if (_cans2 and not _coef2) else 'original'})
    # qtype：**只用来决定要不要出分析页面**（前端要页面可以直接传 want_page）。
    # 真正的分流由模型按《规则》第零节做（分析题走五步、查数题取到数直接答）。
    # 这里取两个"实际发生的事"的信号，任一成立即算分析：
    #   ① 循环里自己查过《规则》（kb_search）—— 说明它认定这题要按规则判；
    #   ② 服务预检索到的那条规则相关度够高（ANALYSIS_SCORE；实测分析题普遍 ≥0.8、
    #      纯查数题普遍 <0.8，因为查数题在规则文档里本来就没有对应的判断条目）。
    qtype = '分析' if (
        _is_analysis                                        # 与注入判据对齐：注入了分析框架就算分析题
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
                                   force_scope or scope['place'])
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
        'rules_loaded': len(rule_hits),      # 本次命中了几条《规则》，0 = 没检索到
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
        'steps': len(trace),
        'history_used': len(history),
        'history_injected': len(hist_detail),
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
