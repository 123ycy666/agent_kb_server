# -*- coding: utf-8 -*-
"""问数线全链路：一次回答从进来到出去，被哪些输入影响 —— 按处理顺序全部列出，每项都能点开看原文。

页面「提示词 → 问数线」渲染 `segments()`；点开某一项用 `body(key)` 取原文。
只读层：这一页不改配置（可改的项另有编辑入口，`editor` 字段指过去）。
"""
from __future__ import annotations

import inspect
import json

EMPTY = '（现在没有内容）'


# ── 取数 ────────────────────────────────────────────────────────────
def _conn():
    import psycopg
    from psycopg.rows import dict_row

    from app.secretary import pgdb
    return psycopg.connect(pgdb.DSN, autocommit=True, row_factory=dict_row)


def _fetch(cur, sql, params=None):
    cur.execute(sql, params or ())
    return cur.fetchall() if cur.description else []


def _try_fetch(cur, *sqls):
    """按顺序试几条 SQL（表结构有分叉时用），全失败抛最后一条。"""
    last = None
    for sql in sqls:
        try:
            return _fetch(cur, sql)
        except Exception as exc:                     # noqa: BLE001
            last = exc
    raise last if last else RuntimeError('没有可用的 SQL')


# ── 结果形状 ────────────────────────────────────────────────────────
def _text(text, size=None, note=''):
    body = str(text or '')
    out = {'kind': 'text', 'text': body, 'size': size or ('%d 字' % len(body))}
    if note:
        out['note'] = note
    return out


def _rows(rows, size=None, note=''):
    """rows = list[dict]；值统一转成可显示字符串。"""
    list_rows, cols = [], []
    for r in rows:
        one = {}
        for k, v in (r or {}).items():
            if k not in cols:
                cols.append(k)
            if v is None:
                one[k] = ''
            elif isinstance(v, (dict, list)):
                one[k] = json.dumps(v, ensure_ascii=False, default=str)
            else:
                one[k] = str(v)
        list_rows.append(one)
    out = {'kind': 'rows', 'columns': cols, 'rows': list_rows,
           'size': size or ('%d 条' % len(list_rows))}
    if note:
        out['note'] = note
    return out


def _empty(why=''):
    return {'kind': 'text', 'text': EMPTY + (('\n' + why) if why else ''), 'size': '0 字'}


def _src(mod, name):
    fn = getattr(mod, name, None)
    if fn is None:
        return '（代码里找不到 %s）' % name
    try:
        return inspect.getsource(fn)
    except Exception as exc:                          # noqa: BLE001
        return '（读不到源码：%s）' % str(exc)[:120]


def _cfg(key, default=''):
    from app.secretary import prompt_text
    return prompt_text.get(key, default) or default


def _sections_text(slot):
    from app.secretary import prompt_material as pm
    rows = pm.sections(slot)
    if not rows:
        return '' , 0
    parts = []
    for r in rows:
        flag = '必带' if r.get('pinned') else '按需'
        parts.append('## %s（%s，%d 字）\n%s'
                     % (r.get('section'), flag, len(str(r.get('body') or '')),
                        str(r.get('body') or '').strip()))
    return '\n\n'.join(parts), sum(len(str(r.get('body') or '')) for r in rows)


# ── 探针：每项输入一个 ────────────────────────────────────────────────
def _p_route_rule(cur):
    rows = _fetch(cur, 'select k, body, version_no, updated_at from cfg.route_rule order by k')
    if not rows:
        return _empty('cfg.route_rule 没有行 —— 入口分流会一直走兜底（判成问数）。')
    txt = []
    for r in rows:
        txt.append('## %s（v%s）\n%s' % (r['k'], r.get('version_no'),
                                        json.dumps(r['body'], ensure_ascii=False, indent=2)
                                        if isinstance(r['body'], (dict, list)) else str(r['body'])))
    return _text('\n\n'.join(txt))


def _p_route_fallback(cur):
    from app.secretary import router
    return _text(_src(router, 'route'))


def _p_nc(table, label):
    def probe(cur):
        rows = _fetch(cur, 'select * from cfg.%s where deleted_flag = 0 limit 20' % table)
        total = _fetch(cur, 'select count(*) as n from cfg.%s where deleted_flag = 0' % table)
        n = int((total[0] or {}).get('n') or 0) if total else 0
        if n == 0:
            return _empty('cfg.%s 是空的，这张词典不起作用。' % table)
        return _rows(rows, size='%d 条（列前 20）' % n,
                     note='%s：纠错时按这张表把问句里的说法改对。' % label)
    return probe


def _p_nc_engine(cur):
    from app.secretary import name_fix
    return _text(_src(name_fix, 'fix'))


def _p_intent(cur):
    body = _cfg('intent')
    if not body.strip():
        return _text(EMPTY + '\n框是空的 = 一律走预处理（纠错 + 补问法 + 补全表 + 问题确定）。\n'
                            '框里只认「不走」这个词；判不出 / 调用失败也一律走。',
                     size='0 字 · 空 = 全走')
    return _text(body)


def _p_route_bools(cur):
    from app.secretary import preprocess
    return _text(_src(preprocess, 'route'))


