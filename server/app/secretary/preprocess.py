# -*- coding: utf-8 -*-
"""第 1 步「要不要走预处理」+ 第 3 步「补问法」+ 第 4 步「查补全表」（2026-11-03 定稿）。

整条流水线见 `docs/03-设计/2026-11-03-问答流程-定稿八步.md`。

第 1 步（`gate`）
    判断的不是"要不要查数"，是**要不要走纠错 + 补全 + 补时间地点这条链**。
    判据写在页面提示词 `intent` 里（Fred 自己写，随时改）。
    框空着 / 判不出来 → **一律走**（多补一句的代价，远小于该补没补：
    线上「两水」两个字一个业务词都不沾，被当闲聊直答，就是漏在这里）。

第 3 步（`complete` 的补问法）
    只说了对象、没说问什么 → 补一个默认问法。逻辑没动。

第 4 步（`complete` 的 hits）
    拿**原问题**和**补后问题**去补全表里找，命中的条目原样返回。
    ★ 只查不改：不替换问题、不覆盖问题。用不用、怎么用由第 6 步判断
      （原来"命中即改"，表里只有一条时会把所有问题都改成那一条）。
"""
from __future__ import annotations

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

NOT_GO = '不走'


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
    `text` 是补问法之后的问题；`hits` 是补全表的命中条目（**问题本身不被替换**）。
    """
    q = str(question or '').strip()
    out = {'text': q, 'changed': False, 'hits': []}
    if not q:
        return out
    # 已经说清了在问什么 → 一个字都不加
    if any(w in q for w in _ASKS):
        out['hits'] = hits(q)
        return out
    if any(w in q for w in _HAS_TOPIC_WORD) and not any(w in q for w in _HAS_HOW):
        # 「两水供电所情况」→ 只补「怎么样」，不要再叠一个「情况」
        out['text'] = q + '怎么样'
    else:
        out['text'] = q + _DEFAULT_ASK
    out['changed'] = True
    # 只拿**原话**查表（2026-11-04）：补问法后那句是代码造的，用它去命中等于自己给自己下套
    # ——「上个月线损率」被补成「…线损率情况怎么样」，就命中「情况怎么样」那条兜底。
    out['hits'] = hits(q)
    return out
