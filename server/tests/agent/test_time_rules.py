# -*- coding: utf-8 -*-
"""时间地点补全**已被删除**（2026-11-03 定稿，Fred）。

代码不再换算「本月 / 今年 / 9月」，也不补默认年份和默认地点 —— 要补什么
由 Fred 在「补全表」（第 4 步 `complete_rule`）里自己配，命中后交给第 5 步
「问题确定」判断怎么用。这个文件只守住"没再偷偷补"这条线。

运行：python -X utf8 server/tests/agent/test_time_rules.py
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SERVER))

_checks: list[tuple[str, bool, str]] = []


def chk(label: str, cond: bool, extra: str = '') -> None:
    _checks.append((label, bool(cond), extra))


def main() -> int:
    from app.secretary import prompt_text, prompt_tree

    print('时间地点补全已删除 —— 验收')

    keys = set(prompt_text.TEXT_KEYS)
    for k in ('time_month_rel', 'time_year_rel', 'time_year_off', 'time_ytd',
              'time_whole_city', 'time_strip_head', 'time_strip_tail'):
        chk('页面不再有 %s' % k, k not in keys, sorted(keys))

    nodes = prompt_tree.tree()
    names = [n.get('name') for n in nodes]
    mats = [m['key'] for n in nodes for m in n['materials']]
    chk('节点树 8 步（接手 + 七步）', len(nodes) == 8, names)
    chk('没有「算时间地点」这一步', all('时间地点' not in (n or '') for n in names), names)
    chk('没有 time.scope 材料', 'time.scope' not in mats, mats)

    src = io.open(SERVER / 'app' / 'secretary' / 'agent.py', encoding='utf-8').read()
    chk('agent 不再调用 time_scope.describe', 'time_scope.describe(' not in src)
    chk('agent 里 scope 是空骨架', "'skipped': True" in src)
    chk('scope 里 time_text 恒为空', "'time_text': ''" in src)

    bad = [c for c in _checks if not c[1]]
    for label, ok, extra in _checks:
        print(('  [OK]   ' if ok else '  [FAIL] ') + label
              + (('   ← ' + extra) if (not ok and extra) else ''))
    print('\n%d 项检查，%d 项失败' % (len(_checks), len(bad)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
