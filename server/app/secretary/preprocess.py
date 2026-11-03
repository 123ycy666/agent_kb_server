# -*- coding: utf-8 -*-
"""预处理：粗闸 + 补全（2026-11-03 改判定方向）。

**粗闸**只回答一个问题：这句话是不是业务问题。只看原话，一个字都不改。

判定方向 2026-11-03 反转（Fred）：
    旧：靠一张「业务相关的问题词表」（`_BIZ`，29 个词）去认业务问题 —— 开集，补不完。
        「两水」两个字里一个业务词都没有，被当闲聊，不补全、不纠错就直答了
        （线上 2026-11-02 实测）。那张词表已删除，原文备份在
        `docs/08-历史归档/2026-11-03-删除的提示词规则/业务相关问题词表.md`。
    新：只认**不查库就能答**的直通词 —— 寒暄 / 问概念 / 出文件 / 写作，词条本身不动
        （就是业务词表里 `chat` / `explain` / `produce` + `objects_file` / `objects_text`）；
        **没命中这些词 → 一律算业务问题**，走纠错 + 补全。
判定直接复用 `modes.judge`：`data` = 业务问题；`general` / `office` = 正常聊天 / 处理文件。

**补全**只剩一种情形：「只有对象、没说要问什么」→ 补默认问法。
补全表（`complete_rule`）2026-11-03 已删除，不再做「口语问法 → 标准口径」的归一。
"""
from __future__ import annotations

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
    """要不要动这句话。True = 业务问题，允许纠错/补全。

    2026-11-03：判定方向反转 —— **没命中直通词就是业务问题**（见文件头）。
    判不出来（读配置失败等）按业务问题处理：多补一句话的代价，远小于「该补没补」。
    """
    q = str(question or '')
    if not q.strip():
        return False
    try:
        from app.secretary import modes
        return str(modes.judge(q).get('mode') or '') == 'data'
    except Exception:                              # noqa: BLE001 - 判不了按业务处理
        return True


def complete(question: str) -> dict:
    """补问题。返回 {'text','changed','hits'}。

    只在 gate 通过后调用 —— 否则「帮我写个通知」会被补成「通知情况怎么样」。

    `hits` 恒为空：补全表已删（2026-11-03），不再有「口语问法 → 标准口径」的命中。
    这个字段留着是因为调用方（`agent.py` 的 trace）还在读它。
    """
    q = str(question or '').strip()
    out = {'text': q, 'changed': False, 'hits': []}
    if not q:
        return out
    # 已经说清了在问什么 → 一个字都不加
    if any(w in q for w in _ASKS):
        return out
    if any(w in q for w in _HAS_TOPIC_WORD) and not any(w in q for w in _HAS_HOW):
        # 「两水供电所情况」→ 只补「怎么样」，不要再叠一个「情况」
        out['text'] = q + '怎么样'
    else:
        out['text'] = q + _DEFAULT_ASK
    out['changed'] = True
    return out
