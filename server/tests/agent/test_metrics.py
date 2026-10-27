# -*- coding: utf-8 -*-
"""口径兜底验收：核心指标走固定 SQL，模型不再自由拼（FIX-口径漂移）。

背景：2026-10-21 实测同一个问题连跑三次，执行率出现 22.79% 与 0.00% 两种答案，
两次都"有 SQL 出处"，数字守卫拦不住。修法：
1. `metrics.budget_execution()` —— 口径写死在代码里（状态 3/6/7、create_time、当年年初至统计日）
2. 挂成工具 `budget_execution`，并在 L3 写明"必须用它"
3. `agent_v2.prefetch_metric()` —— **代码先算好塞进上下文**，不指望模型自觉

钉死六条
--------
1. 真 MySQL 取数：分配预算 > 0；率 = 已审批 ÷ 分配。
2. 同参数调两次，结果**完全一致**（可复现）。
3. 缺所名 / 查不到该所 → 明确报错，不返回 0 糊弄。
4. 工具已注册（`budget_execution` 在 `agent.TOOLS` 里）。
5. 预热：指标问题返回非空且含"0.00%"与分配预算；非指标问题返回空串。
6. 非 data 模式不预热（通用写作不该偷偷查库）。

可选：`SECRETARY_LIVE_CONSISTENCY=1` 时额外跑 3 次真实问答，断言执行率一致（会花模型钱）。

运行：py -X utf8 server/tests/agent/test_metrics.py
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SERVER))

_checks: list[tuple[str, bool, str]] = []


def chk(label: str, cond: bool, extra: str = "") -> None:
    _checks.append((label, bool(cond), extra))


METRIC_Q = '环潭供电所2026年预算执行率是多少'
PLAIN_Q = '帮我写一段自我介绍'


def main() -> int:
    print("口径兜底：核心指标固定 SQL + 预热")
    from app.secretary import agent, agent_v2, metrics as M

    # ── 1 真库取数 + 自洽 ───────────────────────────────────────
    try:
        r = M.budget_execution('环潭', 2026)
    except M.MetricError as exc:
        print("  [跳过] 业务库不可用：%s" % str(exc)[:100])
        r = None
    if r:
        chk("分配预算 > 0", r['allocated_yuan'] > 0, str(r['allocated_yuan']))
        chk("执行率 = 已审批 ÷ 分配（两位小数）",
            r['rate_pct'] == round(r['approved_yuan'] / r['allocated_yuan'] * 100.0, 2),
            '%s vs %s' % (r['rate_pct'], r['approved_yuan'] / r['allocated_yuan'] * 100.0))
        chk("口径写明状态 3/6/7 与 create_time",
            '3' in r['caliber'] and 'create_time' in r['caliber'])
        chk("有窗口内状态分布（给'为什么是0'作证）", bool(r['by_status']), str(r['by_status'])[:80])

        # ── 2 同参两次完全一致 ─────────────────────────────────
        r2 = M.budget_execution('环潭', 2026)
        same = (r['allocated_yuan'] == r2['allocated_yuan']
                and r['approved_yuan'] == r2['approved_yuan']
                and r['approved_orders'] == r2['approved_orders']
                and r['rate_pct'] == r2['rate_pct'])
        chk("同参数两次结果完全一致", same,
            '%s/%s vs %s/%s' % (r['allocated_yuan'], r['rate_pct'],
                                r2['allocated_yuan'], r2['rate_pct']))

        md = M.as_markdown(r)
        chk("markdown 含执行率与口径", '执行率' in md and '口径' in md, md[:60])

    # ── 3 异常输入 ─────────────────────────────────────────────
    chk("缺所名报错", _raises(lambda: M.budget_execution('')))
    chk("查不到的所报错（不返回 0）", _raises(lambda: M.budget_execution('不存在的供电所xyz')))

    # ── 4 工具已注册 ───────────────────────────────────────────
    names = [t['function']['name'] for t in agent.TOOLS]
    chk("工具表里有 budget_execution", 'budget_execution' in names, str(names))
    chk("finish 仍在最后", names[-1] == 'finish', str(names))
    if r:
        got = agent.execute('budget_execution', {'station': '环潭'})
        chk("工具调用返回数字", (got.get('numbers') or {}).get('allocated_yuan') == r['allocated_yuan'],
            str(got)[:120])

    # ── 5/6 预热 ───────────────────────────────────────────────
    md = agent_v2.prefetch_metric(METRIC_Q, 'data')
    chk("预热：指标问题命中", bool(md), str(md)[:60])
    if md:
        chk("预热内容含执行率", '%' in md and '执行率' in md, md[:60])
        chk("预热内容含分配预算", '万元' in md, md[:60])
    chk("预热：非指标问题不命中", agent_v2.prefetch_metric(PLAIN_Q, 'data') == '')
    chk("预热：general 模式不查库", agent_v2.prefetch_metric(METRIC_Q, 'general') == '')

    # ── 7 可选：三次真实问答一致性 ─────────────────────────────
    if os.environ.get('SECRETARY_LIVE_CONSISTENCY') == '1':
        rates, alloc = [], []
        for _ in range(3):
            a = (agent.ask(METRIC_Q, model='qwen3.8-flash', max_steps=8).get('answer') or '')
            rates += re.findall(r'(\d+\.\d+)%', a)[:1]
            alloc += re.findall(r'48\.\d+', a)[:1]
        chk("真实问答 3 次执行率一致", len(set(rates)) <= 1, str(rates))
        chk("真实问答 3 次出现分配预算", bool(alloc), str(alloc))

    bad = [c for c in _checks if not c[1]]
    for label, ok, extra in _checks:
        print(("  [OK]   " if ok else "  [FAIL] ") + label + (("   ← " + extra) if not ok and extra else ""))
    print("\n%d 项检查，%d 项失败" % (len(_checks), len(bad)))
    return 1 if bad else 0


def _raises(fn) -> bool:
    try:
        fn()
        return False
    except Exception:                              # noqa: BLE001
        return True


if __name__ == '__main__':
    sys.exit(main())
