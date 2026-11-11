# -*- coding: utf-8 -*-
"""第 1 步「要不要走预处理」+ 第 3 步「补问法」+ 第 4 步「查补全表」（2026-11-03 定稿）。

整条流水线见 `docs/03-设计/2026-11-03-问答流程-定稿八步.md`。

第 1 步（`gate`）
    判断的不是"要不要查数"，是**要不要走纠错 + 补全 + 补时间地点这条链**。
    判据写在页面提示词 `intent` 里（Fred 自己写，随时改）。
    框空着 / 判不出来 → **一律走**（多补一句的代价，远小于该补没补：
    线上「两水」两个字一个业务词都不沾，被当闲聊直答，就是漏在这里）。

第 3 步（`complete` 的补问法）
    只说了对象、没说问什么 → 补一个默认问法。
    ★ 2026-11-11（Fred）：规则搬到页面上（`askfill` 键，第 4 步节点），词表能改，
      也能用 `"on": false` 把这一步整个关掉 —— 关掉就是一字不加，补全表照原话查。

第 4 步（`complete` 的 hits）
    拿**补问法之后**的问题去补全表里找，命中的条目原样返回（2026-11-08 改回）。
    ★ 只查不改：不替换问题、不覆盖问题。用不用、怎么用由第 5 步判断
      （原来"命中即改"，表里只有一条时会把所有问题都改成那一条）。
"""
from __future__ import annotations

import json

# 已经有这些，说明「在问什么」说清楚了，不用补。
# ⚠️ 「情况」单独出现不算问清楚 —— 「两水供电所情况」还是要补成「…情况怎么样」。
_ASKS = (
    '多少', '多大', '几个', '几笔', '几条', '哪些', '哪几个', '排名', '前几', '占比',
    '比例', '同比', '环比', '情况怎么样', '什么情况', '整体情况', '怎么样', '如何',
    '为什么', '为啥', '原因', '够不够', '能不能', '该不该', '怎么办', '怎么改', '怎么降',
    '建议', '方案', '吗', '？', '?', '对比', '差多少', '统计',
)

# 只补「怎么样」的情形：问题里已经说了「情况」，再补「情况怎么样」就重复了
_HAS_TOPIC_WORD = ('情况', '状态', '表现')
_HAS_HOW = ('怎么样', '如何', '咋样', '好不好')

_DEFAULT_ASK = '情况怎么样'

# 页面 `askfill` 框空着（或哪一项没写）时用的默认规则。全是数据，没有业务词。
_ASKFILL_DEFAULT = {
    'on': True,
    'default': _DEFAULT_ASK,
    'asked': list(_ASKS),
    'topic': list(_HAS_TOPIC_WORD),
    'how': list(_HAS_HOW),
}

# `"on": "false" / "0" / "关" / "不补"` 都当关掉（页面上手写 JSON 什么写法都有）
_OFF_WORDS = ('false', '0', 'off', 'no', 'none', '否', '关', '不补', '关掉')


