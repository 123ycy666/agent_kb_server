# -*- coding: utf-8 -*-
"""功能 1 自检：会议纪要/速读结果 → 待办 → 派单（拟办）。纯逻辑，不连库、不打模型。

钉死的五条
----------
1. 时限能解析的解析准；解析不出就 `待定`，**不许猜**（"近期/尽快/适时"）。
2. 没写责任单位 → `待指派`，**不许默认塞给发起人**。
3. 出处未核实（`verified is False` 或没有 quote）→ `need_review=True`。
4. 优先级：已逾期 `紧急` / ≤30 天 `高` / 有期限 `中` / 无期限 `低`。
5. `to_payload()` 字段齐全，能直接喂给派单接口。

运行：py -X utf8 server/tests/agent/test_digest_to_task.py
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # server/

from app.secretary import dispatch_from_digest as D  # noqa: E402

TODAY = date(2026, 10, 21)
_checks: list[tuple[str, bool, str]] = []


def chk(label: str, cond: bool, extra: str = "") -> None:
    _checks.append((label, bool(cond), extra))


def main() -> int:
    print("功能1：纪要 → 待办 → 派单（拟办）")

    # ── T1 时限解析（含"不猜"）────────────────────────
    cases = [
        ('2026年12月31日前', '2026-12-31'),
        ('2026/12/31', '2026-12-31'),
        ('12月31日', '2026-12-31'),          # 不写年份 = 当年
        ('年底前', '2026-12-31'),
        ('10月底前', '2026-10-31'),
        ('本月底', '2026-10-31'),
        ('近期完成', None),                   # 不猜
        ('尽快', None),                       # 不猜
        ('适时推进', None),                   # 不猜
        ('', None),
    ]
    for raw, want in cases:
        got = D.parse_due(raw, today=TODAY)
        chk("T1 解析 %-14r → %s" % (raw, want or 'None'), got == want, str(got))

    # ── T2/T3/T4 拟办清单 ──────────────────────────────
    items = [
        {'matter': '请制定2026年配网改造实施方案', 'unit': '运检部',
         'deadline': '2026年10月15日前', 'quote': '……于2026年10月15日前报运检部……', 'verified': True},
        {'matter': '要抓紧开展台区线损专项整治', 'unit': '营销部',
         'deadline': '2026年11月20日', 'quote': '……线损专项整治……', 'verified': True},
        {'matter': '适时组织一次应急演练', 'unit': '',
         'deadline': '近期', 'quote': '', 'verified': False},
        {'matter': '请制定2026年配网改造实施方案', 'unit': '运检部',
         'deadline': '2026年10月15日前', 'quote': '重复条目', 'verified': True},   # 去重
    ]
    plan = D.plan_tasks(items, today=TODAY)
    tasks = plan['tasks']
    chk("T2 去重后 3 条", len(tasks) == 3, str(len(tasks)))

    by_title = {t['title']: t for t in tasks}
    t1 = by_title.get('制定2026年配网改造实施方案')
    chk("T2b 去掉「请」前缀", t1 is not None, str(list(by_title)))
    if t1:
        chk("T4 已逾期（10-15 < 10-21）→ 紧急", t1['priority'] == '紧急', t1['priority'])
        chk("T1b 时限进任务", t1['due'] == '2026-10-15', t1['due'])
    t2 = by_title.get('抓紧开展台区线损专项整治')
    chk("T4b 30 天内（11-20）→ 高", t2 and t2['priority'] == '高', str(t2 and t2['priority']))
    t3 = by_title.get('适时组织一次应急演练')
    chk("T2c 没写单位 → 待指派", t3 and t3['owner'] == '待指派', str(t3 and t3['owner']))
    chk("T1c 近期 → 待定（不猜）", t3 and t3['due'] == '待定', str(t3 and t3['due']))
    chk("T3 无 quote + verified=False → need_review",
        t3 and t3['need_review'] is True and t3['source_ok'] is False, str(t3))
    chk("T4c 无期限 → 低", t3 and t3['priority'] == '低', str(t3 and t3['priority']))
    chk("T4d 排序：紧急在前", tasks[0]['priority'] == '紧急', tasks[0]['priority'])

    sm = plan['summary']
    chk("汇总：total=3 urgent=1 need_review=1 no_due=1 no_owner=1",
        (sm['total'], sm['urgent'], sm['need_review'], sm['no_due'], sm['no_owner']) == (3, 1, 1, 1, 1),
        str(sm))
    chk("警告覆盖三类（无时限 / 无单位 / 未核实）", len(plan['warnings']) >= 3, str(plan['warnings']))

    # ── T5 payload ─────────────────────────────────────
    p = D.to_payload(tasks[0], session_id='s_demo')
    need = {'title', 'owner', 'due', 'priority', 'evidence', 'need_review', 'source', 'session_id'}
    chk("T5 payload 字段齐全", need <= set(p), str(sorted(set(p))))
    chk("T5b source=digest", p['source'] == 'digest', p['source'])

    # ── 边界：空清单 ────────────────────────────────────
    empty = D.plan_tasks([], today=TODAY)
    chk("空输入：0 条 + 有警告", empty['tasks'] == [] and bool(empty['warnings']), str(empty['warnings']))

    bad = [c for c in _checks if not c[1]]
    for label, ok, extra in _checks:
        print(("  [OK]   " if ok else "  [FAIL] ") + label + (("   ← " + extra) if not ok and extra else ""))
    print("\n%d 项检查，%d 项失败" % (len(_checks), len(bad)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