def _p_askfill(cur):
    raw = _cfg('askfill')
    if not raw.strip():
        return _empty('askfill 空 = 不补问。')
    try:
        return _text(json.dumps(json.loads(raw), ensure_ascii=False, indent=2))
    except Exception:                                 # noqa: BLE001
        return _text(raw, note='这段不是合法 JSON，代码会按空处理（= 不补问）。')


def _p_confirm(cur):
    body = _cfg('confirm')
    if not body.strip():
        return _text(EMPTY + '\n框是空的 = 不归整问题，纠错后的原话直接往下传。', size='0 字')
    return _text(body)


def _p_complete(cur):
    from app.secretary import complete_rules
    rows = [dict(r) for r in (complete_rules.rows() or [])]
    if not rows:
        return _empty('补全表 0 条 —— 预处理命不中任何条目。')
    return _rows([{'kind': r.get('kind'), 'match_text': r.get('match_text'),
                   'fill_text': r.get('fill_text'), 'priority': r.get('priority'),
                   'enabled': r.get('enabled')} for r in rows],
                 size='%d 条' % len(rows),
                 note='命中后只作参考：改不改问句由「问题确定」那一步定。')


def _p_today(cur):
    import time
    now = time.strftime('%Y-%m-%d')
    return _text(
        '今天 = %s（接口传 today / business_date，没传就取系统当天）\n\n'
        '它进了两处，两处都靠它推月份：\n'
        '1. 送进模型的 user 消息最后一行：【今天】%s\n'
        '2. 第 ⑤ 步「问题确定」的 ask_time —— 框里没写时间时默认补「上一个自然月」\n\n'
        '代码：server/app/secretary/agent.py:1410（_now = today or 系统日）、1417（confirm.run 传 ask_time）' % (now, now),
        size='1 行')


def _p_identity(cur):
    body = _cfg('identity')
    if body.strip():
        return _text(body, note='页面上覆盖过的身份（cfg.prompt_text.identity）。')
    from app.secretary import prompts_data
    return _text(prompts_data.IDENTITY,
                 note='没有覆盖 → 用代码里的默认身份（prompts_data.IDENTITY）。')


def _p_modes(cur):
    raw = _cfg('modes')
    if raw.strip():
        return _text(raw, note='页面覆盖的模式做法（JSON）。问数线固定取 general 那一段。')
    from app.secretary import prompts_data
    return _text(json.dumps(prompts_data.MODE_TEXTS, ensure_ascii=False, indent=2),
                 note='没有覆盖 → 用代码默认的四段模式做法。')


def _p_redlines(cur):
    body = _cfg('redlines')
    if not body.strip():
        return _text(EMPTY + '\nS0 红线框是空的 —— 这一轮 system 里没有红线段。', size='0 字')
    return _text(body)


def _p_priority(cur):
    body = _cfg('priority')
    if not body.strip():
        return _text(EMPTY + '\nS1「谁说了算」框是空的 —— 几份规则撞车时没有优先级声明。', size='0 字')
    return _text(body)


def _p_personal_rules(cur):
    rows = _try_fetch(cur,
                      "select d.filename, d.doc_type, d.chunk_count, "
                      "coalesce(sum(length(c.content)), 0) as chars "
                      "from kb.document d left join kb.document_chunk c on c.document_id = d.id "
                      "where d.doc_type = 'rule' and coalesce(d.is_active, true) "
                      "group by d.id, d.filename, d.doc_type, d.chunk_count order by d.id desc limit 30",
                      "select filename, doc_type, chunk_count, 0 as chars from kb.document "
                      "where doc_type = 'rule' limit 30")
    if not rows:
        return _empty('个人资料库里还没有 doc_type=rule 的文档 —— S7「我的规则」这一段不注入。')
    return _rows(rows, size='%d 篇' % len(rows),
                 note='每轮注入，整段上限 1500 字；超了会标 overflow。')


def _p_persona(cur):
    rows = _fetch(cur,
                  "select session_id, persona_name, left(persona_prompt, 300) as persona_prompt, "
                  "last_msg_at from qa.t_chat_session "
                  "where coalesce(persona_prompt, '') <> '' order by last_msg_at desc nulls last limit 10")
    if not rows:
        return _empty('没有任何会话设过人设 —— L4 这一段本轮不注入。')
    return _rows(rows, size='%d 场' % len(rows),
                 note='会话级人设：员工在某一场对话里自己写，只影响那一场。')


def _p_prefs(cur):
    rows = _fetch(cur, 'select user_id, prefs, updated_at from base.t_user_pref order by user_id')
    if not rows:
        return _empty('没有人设过个人偏好 —— L6 这一段不注入。')
    return _rows(rows, size='%d 人' % len(rows),
                 note='键：正文/pt 字号、中文字体等；出文件时的版式也读它。')


def _p_tools_guide(cur):
    from app.secretary import prompt_text
    body = _cfg('tools')
    if body.strip():
        return _text(body, note='页面覆盖的工具说明。')
    return _text(prompt_text.DEFAULT_TOOLS_GUIDE,
                 note='没覆盖 → 用代码默认工具说明；只在这一轮给了工具时才注入。')


