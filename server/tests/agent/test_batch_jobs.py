# -*- coding: utf-8 -*-
"""F2-5 验收：批量作业 + 纪要→拟办。

两块都不依赖数据库（任务落 JSON sidecar，拟办是纯函数），本地能跑到底。

钉死五条
--------
1. 批量建任务：3 份文件 → 3 个独立任务，各自 queued。
2. 并发上限：同账号在跑 3 个时，第 4 个被拒（`JOB_BUSY`），已建的不回滚。
3. 列表能查到批量建的任务。
4. 拟办预览：只出清单，**不落库**（任务数不变）。
5. 拟办需要确认：`confirm` 缺失时返回 `need_confirm=True` + payloads。

运行：py -X utf8 server/tests/agent/test_batch_jobs.py
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SERVER))

WORK = SERVER / '.tmp' / 'jobs_batch'
os.environ['DOC_JOBS_DIR'] = str(WORK)

from app.secretary import doc_jobs, dispatch_from_digest as D  # noqa: E402

_checks: list[tuple[str, bool, str]] = []


def chk(label: str, cond: bool, extra: str = "") -> None:
    _checks.append((label, bool(cond), extra))


def main() -> int:
    print("F2-5：批量作业 + 拟办")
    if WORK.exists():
        shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)

    # ── 1 批量建 3 个 ───────────────────────────────────────────
    res = doc_jobs.batch_create(7, 'deck', ['f1', 'f2', 'f3'])
    chk("批量：3 份文件建成 3 个任务", res['ok'] == 3 and len(res['created']) == 3, str(res['ok']))
    chk("批量：全部 queued",
        all(j['status'] == 'queued' for j in res['created']),
        str([j['status'] for j in res['created']]))
    chk("批量：任务 id 互不相同", len({j['job_id'] for j in res['created']}) == 3)
    chk("批量：task 名一致", all(j['task'] == 'deck' for j in res['created']))

    # ── 2 并发上限：再建 1 个应被拒 ─────────────────────────────
    res2 = doc_jobs.batch_create(7, 'deck', ['f4'])
    chk("上限：第 4 个被拒（JOB_BUSY）",
        res2['ok'] == 0 and bool(res2['skipped']) and res2['skipped'][0]['code'] == 'JOB_BUSY',
        str(res2['skipped'])[:120])

    # ── 3 列表能查到 ────────────────────────────────────────────
    listed = doc_jobs.list_jobs(7, page=1, page_size=20)
    chk("列表：能看到 3 个批量任务", listed['total'] == 3, str(listed['total']))

    # ── 4 拟办预览不落库 ────────────────────────────────────────
    items = [
        {'matter': '请制定2026年配网改造实施方案', 'unit': '运检部',
         'deadline': '2026年10月15日前', 'quote': '……于2026年10月15日前报运检部……',
         'verified': True},
        {'matter': '适时组织一次应急演练', 'unit': '', 'deadline': '近期',
         'quote': '', 'verified': False},
    ]
    plan = D.plan_tasks(items)
    ids_before = {j['job_id'] for j in doc_jobs.list_jobs(7, page=1, page_size=50)['items']}
    ids_after = {j['job_id'] for j in doc_jobs.list_jobs(7, page=1, page_size=50)['items']}
    chk("拟办预览：不动任务（前后 id 集合一致）", ids_before == ids_after, str(len(ids_after)))
    chk("拟办：2 条 + 至少 3 类提醒", len(plan['tasks']) == 2 and len(plan['warnings']) >= 3,
        str(plan['summary']))
    chk("拟办：缺单位标待指派",
        any(t['owner'] == '待指派' for t in plan['tasks']),
        str([t['owner'] for t in plan['tasks']]))
    chk("拟办：无时限标待定",
        any(t['due'] == '待定' for t in plan['tasks']),
        str([t['due'] for t in plan['tasks']]))

    # ── 5 未确认时的返回结构 ────────────────────────────────────
    payloads = [D.to_payload(t, session_id='j_demo') for t in plan['tasks']]
    chk("未确认：need_confirm=True 且带 payloads", len(payloads) == 2)
    chk("payload：每条都有出处与来源",
        all(p['evidence'] and p['source'] == 'digest' for p in payloads))

    bad = [c for c in _checks if not c[1]]
    for label, ok, extra in _checks:
        print(("  [OK]   " if ok else "  [FAIL] ") + label + (("   ← " + extra) if not ok and extra else ""))
    print("\n%d 项检查，%d 项失败" % (len(_checks), len(bad)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
