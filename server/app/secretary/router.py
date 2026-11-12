# -*- coding: utf-8 -*-
"""入口路由（2026-11-11）：只分线，不改线里的东西。

四条线：
    office  办事 —— 要一个能拿走的交付物（docx / xlsx / pptx / 清单）
    read    读件 —— 带了文件、要看法/审法/提取
    ask_db  问数 —— 2–6 步预处理 + 证据包那条线，**一行不动**
    chat    闲聊 —— 不调工具

判法：先硬规则（有没有文件、有没有"产出性动词 + 产物名"），命中即定；
剩下的在 ask_db / chat 之间交给一次短模型调用；判不出回 ask_db（最成熟）。

为什么不往 preprocess.route 里加字段：那三个布尔是问数线的内部输入
（allow_refine、口径注入、工具子集都挂在它上面），往里加维度就等于改问数线。
"""
from __future__ import annotations

import json

LANES = ('office', 'read', 'ask_db', 'chat')

# 产物词 → 产物类型。这几张表是**配置数据**，页面框 `router` 可以整份替换。
_PRODUCT = {
    'pptx': ('ppt', '幻灯片', '演示', '汇报材料', '课件'),
    'xlsx': ('excel', '表格', '台账', '统计表', '明细表', '汇总表', '清单表', '看板'),
    'docx': ('word', '文档', '报告', '通知', '公文', '请示', '函', '纪要', '方案',
             '总结', '汇报', '材料', '模板', '初稿', '文书', '讲话稿', '简报'),
}
# 产出性动词：光有产物名不算，得有人让它"做出来"。
# 单字「出」「拟」「整理」太宽（"挑出来""得出""整理思路"都会误中），一律不单放。
_MAKE_VERB = ('写', '做', '生成', '出一份', '出个', '出一版', '出一份', '整理成', '转成',
              '导出', '起草', '拟一份', '汇总成', '排版', '套版', '发我', '给我',
              '交上去', '报上去', '做成', '弄成')
# 只要个答案的说法：出现这些且没有产物 → 一定不是 office
_ASK_HINT = ('多少', '几', '怎么样', '如何', '为什么', '多少', '排名', '占比', '对比',
             '查一下', '看看', '分析', '情况')

# 指代一份已经在会话里的文件（语言功能词，不是业务词表）：
# 用户传完文件、下一轮说「这个文章里面说了什么」，那一轮请求里没有 file_ids，
# 只有靠这句话认出"它在说本会话那份文件"，才可能去把它读出来。
FILE_REF = ('这个文件', '这份文件', '这个文章', '这篇文章', '这份材料', '这篇材料', '这个报告',
            '这份报告', '该文件', '该文章', '这份文档', '这个文档', '上面那份', '刚才那份',
            '刚才那个', '刚才上传', '附件', '文件里', '文章里', '材料里', '文中', '全文',
            '这份', '这篇', '这个表', '这份表')


def refers_file(q: str) -> bool:
    text = str(q or '')
    return any(w in text for w in FILE_REF)


_DEFAULT_ROUTE_RULE = (
    '只回答一行，不要解释：\n'
    '分流=查数 或 不查数\n'
    '查数：必须拿到本单位的业务数据才能答（金额、工单、预算、进度、指标、排名、哪个所多少）。\n'
    '不查数：写文件、整理材料、看用户给的文件、校对、闲聊，以及其它一切。\n'
    '拿不准一律回「不查数」。'
)


def _cfg() -> dict:
    """页面框 `router` 给的配置，没配置就用内置这张表。"""
    try:
        from app.secretary import prompt_text
        raw = str(prompt_text.get('router') or '').strip()
        if raw:
            got = json.loads(raw)
            if isinstance(got, dict):
                return got
    except Exception:                               # noqa: BLE001 - 配置坏了用默认
        pass
    return {}


def _product_of(q: str, cfg: dict) -> str:
    low = str(q or '').lower()
    table = cfg.get('product') if isinstance(cfg.get('product'), dict) else _PRODUCT
    for kind in ('pptx', 'xlsx', 'docx'):
        for w in (table.get(kind) or ()):
            if str(w).lower() in low:
                return kind
    return ''