def _on(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() not in _OFF_WORDS
    return bool(v)


def askfill_config() -> dict:
    """第 3 步补问法的规则：读页面 `askfill`，缺什么用默认值。

    读不到 / JSON 写坏 → 用默认规则（跟以前完全一样），不许让问答崩。
    """
    cfg = {k: (list(v) if isinstance(v, list) else v) for k, v in _ASKFILL_DEFAULT.items()}
    try:
        from app.secretary import prompt_text
        raw = str(prompt_text.get('askfill', '') or '').strip()
    except Exception as exc:                       # noqa: BLE001 - 读不到框就用默认规则
        print('[preprocess] 补问法规则读取失败（按默认）：%s: %s'
              % (type(exc).__name__, str(exc)[:120]), flush=True)
        return cfg
    if not raw:
        return cfg
    try:
        got = json.loads(raw)
    except Exception as exc:                       # noqa: BLE001 - JSON 写坏就用默认规则
        print('[preprocess] 补问法规则不是合法 JSON（按默认）：%s: %s'
              % (type(exc).__name__, str(exc)[:120]), flush=True)
        return cfg
    if not isinstance(got, dict):
        return cfg
    if 'on' in got:
        cfg['on'] = _on(got['on'])
    d = str(got.get('default') or '').strip()
    if d:
        cfg['default'] = d
    for k in ('asked', 'topic', 'how'):
        v = got.get(k)
        if isinstance(v, list):
            cfg[k] = [str(x) for x in v if str(x).strip()]
    return cfg

NOT_GO = '不走'

# 页面框 `intent` 空着时用的默认判据（机制说明，不含业务词 —— 业务判据请写到页面上）。
# 输出三个是/否：查库 / 读文件 / 出文件。第 7 步按这三个给工具子集。
_ROUTE_RULE = (
    '只回答四行，不要解释、不要展开：\n'
    '查库=是 或 否\n'
    '读文件=是 或 否\n'
    '出文件=是 或 否\n'
    '理由=一句话\n'
    '判法（**默认查库=是**，判"否"必须有明确理由）：\n'
    '- 默认查库=是：这个系统的用户只会说半句（「情况」「预算」「两水」这类），'
    '说得再省略也是要查业务数据 —— 不查库根本没法回答"情况"；\n'
    '- 只有这两种才查库=否：① 打招呼、寒暄、与业务无关的闲聊；'
    '② 只让写字（写通知 / 总结 / 把这段文字改一下）且不需要任何业务数字；\n'
    '- 本轮带了文件，或要求看 / 审 / 改 / 转某份文件 → 读文件=是；\n'
    '- 要求产出 Word / PPT / Excel / 成文报告这类文件 → 出文件=是。'
)

_YES = ('是', 'YES', 'Y', 'TRUE', '1')


def _bool_of(text: str, key: str, default: bool) -> bool:
    import re as _re
    m = _re.search(key + r'\s*[=:：]\s*([^\s，。、；]+)', str(text or ''))
    if not m:
        return default
    return m.group(1).strip().upper() in _YES or '是' in m.group(1)


def route(question: str, history=None, has_files: bool = False, ask=None) -> dict:
    """第 1 步：三个是/否（2026-11-05）。

    判据来自页面框 `intent`；框空就用 `_ROUTE_RULE`。判不出、调用失败 →
    退成「只查库」（等于现在的行为，问数不受影响）。

    物理规则压过模型：本轮带了文件 → 读文件一律为是。
    """
    out = {'use_db': True, 'use_file': bool(has_files), 'make_file': False,
           'reason': '默认（没判）'}
    try:
        from app.secretary import prompt_text
        rule = (prompt_text.get('intent', '') or '').strip() or _ROUTE_RULE
    except Exception:                              # noqa: BLE001 - 读不到框就用默认判据
        rule = _ROUTE_RULE
    if ask is None or not str(question or '').strip():
        return out
    lines = []
    for h in list(history or [])[-3:]:
        lines.append('用户：%s' % (h.get('raw_question') or ''))
        lines.append('系统：%s' % str(h.get('answer') or '').replace('\n', ' ')[:120])
    user = ('最近几轮：\n%s\n\n本轮原话：%s\n本轮带了文件：%s'
            % ('\n'.join(lines) if lines else '（没有）', question, '是' if has_files else '否'))
    try:
        got = str(ask(rule, user) or '').strip()
    except Exception as exc:                       # noqa: BLE001 - 判不了按默认
        print('[preprocess] 分流判断失败（按只查库）：%s: %s'
              % (type(exc).__name__, str(exc)[:120]), flush=True)
        return out
    out['use_db'] = _bool_of(got, '查库', True)
    out['make_file'] = _bool_of(got, '出文件', False)
    if has_files:
        out['use_file'] = True                     # 物理事实，模型改不了
    else:
        out['use_file'] = _bool_of(got, '读文件', False)
    import re as _re
    m = _re.search(r'理由\s*[=:：]\s*(.+)', got)
    out['reason'] = (m.group(1).strip() if m else '')[:120]
    return out


def gate(question: str, history=None, ask=None) -> bool:
    """第 1 步：要不要走预处理。True = 走（纠错 + 补全 + 补时间地点）。

    判据来自页面框 `intent`（空 = 全走）。`ask(system, user)` 由调用方注入
    （agent 那边的一次模型调用），这样本模块不依赖模型通道。

    只认 `不走` 这一个词：回别的东西、调用失败、框空着 —— 一律当"走"。
    """
    q = str(question or '')
    if not q.strip():
        return False
    try:
        from app.secretary import prompt_text
        rule = (prompt_text.get('intent', '') or '').strip()
    except Exception:                              # noqa: BLE001 - 读不到框就按全走
        return True
    if not rule or ask is None:
        return True
    lines = []
    for h in list(history or [])[-5:]:
        lines.append('用户：%s' % (h.get('raw_question') or ''))
        lines.append('系统：%s' % str(h.get('answer') or '').replace('\n', ' ')[:200])
    user = '最近几轮问答：\n%s\n\n本轮原话：%s' % ('\n'.join(lines) if lines else '（没有）', q)
    try:
        out = str(ask(rule, user) or '').strip()
    except Exception as exc:                       # noqa: BLE001 - 判不了按走
        print('[preprocess] 意图判断失败（按走处理）：%s: %s'
              % (type(exc).__name__, str(exc)[:120]), flush=True)
        return True
    return NOT_GO not in out


def hits(text: str) -> list[dict]:
    """第 4 步：补全表里命中这条问题的条目（原样返回，不改问题）。"""
    t = str(text or '')
    if not t:
        return []
    try:
        from app.secretary import complete_rules
        rows = complete_rules.rows()
    except Exception as exc:                       # noqa: BLE001 - 表读不到就没有命中
        print('[preprocess] 补全表读取失败（按无命中）：%s: %s'
              % (type(exc).__name__, str(exc)[:120]), flush=True)
        return []
    out, seen = [], set()
    for r in rows:
        mt = str(r.get('match_text') or '').strip()
        if not mt or mt not in t:
            continue
        item = {'kind': str(r.get('kind') or 'norm'), 'match': mt,
                'fill': str(r.get('fill_text') or '')}
        key = (item['kind'], item['match'], item['fill'])
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def complete(question: str) -> dict:
    """第 3 步补问法 + 第 4 步查表。返回 {'text','changed','hits'}。

    只在第 1 步判"走"之后调用 —— 否则「帮我写个通知」会被补成「通知情况怎么样」。
    `text` 是补问法之后的问题；`hits` 是**拿这句话**去补全表命中的条目（问题本身不被替换）。
    规则来自页面 `askfill`（可改、可关）：`"on": false` = 一字不加，补全表照原话查。
    """
    q = str(question or '').strip()
    out = {'text': q, 'changed': False, 'hits': []}
    if not q:
        return out
    cfg = askfill_config()
    if not cfg['on']:
        out['hits'] = hits(q)
        return out
    # 已经说清了在问什么 → 一个字都不加
    if any(w in q for w in cfg['asked']):
        out['hits'] = hits(q)
        return out
    if any(w in q for w in cfg['topic']) and not any(w in q for w in cfg['how']):
        # 「两水供电所情况」→ 只补「怎么样」，不要再叠一个「情况」
        out['text'] = q + '怎么样'
    else:
        out['text'] = q + cfg['default']
    out['changed'] = True
    # ★ 2026-11-08（Fred）：改回用**补问法之后**的问题查表 —— 补出来的问法也要能命中补全表。
    out['hits'] = hits(out['text'])
    return out
