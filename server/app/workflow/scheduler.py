"""调度：调度方案存库，改完热重载，不重启进程。"""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from app.core.database import SessionLocalWF as SessionLocal
from app.workflow import docs as docsdb
from app.workflow.models import Schedule
from app.workflow.runner import run_doc

logger = logging.getLogger(__name__)
_SCHED: dict = {'s': None}
LAST: list[dict] = []


def validate_cron(cron: str) -> str:
    """返回错误文案；空串 = 合法。"""
    try:
        CronTrigger.from_crontab(str(cron or '').strip(), timezone='Asia/Shanghai')
        return ''
    except Exception as exc:            # noqa: BLE001
        return 'cron 不合法：%s' % str(exc)[:120]


def next_runs(crons, n: int = 5) -> list[str]:
    """crons 可传单个 cron 字符串，也可传列表；返回合并后最近的 n 个触发时间。"""
    import datetime as _dt
    items = [crons] if isinstance(crons, str) else list(crons or [])
    out: list[_dt.datetime] = []
    for c in items:
        try:
            trig = CronTrigger.from_crontab(str(c or '').strip(), timezone='Asia/Shanghai')
        except Exception:               # noqa: BLE001
            continue
        prev = None
        now = _dt.datetime.now(trig.timezone)
        for _ in range(n):
            nxt = trig.get_next_fire_time(prev, now if prev is None else prev)
            if nxt is None:
                break
            out.append(nxt)
            prev = nxt
    out.sort()
    return [x.strftime('%Y-%m-%d %H:%M') for x in out[:n]]


def run_schedule_now(sid: int, force: bool = True) -> dict:
    """跑一个方案下的所有启用文档（3 路并发，各自独立会话）。force=False 时停用的方案也跳过。"""
    import concurrent.futures as cf
    db = SessionLocal()
    try:
        sch = db.get(Schedule, int(sid))
        if sch is None:
            return {'skipped': 'schedule 不存在'}
        sched_name = sch.name
        if not force and not sch.enabled:
            return {'skipped': 'schedule 已停用'}
        targets = [(d.id, d.name) for d in docsdb.docs_of_schedule(db, sid) if d.enabled]
    finally:
        db.close()

    def _one(pair):
        doc_id, doc_name = pair
        s = SessionLocal()
        try:
            r = run_doc(s, doc_id, trigger='cron' if not force else 'manual')
            return {'doc': doc_name, 'run_id': r.get('run_id'), 'status': r.get('status'),
                    'hit_n': r.get('hit_n')}
        except Exception as exc:    # noqa: BLE001
            logger.warning('跑文档 %s 失败：%s', doc_name, exc)
            return {'doc': doc_name, 'status': 'failed', 'error': str(exc)[:200]}
        finally:
            s.close()

    out = []
    if targets:
        with cf.ThreadPoolExecutor(max_workers=3) as ex:
            out = list(ex.map(_one, targets))
    rec = {'schedule': sched_name, 'docs': out}
    LAST.append(rec)
    del LAST[:-50]
    return rec


def _run_schedule(sid: int) -> dict:
    """APScheduler 到点调用：方案停用就跳过。"""
    return run_schedule_now(sid, force=False)


def reload() -> dict:
    """按库重建所有 job。已启动就复用实例。"""
    sch = _SCHED.get('s')
    if sch is None:
        sch = BackgroundScheduler(timezone='Asia/Shanghai')
        sch.start()
        _SCHED['s'] = sch
    for job in list(sch.get_jobs()):
        job.remove()
    added, skipped = [], []
    db = SessionLocal()
    try:
        for s in db.execute(select(Schedule)).scalars().all():
            if not s.enabled:
                skipped.append({'id': s.id, 'why': '停用'})
                continue
            crons = [c for c in (s.crons or []) if str(c or '').strip()] or [s.cron]
            for idx, c in enumerate(crons):
                err = validate_cron(c)
                if err:
                    skipped.append({'id': s.id, 'why': err})
                    continue
                sch.add_job(_run_schedule, CronTrigger.from_crontab(
                    str(c).strip(), timezone=s.timezone or 'Asia/Shanghai'),
                    id='wf-%d-%d' % (s.id, idx), args=[s.id], replace_existing=True,
                    max_instances=1, coalesce=True)
                added.append({'id': s.id, 'name': s.name, 'cron': c})
    finally:
        db.close()
    return {'added': added, 'skipped': skipped}


def status() -> dict:
    sch = _SCHED.get('s')
    jobs = []
    if sch is not None:
        for j in sch.get_jobs():
            jobs.append({'id': j.id, 'next': str(getattr(j, 'next_run_time', '') or '')})
    return {'running': sch is not None, 'jobs': jobs, 'last': LAST[-10:]}


def stop() -> None:
    sch = _SCHED.get('s')
    if sch is not None:
        try:
            sch.shutdown(wait=False)
        except Exception:               # noqa: BLE001
            pass
        _SCHED['s'] = None

