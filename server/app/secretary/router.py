# -*- coding: utf-8 -*-
"""入口路由（2026-11-13 定稿）：默认走问数线，只有两种情况去办公线。

    ask_db  问数 —— 2–6 步预处理 + 证据包那条线，**默认去向，一行不动**
    office  办公 —— ① 明确要一份能拿走的文件：产物名 + 让它做出来的说法
                    ② 带了文件，或这句话在指会话里的文件（问数线没有 read_file）

不判「是不是业务问题」：那是问数线自己第 1 步（intent）的事。半句话问情况
（「凉水情况」）也走问数线 —— 2026-11-13 实测：这类半句被旧判据判成「不查数」
进了办公线，跑了 40 轮 SQL，一个字没答出来。

为什么不往 preprocess.route 里加字段：那三个布尔是问数线的内部输入
（allow_refine、口径注入、工具子集都挂在它上面），往里加维度就等于改问数线。
"""
from __future__ import annotations

LANES = ('ask_db', 'office')

# 产物词 → 产物类型。
_PRODUCT = {
    'pptx': ('ppt', '幻灯片', '演示', '汇报材料', '课件'),
    'xlsx': ('excel', '表格', '台账', '统计表', '明细表', '汇总表', '清单表', '看板'),
    'docx': ('word', '文档', '报告', '通知', '公文', '请示', '函', '纪要', '方案',
             '总结', '汇报', '材料', '模板', '初稿', '文书', '讲话稿', '简报'),
}
# 产出性动词：光有产物名不算，得有人让它"做出来"。
# 单字「出」「拟」「整理」太宽（"挑出来""得出""整理思路"都会误中），一律不单放。
_MAKE_VERB = ('写', '做', '生成', '出一份', '出个', '出一版', '整理成', '整理个',
              '整理一份', '转成', '起草', '拟一份', '汇总成', '排版', '套版', '做成', '弄成')
# 只说了"给我"的说法：得配上产物名才算要文件（见 _wants_deliver）。
_DELIVER_TAIL = ('发我', '给我', '交上去', '报上去', '导出', '发过来', '传给我')
# 只要个答案的说法：配着"给我"出现时说明要的是内容，不是文件。
_ASK_HINT = ('多少', '几', '怎么样', '如何', '为什么', '排名', '占比', '对比',
             '查一下', '看看', '分析', '情况')

# 指代一份已经在会话里的文件（语言功能词，不是业务词表）：
# 用户传完文件、下一轮说「这个文章里面说了什么」，那一轮请求里没有 file_ids，
# 只有靠这句话认出"它在说本会话那份文件"，才可能去把它读出来。
FILE_REF = ('这个文件', '这份文件', '这个文章', '这篇文章', '这份材料', '这篇材料', '这个报告',
            '这份报告', '该文件', '该文章', '这份文档', '这个文档', '上面那份', '刚才那份',
            '刚才那个', '刚才上传', '附件', '文件里', '文章里', '材料里', '文中', '全文',
            '这份', '这篇', '这个表', '这份表')


def _rules() -> dict:
    """判据从页面读（`cfg.route_rule`），读不到就用本文件里的常量兜底。

    2026-11-16（阶段 5）：原来这套词表写死在代码里、页面上存不进（`prompt_text` 的
    `TEXT_KEYS` 白名单没有这两个键）—— 这就是「代码读、页面存不进」的两处之一。
    现在改判据只在页面上改，60 秒内生效。
    """
    try:
        from app.secretary import route_rule
        r = route_rule.rules()
        if r and r.get('product') and r.get('file_ref'):
            return r
    except Exception as exc:                       # noqa: BLE001 - 读不到用代码常量
        print('[router] 读判据失败（用代码常量）：%s: %s'
              % (type(exc).__name__, str(exc)[:100]), flush=True)
    return {'product': _PRODUCT, 'make_verb': list(_MAKE_VERB),
            'deliver_tail': list(_DELIVER_TAIL), 'ask_hint': list(_ASK_HINT),
            'file_ref': list(FILE_REF), 'priority': []}


def refers_file(q: str) -> bool:
    text = str(q or '')
    return any(w in text for w in _rules().get('file_ref') or FILE_REF)


def _product_of(q: str) -> str:
    low = str(q or '').lower()
    prod = _rules().get('product') or _PRODUCT
    for kind in ('pptx', 'xlsx', 'docx'):
        for w in (prod.get(kind) or _PRODUCT[kind]):
            if str(w).lower() in low:
                return kind
    return ''


def _has_make_verb(q: str) -> bool:
    verbs = _rules().get('make_verb') or _MAKE_VERB
    return any(str(w) in (q or '') for w in verbs)


def _wants_deliver(q: str) -> bool:
    """要一份文件 = 说清了要什么文件 + 说了让我做出来。

    只认产物名：光说「发我 / 交上去」不算 —— 那样「把数据给我」也会被当成
    要文件，而它要的是内容。
    """
    r = _rules()
    if not _product_of(q):
        return False
    if _has_make_verb(q):
        return True
    return (any(t in (q or '') for t in (r.get('deliver_tail') or _DELIVER_TAIL))
            and not any(h in (q or '') for h in (r.get('ask_hint') or _ASK_HINT)))


def route(question: str, has_files: bool = False) -> dict:
    """返回 {lane, product, howto, need, why}。

    两种去向（2026-11-13 按 Fred 的要求改回默认问数）：
      · ask_db —— 默认。半句问情况、闲聊、问排名、问趋势全在这儿；要不要查库
                  由它自己第 1 步（intent）判，入口不替它判。
      · office —— 只有两种：明确要一份文件；本轮带了文件、或这句话在指会话里的
                  文件（问数线没有 read_file）。
    """
    q = str(question or '').strip()
    product = _product_of(q)
    if product and _wants_deliver(q):
        return {'lane': 'office', 'product': product, 'howto': '',
                'need': ['data', 'file_write'] + (['file_read'] if has_files else []),
                'why': '要交付 %s' % product}
    if has_files:
        return {'lane': 'office', 'product': '', 'howto': '',
                'need': ['file_read', 'data'], 'why': '本轮带了文件'}
    if refers_file(q):
        return {'lane': 'office', 'product': '', 'howto': '',
                'need': ['file_read', 'data'], 'why': '这句话在指会话里的文件'}
    return {'lane': 'ask_db', 'product': '', 'howto': '', 'need': ['data'],
            'why': '默认走问数线'}