def _p_tool_defs(cur):
    from app.secretary import agent
    parts = ['共 %d 只。这是模型看到的那份（name / description / parameters）。' % len(agent.TOOLS)]
    for t in agent.TOOLS:
        f = t.get('function') or {}
        parts.append('## %s\n%s\n\nparameters: %s'
                     % (f.get('name'), str(f.get('description') or '').strip(),
                        json.dumps(f.get('parameters') or {}, ensure_ascii=False, indent=1)))
    return _text('\n\n'.join(parts), note='description 决定它怎么用；改它要改代码。')


def _p_tool_subset(cur):
    from app.secretary import agent
    names = [((t.get('function') or {}).get('name')) for t in agent.TOOLS]
    return _text(
        '三个布尔由第 ② 步（意图判据 + 历史 + 有没有带文件）给出，代码按它切工具子集：\n\n'
        'use_db=是    → kb_search, list_metrics, metric_query, run_sql, read_result, finish\n'
        'use_file=是  → kb_search, kb_read, read_file, run_code\n'
        'make_file=是 → kb_search, make_file, run_code\n'
        '三个都不是（闲聊）→ 一只工具都不给\n\n'
        '并集去重后按下单顺序取，工具表里现在有 %d 只：%s\n\n'
        '代码：server/app/secretary/agent.py:1568-1577（_want）' % (len(names), '、'.join(n or '' for n in names)),
        size='%d 只' % len(names))


def _p_material(slot):
    def probe(cur):
        body, chars = _sections_text(slot)
        from app.secretary import prompt_material as pm
        name = pm.SLOT_NAMES.get(slot, slot)
        if not chars:
            return _empty('cfg.prompt_material 的 slot=%s 没有启用的节 —— 这一段模型收不到。' % slot)
        return _text(body, size='%d 字 / %d 节' % (chars, len(pm.sections(slot))),
                     note='%s。这里是全部小节；实际注入只取「必带」的节。' % name)
    return probe


def _p_material_index(cur):
    from app.secretary import prompt_material as pm
    idx = pm.index_text(pm.SLOTS) if hasattr(pm, 'index_text') else ''
    if not str(idx).strip():
        return _empty('没有可列的清单（四份材料都没有节）。')
    return _text(str(idx), note='接在材料段末尾，让模型知道还有哪些没带上、要不要提前取。')


def _p_user_msg(cur):
    return _text(
        '送进模型的那条 user 消息按这个顺序拼（代码里就是这几段字符串相加）：\n\n'
        '【本轮要办的事】\n'
        '  = 第 ⑤ 步「问题确定」的定稿；那一步没走成就是原话\n'
        '【今天】YYYY-MM-DD\n'
        '【数据时间】各表最新可用期间（代码现算，见下一项）\n'
        '【提问人身份与数据范围】（接口传了 profile 才有）\n'
        '【本轮带来的文件】/【用户上传的文件】+ 四条纪律（只有带文件才有；纪律写死在这里）\n\n'
        '代码：server/app/secretary/agent.py:1425-1434（基础四段）、1474（身份与范围）、1479-1505（文件）',
        size='6 段')


def _p_data_time(cur):
    from app.secretary import semantic
    rows = []
    try:
        tables = sorted(semantic.allowed_tables())
    except Exception as exc:                          # noqa: BLE001
        return _text('算不出来（按不注入处理）：%s' % str(exc)[:200], size='0 张')
    for t in tables:
        try:
            pf = semantic.period_facts(t) or {}
        except Exception:                             # noqa: BLE001
            pf = {}
        if pf.get('col') and pf.get('max'):
            rows.append({'表': t, '期间列': pf.get('col'), '最新可用期间': str(pf.get('max'))})
    if not rows:
        return _empty('没有一张表能从字典里认出期间列 —— 【数据时间】这一行不注入。')
    return _rows(rows, size='%d 张表' % len(rows),
                 note='用户没说时间时按它取；比它晚的期间直接答「数据未就绪」。')


def _p_profile(cur):
    return _text(
        '接口参数 profile（提问人身份与数据范围）与 scope（上游强制的数据范围）。\n'
        '页面上看不到一次真实请求的值 —— 这里只说明它在哪、怎么用：\n\n'
        'profile 非空 → 追加到 user 消息：【提问人身份与数据范围】（判断标准与关注重点按它来，不扩大问题范围）\n'
        'scope 非空 → 强制这一轮的数据范围，并在页面缓存的键里参与去重\n\n'
        '代码：server/app/secretary/agent.py:1298（force_scope）、1474-1475（profile 注入）',
        size='2 个参数')


def _p_doc_files(cur):
    return _text(
        '本轮带来的文件清单（doc_files），以及写死的四条纪律：\n\n'
        '1 只依据文件内容回答；文件里没有的写「文件中未提及」，不许用外部常识、经验或业务库补。\n'
        '2 时限 / 金额 / 比例 / 责任单位必须能在文件里找到原句，引用时标「（见：章节路径）」。\n'
        '3 给的是节选（标了「全文约 N 字，节选 M/K 段」）时不许说「全文只有这些」。\n'
        '4 本次不需要查业务库；只有明确要求「和系统里的数据比对」时才 run_sql。\n\n'
        '正文不灌进 user 消息：只给清单 + file_id，模型自己 read_file 分页读。\n'
        '代码：server/app/secretary/agent.py:1479-1505',
        size='4 条纪律')


