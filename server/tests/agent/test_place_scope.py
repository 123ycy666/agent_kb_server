# -*- coding: utf-8 -*-
"""地点口径验收：认不出地点时**不许**静默套上一轮（2026-11-02）。

背景：上一问「两水供电所」，下一问「曾都供电所」，答案还是两水的。
根因：`place_of()` 返回空串 → `describe()` 把空串读成"这轮没说地点" → 沿用上一轮，
并把「地点：两水供电所」**钉死**进【统计范围】。旧代码里那句"如果本轮提到别的地点以
问题为准"的附注压不过钉死的范围，所以加提示没用。

现在（2026-11-03 再改）：认不出 → 有上一轮就写「沿用上一轮：X」，没有就写
「按全市取数」并给维表确认办法。**不再说"本轮范围未定"** —— 那句话会让模型反问，
反而答不出来；旧版更糟：没有上一轮时写「沿用上一轮：全市」，是一句假话。

运行：py -X utf8 server/tests/agent/test_place_scope.py（不连数据库、不调模型）
"""
from __future__ import annotations

import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SERVER))

from app.secretary import time_scope                     # noqa: E402

_checks: list[tuple[str, bool, str]] = []
_TODAY = '2026-11-02'
_PREV = {'period_type': 'year', 'period_key': '2025', 'time_text': '2025年',
         'place': '两水供电所'}


def chk(label: str, cond: bool, extra: str = '') -> None:
    _checks.append((label, bool(cond), str(extra)[:220]))


def _stub(recognized: str = ''):
    """把"认地点"和"查库"都换成替身：recognized 非空 = 这一轮认出来了。"""
    time_scope.place_of = lambda fixed, question='': recognized
    time_scope._latest_ym = lambda: '202511'
    time_scope._month_has_data = lambda key: True
    time_scope._nearest_month = lambda key: ''


def describe(q, prev=None, fixed=None, **kw):
    return time_scope.describe(q, fixed=fixed or {}, prev=prev, today=_TODAY, **kw)


def main() -> int:
    print('地点口径验收（不连库）')

    # ── 1 认不出：绝不许把上一轮写成既定范围
    _stub('')
    for q in ('曾都供电所情况怎么样', '曾都供电所', '曾都所', '曾都那边', '曾都的情况',
              '那售电量呢'):
        d = describe(q, prev=_PREV)
        t = d['scope_text']
        chk('认不出「%s」→ place_confirm=True' % q, d['place_confirm'] is True, d['place_confirm'])
        chk('认不出「%s」→ 口径不再说"范围未定"、且给了确认办法' % q,
            '本轮范围未定' not in t and ('按全市取数' in t or '沿用上一轮' in t), t[:80])
        chk('认不出「%s」→ 口径里没有「地点：两水供电所」' % q, '地点：两水供电所' not in t, t[:80])
        chk('认不出「%s」→ 给出维表让人确认' % q,
            't_power_supply_company' in t and 't_power_company' in t, t[:80])
        chk('认不出「%s」→ 机器字段仍能续上一轮（落库连续性）' % q,
            d['place'] == '两水供电所' and d['inherited']['place'] is True, d['place'])

    # ── 2 明说全市：确定，不算未定
    d = describe('全市情况怎么样', prev=_PREV)
    chk('明说全市 → place=全市', d['place'] == '全市', d['place'])
    chk('明说全市 → place_confirm=False', d['place_confirm'] is False, d['place_confirm'])
    chk('明说全市 → 口径写地点：全市', '地点：全市' in d['scope_text'], d['scope_text'][:80])
    chk('明说全市 → 不沿用上一轮', d['inherited']['place'] is False)

    # ── 3 认出了：钉死，不算未定
    _stub('国网曾都区供电公司')
    d = describe('曾都供电所情况怎么样', prev=_PREV)
    chk('认出 → place=国网曾都区供电公司', d['place'] == '国网曾都区供电公司', d['place'])
    chk('认出 → place_confirm=False', d['place_confirm'] is False, d['place_confirm'])
    chk('认出 → 口径写地点：该单位', '地点：国网曾都区供电公司' in d['scope_text'],
        d['scope_text'][:80])
    chk('认出 → 不沿用上一轮', d['inherited']['place'] is False)

    # ── 4 没有上一轮、又认不出：默认全市但不许当"确定"
    _stub('')
    d = describe('曾都供电所')
    chk('无上轮 + 认不出 → place_confirm=True', d['place_confirm'] is True, d['place_confirm'])
    chk('无上轮 + 认不出 → 机器默认全市', d['place'] == '全市' and d['place_default'] is True,
        '%s/%s' % (d['place'], d['place_default']))
    chk('无上轮 + 认不出 → 按全市取数（不是"未定"，也不是假的"沿用")',
        '按全市取数' in d['scope_text'] and '沿用上一轮' not in d['scope_text'],
        d['scope_text'][:120])

    # ── 5 上游强制范围：照旧
    d = describe('曾都供电所情况怎么样', prev=_PREV, force_place='广水市供电公司')
    chk('强制范围 → place=强制值', d['place'] == '广水市供电公司', d['place'])
    chk('强制范围 → 口径写明强制', '强制按此范围' in d['scope_text'], d['scope_text'][:80])
    chk('强制范围 → place_confirm=False', d['place_confirm'] is False)

    # ── 6 防回潮：死代码不许回来
    d = describe('曾都供电所', prev=_PREV)
    chk('describe 的 object 恒为 None（object_index 模块不存在）', d['object'] is None,
        d['object'])
    import re as _re
    src = (SERVER / 'app' / 'secretary' / 'time_scope.py').read_text(encoding='utf-8')
    dead = [ln.strip() for ln in src.splitlines()
            if _re.search(r'(from\s+\S+\s+import\s+object_index|import\s+object_index|'
                          r'object_index\.)', ln.split('#', 1)[0])]
    chk('time_scope.py 代码里不再 import / 调用 object_index（说明文字里可以提）',
        not dead, dead[:2])

    # ── 7 「明说全市」的判据本身
    for q in ('全市', '各所情况', '市局呢', '各县'):
        chk('_says_whole_city「%s」=True' % q, time_scope._says_whole_city(q) is True)
    for q in ('曾都供电所', '那售电量呢', '情况怎么样'):
        chk('_says_whole_city「%s」=False' % q, time_scope._says_whole_city(q) is False)

    bad = [c for c in _checks if not c[1]]
    for label, ok, extra in _checks:
        print(('  [OK]   ' if ok else '  [FAIL] ') + label
              + (('   ← ' + extra) if (not ok and extra) else ''))
    print('\n%d 项检查，%d 项失败' % (len(_checks), len(bad)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
