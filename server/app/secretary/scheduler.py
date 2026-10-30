# -*- coding: utf-8 -*-
"""定时任务：用 APScheduler（MIT）按站点包 `jobs.yml` 跑定时活。

为什么不自己写调度（2026-10-21）
------------------------------
cron 解析、错过补跑、并发防护、异常隔离都要写，写出来还不如别人的稳。
APScheduler 是纯 Python、无外部依赖、MIT，单进程后台线程就能用 —— 直接用它。

职责划分
-------
- 站点包 `jobs.yml`：**有哪些活、什么时候跑**（换单位只改包）
- 本文件：注册/启停/调用；具体活从 `JOBS` 白名单取（不给包任意执行能力）
- 业务函数自己负责幂等（同一天跑两次不能重复发）

安全边界：`JOBS` 里只登记本仓库的函数；包里的 `fn` 不在白名单就拒绝注册并报错。
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import yaml

_SCHED = {'s': None}
RUNS: list[dict] = []          # 最近几次触发记录（供 /api 查看与测试断言）


# ── 具体活的白名单（加一项活 = 加一行 + 在 jobs.yml 引用）────────────────
def _job_noop(**kwargs) -> str:
    """空活：部署自检用（验证调度通不通，不产生副作用）。"""
    return 'noop'


def _job_alerts_daily(**kwargs) -> str:
    """每日预警：调现有预警入口（需要业务库；库不可用会抛，由调度器记日志）。"""
    from app.secretary import alerts
    fn = getattr(alerts, 'daily', None)
    if fn is None:
        raise RuntimeError('alerts.daily 不存在：该活需要在有业务库的环境里跑')
    return str(fn(**kwargs))


def _job_dispatch_tick(**kwargs) -> str:
    """催办/升级/自动核销心跳。间隔写在 jobs.yml 的 cron 里，不写在这个文件。"""
    from app.core.database import SessionLocal
    from app.services import dispatch_service
    db = SessionLocal()
    try:
        r = dispatch_service.tick(db)
        return 'tick %s' % str(r)[:180]
    finally:
        db.close()


def _job_dispatch_scan(**kwargs) -> str:
    """跑一轮预警扫描（四类预警算完落库）。

    几点跑**不写在这里**，写在站点包 jobs.yml 的 cron 里；换单位/换作息只改包。
    """
    from app.core.database import SessionLocal
    from app.services import alert_service
    cadence = str(kwargs.get('cadence') or 'daily')
    db = SessionLocal()
    try:
        r = alert_service.scan(db, cadence=cadence)
        return 'cadence=%s found=%s new=%s resolved=%s' % (
            cadence, r.get('found'), r.get('new'), r.get('resolved'))
    finally:
        db.close()


JOBS = {
    'noop': _job_noop,
    'alerts_daily': _job_alerts_daily,
    'dispatch_scan': _job_dispatch_scan,
    'dispatch_tick': _job_dispatch_tick,
}


def pack_has(fn_name: str) -> bool:
    """站点包里有没有启用这个活（用于"包接管了就别再跑内置循环"）。"""
    return any((j.get('fn') == fn_name and j.get('enabled')) for j in _pack_jobs())


def _pack_jobs() -> list[dict]:
    """定时任务清单（原站点包 jobs.yml，2026-10-30 内联进 `project.JOBS`）。"""
    try:
        from app.secretary import project
        jobs = (project.JOBS or {}).get('jobs') or []
        return [j for j in jobs if isinstance(j, dict)]
    except Exception as exc:                       # noqa: BLE001
        print('[scheduler] 读定时任务失败：%s' % exc)
        return []


def build(scheduler=None):
    """按包注册定时任务。scheduler 传 None 时自己建 BackgroundScheduler。"""
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    sch = scheduler or BackgroundScheduler(timezone='Asia/Shanghai')
    added, skipped = [], []
    for spec in _pack_jobs():
        jid = str(spec.get('id') or '').strip()
        fn_name = str(spec.get('fn') or '').strip()
        cron = str(spec.get('cron') or '').strip()
        if not jid or not fn_name or not cron:
            skipped.append({'id': jid or '(空)', 'why': 'id/fn/cron 不全'})
            continue
        if not spec.get('enabled', True):
            skipped.append({'id': jid, 'why': 'enabled=false'})
            continue
        fn = JOBS.get(fn_name)
        if fn is None:
            skipped.append({'id': jid, 'why': 'fn 不在白名单：%s' % fn_name})
            continue
        kwargs = spec.get('args') or {}

        def _run(jid=jid, fn_name=fn_name, kwargs=kwargs, fn=fn):
            rec = {'job': jid, 'fn': fn_name, 'at': datetime.now().isoformat(timespec='seconds')}
            try:
                rec['result'] = str(fn(**kwargs))[:200]
            except Exception as exc:               # noqa: BLE001  单活失败不影响其它活
                rec['error'] = '%s: %s' % (type(exc).__name__, str(exc)[:200])
            RUNS.append(rec)
            if len(RUNS) > 200:
                del RUNS[:-200]

        try:
            sch.add_job(_run, CronTrigger.from_crontab(cron, timezone='Asia/Shanghai'),
                        id=jid, replace_existing=True, max_instances=1, coalesce=True)
            added.append({'id': jid, 'fn': fn_name, 'cron': cron})
        except Exception as exc:                   # noqa: BLE001
            skipped.append({'id': jid, 'why': 'cron 非法或注册失败：%s' % str(exc)[:80]})
    return {'scheduler': sch, 'added': added, 'skipped': skipped}


def start():
    """幂等启动：已启动就返回现有实例。"""
    if _SCHED['s'] is not None:
        return _SCHED['s']
    built = build()
    sch = built['scheduler']
    sch.start()
    _SCHED['s'] = sch
    print('[scheduler] 启动：注册 %d 条，跳过 %d 条'
          % (len(built['added']), len(built['skipped'])))
    return sch


def stop() -> None:
    sch = _SCHED.get('s')
    if sch is not None:
        try:
            sch.shutdown(wait=False)
        except Exception:                          # noqa: BLE001
            pass
        _SCHED['s'] = None


def status() -> dict:
    sch = _SCHED.get('s')
    jobs = []
    if sch is not None:
        for j in sch.get_jobs():
            jobs.append({'id': j.id, 'next': str(getattr(j, 'next_run_time', '') or '')})
    return {'running': sch is not None, 'jobs': jobs, 'recent': RUNS[-10:]}