def _p_semantic(cur):
    from app.secretary import semantic
    cat = semantic.catalog() or {}
    tables = cat.get('tables') or []
    cols = cat.get('cols') or {}
    counts = cat.get('counts') or {}
    if not tables:
        return _empty('业务字典读不到 —— 表名翻中文、找字段这一步整个失效。')
    rows = [{'表名': t.get('table_name'), '中文名': t.get('table_cn_name'),
             '说明': t.get('business_desc'), '字段数': len((cols.get(t.get('table_name')) or {})),
             '行数': counts.get(t.get('table_name'))} for t in tables]
    ov = len(cat.get('overrides') or [])
    return _rows(rows, size='%d 张表' % len(rows),
                 note='表名/字段中文名唯一来源是 MySQL ai_table_metadata；覆盖层 %d 条优先。' % ov)


def _p_metric(cur):
    rows = _fetch(cur, 'select code, label, unit, digits, direction, expr, notes, enabled '
                       'from cfg.caliber_metric order by code')
    if not rows:
        return _empty('cfg.caliber_metric 空 —— list_metrics / metric_query 就没东西可按口径算。')
    return _rows(rows, size='%d 条' % len(rows), note='指标公式；metric_query 直接按它算，模型不写 SQL。')


def _p_caliber_base(cur):
    rows = _fetch(cur, 'select metric_code, table_name, column_name, agg, scope_col, must '
                       'from cfg.caliber_base order by metric_code, table_name')
    if not rows:
        return _empty('cfg.caliber_base 空 —— 命中契约表时没有「必需过滤」可校验。')
    return _rows(rows, size='%d 条' % len(rows),
                 note='must = 必需过滤条件；SQL 缺了就被 caliber.check_sql 拒。')


def _p_caliber_echo(cur):
    from app.secretary import agent
    return _text(_src(agent, '_caliber_echo'),
                 note='每一步查数前把「查数口径」原文再贴一次到当前轮。')


def _p_sql_guard(cur):
    from app.secretary import config, semantic, tools_db
    try:
        names = sorted(semantic.allowed_tables())
    except Exception:                                 # noqa: BLE001
        names = []
    return _text(
        '## safe_select（代码）\n' + _src(tools_db, 'safe_select') +
        '\n\n## 表白名单（现算 %d 张）\n%s' % (len(names), '、'.join(names) or '（空）') +
        '\n\n行数上限 SQL_MAX_ROWS=%d（没写 limit 就自动加）；超时 SQL_TIMEOUT_MS=%d；'
        'FORBID 拦分号与注释；只允许 select/with 开头。'
        % (config.SQL_MAX_ROWS, config.SQL_TIMEOUT_MS),
        size='%d 张表' % len(names))


def _p_delivery(cur):
    from app.secretary import config, tools_result
    return _text(
        '## tools_result.deliver（代码）\n' + _src(tools_result, 'deliver') +
        '\n\n## 参数\n单次工具结果给模型的字数上限 TOOL_RESULT_CHARS=%d；'
        '超了不砍内容，落盘 + 给骨架，用 read_result 接着取（一次 READ_RESULT_CHARS=%d）。'
        % (config.TOOL_RESULT_CHARS, config.READ_RESULT_CHARS),
        size='%d 字/次' % config.TOOL_RESULT_CHARS)


def _p_loop_params(cur):
    from app.secretary import config
    return _text(
        '模型：%s（通道 %s）\n'
        'temperature=0（写死）\n'
        '查数轮 think=False（关思考，防内心独白写进正文）\n'
        'max_tokens=%s（0 = 不带，由网关给上限）\n'
        'MAX_STEPS=40（只防死循环）\n'
        'QUESTION_BUDGET_S=%s（0 = 不限）\n'
        '打转判据：连续 2 轮结果指纹完全一样 → 收尾\n'
        '工具结果行数上限 SQL_MAX_ROWS=%d\n'
        'HTTP_TIMEOUT=%d 秒（超时重试一次，放宽 90 秒）\n'
        '这些都在 server/app/secretary/config.py 与 agent.py:313（_chat）里，页面上改不了。'
        % (config.MODEL, config.MODEL_CHANNEL, config.LLM_MAX_TOKENS or '不设',
           config.QUESTION_BUDGET_S or '不限', config.SQL_MAX_ROWS, config.HTTP_TIMEOUT),
        size='9 项')


def _p_evidence(cur):
    from app.secretary import evidence
    return _text(_src(evidence, 'text'),
                 note='写正文前把本轮查到的原始结果摊成一份证据包，正文的数字只许从这里来。')


def _p_verify(cur):
    from app.secretary import caliber
    return _text(_src(caliber, 'verify'),
                 note='正文里的数在证据包里找不到出处 → 带着差异重写一次。')


