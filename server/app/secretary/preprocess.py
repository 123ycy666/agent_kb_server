# -*- coding: utf-8 -*-
"""预处理：粗闸 + 补全（2026-11-01）。

**粗闸**只回答一个问题：这句话像不像业务问题。只看原话，一个字都不改。
不像的（写通知、闲聊、问概念）后面一步都不许动原话 —— 这是为了不再把「资金」改成「紫金台区」。

**补全**照着 `complete_rule` 表做两件事：
    norm 归一：口语问法 → 标准口径
    fill 补缺：只有对象没问法 → 补默认问法
这张表不进提示词，代码读完直接把问题补好，模型只拿到结果。
"""
from __future__ import annotations

# 有这些字，就当是业务问题（粗判，宁可多放行：放行后还有题型判断兜着）
_BIZ = (
    '所', '县', '市', '台区', '线路', '线损', '工单', '缺陷', '预算', '项目', '审批',
    '指标', '抢修', '停电', '售电', '执行率', '进度', '费用', '金额', '分配', '投资',
    '多少', '几个', '哪些', '排名', '占比', '同比', '环比', '情况', '为什么', '够不够',
)

# 已经有这些，说明「在问什么」说清楚了，不用补。
# ⚠️ 「情况」单独出现不算问清楚 —— 「两水供电所情况」还是要补成「…情况怎么样」。
#    第一版把「情况」放进来了，结果这类问题一个都没补（线上 2026-11-02 实测）。
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


def gate(question: str) -> bool:
    """要不要动这句话。True = 像业务问题，允许纠错/补全。"""
    q = str(question or '')
    if not q.strip():
        return False
    return any(w in q for w in _BIZ)


def _norm_hits(question: str, rows: list[dict]) -> list[dict]:
    q = str(question or '')
    out = []
    for r in rows or []:
        if str(r.get('kind')) != 'norm':
            continue
        m = str(r.get('match_text') or '')
        if m and m in q:
            out.append(r)
    return out


def complete(question: str) -> dict:
    """补问题。返回 {'text','changed','hits'}。

    只在 gate 通过后调用 —— 否则「帮我写个通知」会被补成「通知情况怎么样」。
    """
    q = str(question or '').strip()
    out = {'text': q, 'changed': False, 'hits': []}
    if not q:
        return out
    try:
        from app.secretary import complete_rules
        rows = complete_rules.rows()
    except Exception:                              # noqa: BLE001 - 表读不到就不补
        rows = []
    hits = _norm_hits(q, rows)
    out['hits'] = [{'match': h.get('match_text'), 'fill': h.get('fill_text')} for h in hits]
    # 只说了对象、没说要问什么 → 补默认问法
    if not any(w in q for w in _ASKS):
        if any(w in q for w in _HAS_TOPIC_WORD) and not any(w in q for w in _HAS_HOW):
            # 「两水供电所情况」→ 只补「怎么样」，不要再叠一个「情况」
            out['text'] = q + '怎么样'
        else:
            out['text'] = q + _DEFAULT_ASK
        out['changed'] = True
        # 补完再算一次口径（补出来的问法正好命中补全表）
        out['hits'] = [{'match': h.get('match_text'), 'fill': h.get('fill_text')}
                       for h in _norm_hits(out['text'], rows)]
    return out
