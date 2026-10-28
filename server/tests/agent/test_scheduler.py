# -*- coding: utf-8 -*-
"""F5-1 验收：定时任务（APScheduler）。

钉死五条
--------
1. 站点包 `jobs.yml` 里的任务能被读出来（随州：1 条启用 + 1 条关闭）。
2. 只注册 `JOBS` 白名单里的活；包写了不存在的 fn → 记 skipped，不报错崩掉。
3. cron 非法 → 记 skipped，不影响其它任务。
4. 注册出来的任务触发器时间正确（`0 8 * * *` → 每天 08:00）。
5. 活真能被调用（直接调 job.func，不等到点）。

运行：py -X utf8 server/tests/agent/test_scheduler.py
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SERVER))

from app.secretary import scheduler as S

_checks: list[tuple[str, bool, str]] = []


def chk(label: str, cond: bool, extra: str = "") -> None:
    _checks.append((label, bool(cond), extra))


def main() -> int:
    print("F5-1：定时任务（APScheduler）")

    # ── 1 随州包：1 启用 + 1 关闭 ───────────────────────────────
    built = S.build()
    chk("随州：注册 1 条（selfcheck_daily）",
        [j['id'] for j in built['added']] == ['selfcheck_daily'], str(built['added']))
    chk("随州：alerts_daily 因 enabled=false 被跳过",
        any(x['id'] == 'alerts_daily' and 'enabled' in x['why'] for x in built['skipped']),
        str(built['skipped']))

    # ── 2/3/4 假站点包：白名单 + 非法 cron ──────────────────────
    fake = SERVER / '.tmp' / 'sched_demo'
    if fake.exists():
        shutil.rmtree(fake, ignore_errors=True)
    (fake / 'demo').mkdir(parents=True)
    (fake / 'demo' / 'jobs.yml').write_text(
        'jobs:\n'
        '  - {id: ok1, fn: noop, cron: "*/5 * * * *", enabled: true}\n'
        '  - {id: bad_fn, fn: rm_rf, cron: "0 9 * * *", enabled: true}\n'
        '  - {id: bad_cron, fn: noop, cron: "not a cron", enabled: true}\n'
        '  - {id: incomplete, fn: noop, enabled: true}\n', encoding='utf-8')

    old_env, old_site = os.environ.get('SITE_PACK_DIR'), os.environ.get('SITE')
    os.environ['SITE_PACK_DIR'] = str(fake)
    os.environ['SITE'] = 'demo'
    try:
        b2 = S.build()
        chk("假包：只注册白名单里的 ok1", [j['id'] for j in b2['added']] == ['ok1'],
            str(b2['added']))
        whys = {x['id']: x['why'] for x in b2['skipped']}
        chk("假包：不存在的 fn 被拒", 'bad_fn' in whys and '白名单' in whys['bad_fn'], str(whys))
        chk("假包：非法 cron 被拒", 'bad_cron' in whys, str(whys.get('bad_cron', '')))
        chk("假包：字段不全被拒", 'incomplete' in whys, str(whys.get('incomplete', '')))

        # 触发器时间
        sch = b2['scheduler']
        jobs = sch.get_jobs()
        trig = jobs[0].trigger if jobs else None
        chk("触发器：每 5 分钟", trig is not None and getattr(trig, 'fields', None) is not None,
            str(trig))
        fields = {f.name: str(f) for f in (trig.fields if trig else [])}
        chk("触发器字段含 minute=*/5", '*/5' in fields.get('minute', ''), str(fields))

        # ── 5 活真能被调用 ─────────────────────────────────────
        S.RUNS.clear()
        jobs[0].func()
        chk("活被调用且记录进 RUNS", len(S.RUNS) == 1 and S.RUNS[0].get('result') == 'noop',
            str(S.RUNS))
    finally:
        if old_env is None:
            os.environ.pop('SITE_PACK_DIR', None)
        else:
            os.environ['SITE_PACK_DIR'] = old_env
        if old_site is None:
            os.environ.pop('SITE', None)
        else:
            os.environ['SITE'] = old_site

    # ── 6 启停幂等 ─────────────────────────────────────────────
    sch = S.start()
    chk("start() 返回调度器", sch is not None)
    chk("重复 start() 不重复起", S.start() is sch)
    chk("status() 有 running/jobs", S.status().get('running') is True and
        isinstance(S.status().get('jobs'), list), str(S.status())[:80])
    S.stop()
    chk("stop() 后状态为未运行", S.status().get('running') is False)

    bad = [c for c in _checks if not c[1]]
    for label, ok, extra in _checks:
        print(("  [OK]   " if ok else "  [FAIL] ") + label + (("   ← " + extra) if not ok and extra else ""))
    print("\n%d 项检查，%d 项失败" % (len(_checks), len(bad)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