def _p_clean(cur):
    from app.secretary import agent
    return _text(_src(agent, '_clean_answer'),
                 note='输出清洗：漏进正文的 Markdown 符号、过程话术、英文思考句。')


def _p_page(cur):
    from app.secretary import config
    return _text(
        '分析页面：%s（PAGE_ON）；模型 %s。\n'
        '只在 qtype=分析 或接口传 want_page 时才另调一次模型生成单页 HTML；失败不影响回答。'
        % ('开' if config.PAGE_ON else '关', config.PAGE_MODEL),
        size='1 行')


def _p_log(cur):
    from app.secretary import config
    return _text(
        '这一条不影响回答，只记这一轮发生了什么：\n'
        'qa.t_chat_query 一轮一行；qa.t_chat_trace_step 每一步一行；qa.t_chat_message 每句一行。\n'
        '会话摘要：SESSION_SUMMARY=%s，每 %d 轮做一次，上限 %d 字，最近 %d 轮始终原样带；'
        '总轮数 < %d 不做摘要。\n'
        '摘要只在干活线注入；问数线只记账、不进提示词。'
        % ('开' if config.SESSION_SUMMARY else '关', config.SUMMARY_EVERY,
           config.SUMMARY_MAX_CHARS, config.SUMMARY_KEEP_RECENT, config.SUMMARY_MIN_TURNS),
        size='3 张表')