def _has_make_verb(q: str, cfg: dict) -> bool:
    words = cfg.get('make_verb') or _MAKE_VERB
    return any(str(w) in (q or '') for w in words)


def _wants_deliver(q: str, cfg: dict) -> bool:
    """产出性动词 + 产物名（或明确"发我/交上去"）同时出现，才算办事。"""
    product = _product_of(q, cfg)
    verb = _has_make_verb(q, cfg)
    if product and verb:
        return True
    # 没有产物名但说了"发我 / 交上去 / 导出"这类，也算要交付（产物默认 docx）
    tail = ('发我', '给我', '交上去', '报上去', '导出', '发过来', '传给我')
    hit = any(t in (q or '') for t in tail) and not any(h in (q or '') for h in _ASK_HINT)
    if hit and refers_file(q) and not product:
        # 「这个文章里面说了什么 直接输出给我」——"给我"要的是内容，不是一份文件
        return False
    return hit


def _ask_lane(question: str, ask) -> tuple[str, str]:
    """只判一件事：这句要不要本单位的业务数据。

    判「查数」→ 走查数线（那条链路的预处理/证据包是它的强项）。
    其余（写东西、看文件、闲聊、别的）→ 统一办公线，由模型自己看做法、自己决定做什么。
    判不出 → 办公线：它有全套工具，什么都能干；查数线没有 read_file，判断错更贵。
    """
    if ask is None:
        return 'office', '无模型可判，默认办公线'
    try:
        from app.secretary import prompt_text
        rule = str(prompt_text.get('router') or '').strip() or _DEFAULT_ROUTE_RULE
        if rule.startswith('{'):
            rule = _DEFAULT_ROUTE_RULE
    except Exception:                               # noqa: BLE001
        rule = _DEFAULT_ROUTE_RULE
    try:
        got = str(ask(rule, '本轮原话：%s' % (question or '')) or '')
    except Exception as exc:                        # noqa: BLE001
        print('[router] 分流判断失败（按办公线）：%s: %s' % (type(exc).__name__, str(exc)[:100]),
              flush=True)
        return 'office', '判断失败，默认办公线'
    if '查数' in got and '不查数' not in got and '非查数' not in got:
        return 'ask_db', got.strip()[:80]
    return 'office', got.strip()[:80]


def route(question: str, has_files: bool = False, ask=None) -> dict:
    """返回 {lane, product, howto, need, why}。

    只有两种去向（2026-11-12 按 Fred 的要求重构）：
      · ask_db   —— 要本单位业务数据，走查数线（那条链路一行没动）
      · office   —— 其余全部：写东西、看文件、做表格、校对、闲聊、任何没见过的活，
                    由模型自己看做法目录、自己决定怎么做。代码不判断"这是什么任务"。
    会话里有文件 → 一定是办公线（查数线没有 read_file）。
    """
    q = str(question or '').strip()
    if has_files:
        return {'lane': 'office', 'product': '', 'howto': '',
                'need': ['file_read', 'data'], 'why': '会话里有文件'}
    lane, why = _ask_lane(q, ask)
    return {'lane': lane, 'product': '', 'howto': '',
            'need': ['data'] if lane == 'ask_db' else ['data', 'file_read', 'file_write'],
            'why': why}


def attach_howto(routed: dict, question: str) -> dict:
    """办公线用：把做法挑出来（词表在做法文档里，是数据不是代码）。"""
    if routed.get('lane') != 'office' or routed.get('howto'):
        return routed
    try:
        from app.secretary import howto
        item = howto.match(question) or howto.get('', routed.get('product') or 'docx')
        if item:
            routed = dict(routed)
            routed['howto'] = item.get('task') or ''
            routed['product'] = item.get('product') or routed.get('product')
    except Exception as exc:                        # noqa: BLE001 - 做法取不到不阻断
        print('[router] 取做法失败：%s: %s' % (type(exc).__name__, str(exc)[:100]), flush=True)
    return routed