# ── 链路（顺序就是处理顺序）─────────────────────────────────────────
CHAIN = [
    {'key': 'route', 'name': '① 入口分流', 'items': [
        {'key': 'route_rule', 'name': '入口分流判据（cfg.route_rule）',
         'where': 'cfg.route_rule（JSON）', 'editable': False, 'takes_effect': '下一轮',
         'note': '判成问数 / 干活 / 闲聊；判错后面全错。', 'editor': '', 'probe': _p_route_rule},
        {'key': 'route_fallback', 'name': '判不出来时走哪（代码）',
         'where': 'server/app/secretary/router.py', 'editable': False, 'takes_effect': '改代码',
         'note': '读不到判据 / 抛错 → 一律走问数；/ask/data 直接跳过这一判。',
         'editor': '', 'probe': _p_route_fallback},
    ]},
    {'key': 'fix', 'name': '② 名称与错字纠错', 'items': [
        {'key': 'nc_catalog', 'name': '名称库（机构/站所/指标全名）',
         'where': 'cfg.t_nc_catalog', 'editable': False, 'takes_effect': '60 秒',
         'note': '所名、站名的规范写法与拼音；两万行，页面只列前 20。',
         'editor': '', 'probe': _p_nc('t_nc_catalog', '名称库')},
        {'key': 'nc_pinyin', 'name': '拼音表',
         'where': 'cfg.t_nc_pinyin', 'editable': False, 'takes_effect': '60 秒',
         'note': '同音字兜底；两万行，页面只列前 20。',
         'editor': '', 'probe': _p_nc('t_nc_pinyin', '拼音表')},
        {'key': 'nc_term', 'name': '业务术语表',
         'where': 'cfg.t_nc_term', 'editable': False, 'takes_effect': '60 秒',
         'note': '术语别名 → 规范说法。', 'editor': '', 'probe': _p_nc('t_nc_term', '术语表')},
        {'key': 'nc_typo', 'name': '错别字表',
         'where': 'cfg.t_nc_typo', 'editable': False, 'takes_effect': '60 秒',
         'note': '错字 → 正字，带频次与原因。', 'editor': '', 'probe': _p_nc('t_nc_typo', '错字表')},
        {'key': 'nc_wordlist', 'name': '词表（业务词/题型词）',
         'where': 'cfg.t_nc_wordlist', 'editable': False, 'takes_effect': '60 秒',
         'note': '判断这句话里有没有业务词、是不是在问数。',
         'editor': '', 'probe': _p_nc('t_nc_wordlist', '词表')},
        {'key': 'nc_ambiguous', 'name': '歧义词表',
         'where': 'cfg.t_nc_ambiguous', 'editable': False, 'takes_effect': '60 秒',
         'note': '同名不同义（A 所 vs B 站）→ 决定是改还是追问。',
         'editor': '', 'probe': _p_nc('t_nc_ambiguous', '歧义词表')},
        {'key': 'nc_engine', 'name': '纠错引擎怎么用这六张表（代码）',
         'where': 'server/app/secretary/name_fix.py', 'editable': False, 'takes_effect': '改代码',
         'note': '改对之后的写法就是后面所有步骤看到的问句。', 'editor': '', 'probe': _p_nc_engine},
    ]},
    {'key': 'pre', 'name': '③ 要不要预处理', 'items': [
        {'key': 'intent', 'name': '意图判据（intent）',
         'where': 'cfg.prompt_text.intent', 'editable': True, 'takes_effect': '下一轮',
         'note': '只认「不走」；空 / 判不出 / 失败一律走。', 'editor': 'text:intent',
         'probe': _p_intent},
        {'key': 'route_bools', 'name': '三个布尔怎么定（代码）',
         'where': 'server/app/secretary/preprocess.py', 'editable': False, 'takes_effect': '改代码',
         'note': 'use_db / use_file / make_file —— 决定下一项工具子集与材料。',
         'editor': '', 'probe': _p_route_bools},
    ]},
    {'key': 'askfill', 'name': '④ 补问法', 'items': [
        {'key': 'askfill', 'name': '补问词与话术（askfill）',
         'where': 'cfg.prompt_text.askfill（JSON）', 'editable': True, 'takes_effect': '下一轮',
         'note': '缺时间/地点/对象时问一句；"on": false 整段关掉。', 'editor': 'askfill',
         'probe': _p_askfill},
    ]},
    {'key': 'fill', 'name': '⑤ 补全表', 'items': [
        {'key': 'complete_rule', 'name': '补全表（complete_rule）',
         'where': 'cfg.complete_rule', 'editable': False, 'takes_effect': '下一轮',
         'note': '命中的默认问法；只作参考，改不改由问题确定定。', 'editor': '',
         'probe': _p_complete},
    ]},
    {'key': 'confirm', 'name': '⑥ 问题确定', 'items': [
        {'key': 'confirm', 'name': '问题确定规则（confirm）',
         'where': 'cfg.prompt_text.confirm', 'editable': True, 'takes_effect': '下一轮',
         'note': '把口语问句揉成一句可查的问题；空 = 原话往下传。', 'editor': 'text:confirm',
         'probe': _p_confirm},
        {'key': 'today', 'name': '今天（ask_time）',
         'where': '接口参数 today / business_date → agent.py:1410', 'editable': False,
         'takes_effect': '每轮', 'note': '框里没写时间时，靠它默认补「上一个自然月」。',
         'editor': '', 'probe': _p_today},
    ]},
    {'key': 'system', 'name': '⑦ 装配 system（10 段）', 'items': [
        {'key': 'identity', 'name': 'L1 身份',
         'where': 'cfg.prompt_text.identity → 缺省 prompts_data.IDENTITY', 'editable': True,
         'takes_effect': '下一轮', 'note': '它是谁、站在谁的位置说话。', 'editor': 'text:identity',
         'probe': _p_identity},
        {'key': 'modes', 'name': 'L2 模式做法（取 general）',
         'where': 'cfg.prompt_text.modes → 缺省 MODE_TEXTS', 'editable': True,
         'takes_effect': '下一轮', 'note': '这一轮按哪种做法干（问数线固定 general）。',
         'editor': 'text:modes', 'probe': _p_modes},
        {'key': 'redlines', 'name': 'S0 红线',
         'where': 'cfg.prompt_text.redlines', 'editable': True, 'takes_effect': '下一轮',
         'note': '不许做什么；空就不注入。', 'editor': 'text:redlines', 'probe': _p_redlines},
        {'key': 'priority', 'name': 'S1 谁说了算',
         'where': 'cfg.prompt_text.priority', 'editable': True, 'takes_effect': '下一轮',
         'note': '几份规则撞车时听谁；空就不注入。', 'editor': 'text:priority',
         'probe': _p_priority},
        {'key': 'personal_rules', 'name': 'S7 我的规则（个人库 doc_type=rule）',
         'where': 'kb.document（doc_type=rule）+ kb.document_chunk', 'editable': False,
         'takes_effect': '下一轮', 'note': '只对这个人生效的规矩；整段上限 1500 字。',
         'editor': '', 'probe': _p_personal_rules},
        {'key': 'persona', 'name': 'L4 本会话人设',
         'where': 'qa.t_chat_session.persona_name / persona_prompt', 'editable': False,
         'takes_effect': '下一轮', 'note': '员工在某一场对话里自己写，只影响那一场。',
         'editor': '', 'probe': _p_persona},
        {'key': 'prefs', 'name': 'L6 个人偏好',
         'where': 'base.t_user_pref.prefs', 'editable': False, 'takes_effect': '下一轮',
         'note': '口吻、字号、字体；出文件时版式也读它。', 'editor': '', 'probe': _p_prefs},
        {'key': 'tools_guide', 'name': '工具说明',
         'where': 'cfg.prompt_text.tools → 缺省 DEFAULT_TOOLS_GUIDE', 'editable': True,
         'takes_effect': '下一轮', 'note': '先 list_metrics 还是自己写 SQL、数组并发、分页读。',
         'editor': 'text:tools', 'probe': _p_tools_guide},
        {'key': 'tool_defs', 'name': '工具定义与参数 schema',
         'where': 'server/app/secretary/agent.py TOOLS', 'editable': False,
         'takes_effect': '改代码', 'note': '模型看到的那份；description 决定它怎么用。',
         'editor': '', 'probe': _p_tool_defs},
        {'key': 'tool_subset', 'name': '这一轮给哪几只（子集规则）',
         'where': 'server/app/secretary/agent.py:1568-1577（_want）', 'editable': False,
         'takes_effect': '改代码', 'note': '三个布尔决定给哪几只工具；给错了它就干不了。',
         'editor': '', 'probe': _p_tool_subset},
    ]},
    {'key': 'material', 'name': '⑧ 查数阶段的材料', 'items': [
        {'key': 'material_fetch', 'name': '查数口径（fetch）',
         'where': 'cfg.prompt_material（slot=fetch）', 'editable': True, 'takes_effect': '下一轮',
         'note': '模型写 SQL 唯一的表与字段来源；每一步还会重贴一次。',
         'editor': 'material:fetch', 'probe': _p_material('fetch')},
        {'key': 'material_judge', 'name': '判定标准（judge）',
         'where': 'cfg.prompt_material（slot=judge）', 'editable': True, 'takes_effect': '下一轮',
         'note': '这一阶段怎么判对错。', 'editor': 'material:judge', 'probe': _p_material('judge')},
        {'key': 'material_index', 'name': '没带上的规则清单（索引）',
         'where': 'prompt_material.index_text(SLOTS)', 'editable': False, 'takes_effect': '下一轮',
         'note': '接在材料段末尾，让它知道还有哪些可取。', 'editor': '',
         'probe': _p_material_index},
    ]},
    {'key': 'user_msg', 'name': '⑨ 送进模型的 user 消息', 'items': [
        {'key': 'user_msg_how', 'name': '拼装顺序（6 段）',
         'where': 'server/app/secretary/agent.py:1425-1505', 'editable': False, 'takes_effect': '改代码',
         'note': '本轮要办的事 → 今天 → 数据时间 → 身份范围 → 文件。', 'editor': '',
         'probe': _p_user_msg},
        {'key': 'data_time', 'name': '数据时间（各表最新可用期间）',
         'where': 'semantic.period_facts（现算）', 'editable': False, 'takes_effect': '每轮',
         'note': '没说时间时按它取；比它晚的期间直接答「未就绪」。', 'editor': '',
         'probe': _p_data_time},
        {'key': 'profile', 'name': '提问人身份与数据范围',
         'where': '接口参数 profile / scope', 'editable': False, 'takes_effect': '每轮',
         'note': '按谁的范围算、看哪几级。', 'editor': '', 'probe': _p_profile},
        {'key': 'doc_files', 'name': '本轮文件 + 四条纪律',
         'where': 'doc_files → 清单与 file_id（正文按需 read_file）', 'editable': False,
         'takes_effect': '每轮', 'note': '事实来源是文件还是库。', 'editor': '',
         'probe': _p_doc_files},
    ]},
    {'key': 'loop', 'name': '⑩ 查数循环', 'items': [
        {'key': 'semantic_dict', 'name': '业务字典 + 覆盖层',
         'where': 'MySQL ai_table_metadata + cfg.data_meta_override', 'editable': False,
         'takes_effect': '下一轮', 'note': '表名翻中文、找字段；字典是说明层不是白名单。',
         'editor': '', 'probe': _p_semantic},
        {'key': 'caliber_metric', 'name': '指标契约（怎么算）',
         'where': 'cfg.caliber_metric', 'editable': False, 'takes_effect': '下一轮',
         'note': 'metric_query 按它算，模型不写 SQL。', 'editor': '', 'probe': _p_metric},
        {'key': 'caliber_base', 'name': '口径底表与必需过滤',
         'where': 'cfg.caliber_base', 'editable': False, 'takes_effect': '下一轮',
         'note': '命中契约表却缺必需过滤 → SQL 被拒。', 'editor': '', 'probe': _p_caliber_base},
        {'key': 'caliber_echo', 'name': '口径回显（每步重贴）',
         'where': 'agent.py _caliber_echo', 'editable': False, 'takes_effect': '改代码',
         'note': 'system 里的口径在长对话里会被稀释，所以贴到当前轮。', 'editor': '',
         'probe': _p_caliber_echo},
        {'key': 'sql_guard', 'name': 'SQL 安全闸 + 表白名单',
         'where': 'tools_db.safe_select + semantic.allowed_tables', 'editable': False,
         'takes_effect': '改代码', 'note': '只读、禁分号注释、自动加 LIMIT 200。', 'editor': '',
         'probe': _p_sql_guard},
        {'key': 'delivery', 'name': '工具结果交付层',
         'where': 'tools_result.deliver + config', 'editable': False, 'takes_effect': '改配置',
         'note': '决定模型看得到几行、要不要 read_result 接着取。', 'editor': '',
         'probe': _p_delivery},
        {'key': 'loop_params', 'name': '运行参数（模型/步数/额度）',
         'where': 'server/app/secretary/config.py + agent.py:313', 'editable': False,
         'takes_effect': '改 .env 并重启', 'note': '快慢、截断、会不会答成「查数计划」。',
         'editor': '', 'probe': _p_loop_params},
    ]},
    {'key': 'answer', 'name': '⑪ 证据与作答', 'items': [
        {'key': 'evidence', 'name': '证据包（写正文只用它）',
         'where': 'server/app/secretary/evidence.py text()', 'editable': False,
         'takes_effect': '改代码', 'note': '原始结果直接摊出，不经模型转述。', 'editor': '',
         'probe': _p_evidence},
        {'key': 'material_answer', 'name': '作答规则（answer）',
         'where': 'cfg.prompt_material（slot=answer）', 'editable': True, 'takes_effect': '下一轮',
         'note': '出答案的格式与出处要求。', 'editor': 'material:answer',
         'probe': _p_material('answer')},
        {'key': 'material_check', 'name': '自检清单（check）',
         'where': 'cfg.prompt_material（slot=check）', 'editable': True, 'takes_effect': '下一轮',
         'note': '交卷前逐条过的清单。', 'editor': 'material:check', 'probe': _p_material('check')},
        {'key': 'material_list', 'name': '清单规则（list，读文件出清单时）',
         'where': 'cfg.prompt_material（slot=list）', 'editable': True, 'takes_effect': '下一轮',
         'note': 'use_file=是 且要出清单时才注入。', 'editor': 'material:list',
         'probe': _p_material('list')},
        {'key': 'material_file', 'name': '产物规则（file，出文件时）',
         'where': 'cfg.prompt_material（slot=file）', 'editable': True, 'takes_effect': '下一轮',
         'note': 'make_file=是 时才注入。', 'editor': 'material:file', 'probe': _p_material('file')},
        {'key': 'material_kb', 'name': '改库规则（kb）',
         'where': 'cfg.prompt_material（slot=kb）', 'editable': True, 'takes_effect': '下一轮',
         'note': '改知识库时才注入。', 'editor': 'material:kb', 'probe': _p_material('kb')},
        {'key': 'verify', 'name': '数字对账（对不上重写一次）',
         'where': 'server/app/secretary/caliber.py verify()', 'editable': False,
         'takes_effect': '改代码', 'note': '正文里的数在证据包里找不到 → 只改那几处再交。',
         'editor': '', 'probe': _p_verify},
        {'key': 'clean', 'name': '输出清洗',
         'where': 'agent.py _clean_answer', 'editable': False, 'takes_effect': '改代码',
         'note': '把 Markdown 符号、过程话术、思考句洗掉。', 'editor': '', 'probe': _p_clean},
    ]},
    {'key': 'after', 'name': '⑫ 收尾（不影响回答）', 'items': [
        {'key': 'page', 'name': '分析页面（另一次模型调用）',
         'where': 'config.PAGE_ON / PAGE_MODEL', 'editable': False, 'takes_effect': '改 .env 并重启',
         'note': '只在分析题或 want_page 时生成；失败不影响回答。', 'editor': '',
         'probe': _p_page},
        {'key': 'qa_log', 'name': '落库与摘要',
         'where': 'qa.t_chat_query / trace_step / message', 'editable': False,
         'takes_effect': '改 .env 并重启', 'note': '只记账；摘要问数线不注入。', 'editor': '',
         'probe': _p_log},
    ]},
]


def _index():
    return {it['key']: (seg, it) for seg in CHAIN for it in seg['items']}


def _run(seg, it, cur):
    try:
        p = it['probe'](cur)
    except Exception as exc:                          # noqa: BLE001 - 取不到也要能读
        p = {'kind': 'text', 'size': '读不到',
             'text': '%s\n\n读取失败：%s: %s' % (EMPTY, type(exc).__name__, str(exc)[:300])}
    return p


def _meta(seg, it, p):
    return {'key': it['key'], 'seg': seg['name'], 'name': it['name'],
            'where': it['where'], 'size': p.get('size') or '',
            'editable': bool(it['editable']), 'takes_effect': it['takes_effect'],
            'note': it['note'], 'editor': it['editor'], 'kind': p.get('kind') or 'text'}


def segments():
    """全链路：段 → 项，按处理顺序。每项带 key，点开用 body(key) 取原文。"""
    out = []
    try:
        conn = _conn()
    except Exception as exc:                          # noqa: BLE001
        return [{'key': 'error', 'name': '读不到配置库',
                 'items': [{'key': 'error', 'seg': '读不到配置库', 'name': '数据库连不上',
                            'where': 'PostgreSQL', 'size': '-', 'editable': False,
                            'takes_effect': '-', 'note': str(exc)[:200], 'editor': '',
                            'kind': 'text'}]}]
    try:
        cur = conn.cursor()
        try:
            for seg in CHAIN:
                items = [_meta(seg, it, _run(seg, it, cur)) for it in seg['items']]
                out.append({'key': seg['key'], 'name': seg['name'], 'items': items})
        finally:
            cur.close()
    finally:
        conn.close()
    return out


def body(key):
    """某一项的原文与说明。找不到返回 {'error': ...}。"""
    idx = _index().get(key)
    if not idx:
        return {'error': '未知的项：%s' % key}
    seg, it = idx
    conn = _conn()
    try:
        cur = conn.cursor()
        try:
            p = _run(seg, it, cur)
        finally:
            cur.close()
    finally:
        conn.close()
    out = dict(_meta(seg, it, p))
    out.update({'kind': p.get('kind') or 'text', 'text': p.get('text') or '',
                'columns': p.get('columns') or [], 'rows': p.get('rows') or [],
                'hint': p.get('note') or ''})
    return out


def counts():
    segs = segments()
    return {'segments': len(segs), 'items': sum(len(s['items']) for s in segs)}
