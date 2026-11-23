"""触发器：按时间规则触发工作流。

规则存在预警库 `ad_wf.wf_trigger.rule` 里，不写死在代码：
`{"unit":"day|week|month","dow":[1..7],"dom":[1..28],"times":["08:00"]}`。
代码只负责把规则翻译成 cron，改规则不重启进程。
"""
from __future__ import annotations

import datetime as _dt
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from app.core.database import SessionLocalWF as SessionLocal
from app.wf import engine
from app.wf.models import Flow, Trigger, TriggerFlow

logger = logging.getLogger(__name__)
_SCHED: dict = {'s': None}

UNITS = ('day', 'week', 'month')
WEEK_NAME = {1: '周一', 2: '周二', 3: '周三', 4: '周四', 5: '周五', 6: '周六', 7: '周日'}
DAY_NAME = {1: 'mon', 2: 'tue', 3: 'wed', 4: 'thu', 5: 'fri', 6: 'sat', 7: 'sun'}


def _hm(t) -> tuple[int, int] | None:
    s = str(t or '').strip()
    if ':' not in s:
        return None
    hh, mm = s.split(':', 1)
    try:
        h, m = int(hh), int(mm)
    except ValueError:
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h, m


def normalize_rule(rule: dict | None, times: list | None = None) -> dict:
    """把前端传的规则补全并挡掉非法值。unit 缺省按每天。"""
    rule = dict(rule or {})
    unit = str(rule.get('unit') or 'day')
    if unit not in UNITS:
        unit = 'day'
    ts = [str(t) for t in (rule.get('times') or times or []) if _hm(t)]
    dow: list[int] = []
    dom: list[int] = []
    if unit == 'week':
        for x in rule.get('dow') or []:
            try:
                v = int(x)
            except (TypeError, ValueError):
                continue
            if 1 <= v <= 7 and v not in dow:
                dow.append(v)
        dow.sort()
        if not dow:
            dow = [1]
    if unit == 'month':
        for x in rule.get('dom') or []:
            try:
                v = int(x)
            except (TypeError, ValueError):
                continue
            if 1 <= v <= 28 and v not in dom:
                dom.append(v)
        dom.sort()
        if not dom:
            dom = [1]
    return {'unit': unit, 'dow': dow, 'dom': dom, 'times': ts}


def rule_of(trg: Trigger) -> dict:
    """取一行的规则；老数据只有 times，就当成每天。"""
    return normalize_rule(trg.rule or {}, trg.times or [])


def triggers_of(rule: dict) -> list[dict]:
    """把规则翻成 CronTrigger 的参数。

    ⚠️ 星期几必须用名字（mon/tue/...）：APScheduler 的 day_of_week 里 0 是周一，
    而 crontab 里 0 是周日 —— 直接传数字会把周一跑成周二（2026-11-23 实测踩到）。
    """
    out: list[dict] = []
    for t in rule.get('times') or []:
        hm = _hm(t)
        if hm is None:
            continue
        h, m = hm
        unit = rule.get('unit') or 'day'
        if unit == 'week':
            days = [DAY_NAME[int(d)] for d in (rule.get('dow') or [1]) if int(d) in DAY_NAME]
            out.append({'minute': m, 'hour': h, 'day_of_week': ','.join(days) or 'mon'})
        elif unit == 'month':
            days = [str(int(d)) for d in (rule.get('dom') or [1])]
            out.append({'minute': m, 'hour': h, 'day': ','.join(days) or '1'})
        else:
            out.append({'minute': m, 'hour': h})
    return out


def crons_of(rule: dict) -> list[str]:
    """给人看的 cron 原文（排错用）。"""
    out: list[str] = []
    for kw in triggers_of(rule):
        dow = kw.get('day_of_week') or '*'
        dom = kw.get('day') or '*'
        out.append('%d %d %s * %s' % (kw['minute'], kw['hour'], dom, dow))
    return out


def rule_text(rule: dict) -> str:
    """给人看的一句话。"""
    unit = rule.get('unit') or 'day'
    times = '、'.join(rule.get('times') or []) or '—'
    if unit == 'week':
        days = '、'.join(WEEK_NAME.get(int(d), str(d)) for d in rule.get('dow') or [])
        return '%s %s' % (days or '周一', times)
    if unit == 'month':
        days = '、'.join('%d 号' % int(d) for d in rule.get('dom') or [])
        return '每月 %s %s' % (days or '1 号', times)
    return '每天 %s' % times


def flows_of(db, trigger_id: int) -> list[Flow]:
    ids = [r for r in db.execute(select(TriggerFlow.flow_id)
                                 .where(TriggerFlow.trigger_id == int(trigger_id))
                                 .order_by(TriggerFlow.seq, TriggerFlow.id)).scalars().all()]
    if not ids:
        return []
    rows = db.execute(select(Flow).where(Flow.id.in_(ids), Flow.deleted_flag == 0)).scalars().all()
    order = {fid: i for i, fid in enumerate(ids)}
    return sorted(rows, key=lambda f: order.get(f.id, 999))


def run_trigger_now(trigger_id: int, force: bool = True) -> dict:
    """跑一个触发器下挂的所有启用工作流，一个接一个（后面的能读前一个刚写的表）。"""
    db = SessionLocal()
    try:
        trg = db.get(Trigger, int(trigger_id))
        if trg is None:
            return {'skipped': '触发器不存在'}
        name = trg.name
        if not force and not trg.enabled:
            return {'skipped': '触发器已停用'}
        flows = flows_of(db, trigger_id)
    finally:
        db.close()

    results = []
    for f in flows:
        if not f.enabled:
            results.append({'flow': f.name, 'status': 'skipped', 'reason': '工作流已停用'})
            continue
        s = SessionLocal()
        try:
            out = engine.run_flow(s, f, trigger='cron' if not force else 'manual')
            results.append({'flow': f.name, 'run_id': out.get('run_id'),
                            'status': out.get('status'), 'hit_n': out.get('hit_n'),
                            'written_n': out.get('written_n')})
        except Exception as exc:          # noqa: BLE001 - 一个失败不影响后面的
            logger.warning('跑工作流 %s 失败：%s', f.name, exc)
            results.append({'flow': f.name, 'status': 'failed', 'error': str(exc)[:300]})
        finally:
            s.close()
    return {'trigger': name, 'results': results}


def _tick(trigger_id: int) -> None:
    try:
        run_trigger_now(trigger_id, force=False)
    except Exception as exc:              # noqa: BLE001
        logger.warning('触发器 %s 调度失败：%s', trigger_id, exc)


def next_runs(rule: dict, n: int = 3) -> list[str]:
    """按规则算未来 n 次触发时间。"""
    out: list[_dt.datetime] = []
    now = _dt.datetime.now()
    for kw in triggers_of(rule):
        try:
            trig = CronTrigger(timezone='Asia/Shanghai', **kw)
        except Exception:                 # noqa: BLE001
            continue
        prev = None
        cur = now
        for _ in range(n):
            nxt = trig.get_next_fire_time(prev, cur)
            if nxt is None:
                break
            out.append(nxt)
            prev, cur = nxt, nxt
    out.sort()
    return [x.strftime('%Y-%m-%d %H:%M') for x in out[:n]]


def reload() -> dict:
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
        for trg in db.execute(select(Trigger)).scalars().all():
            if not trg.enabled:
                skipped.append({'id': trg.id, 'why': '停用'})
                continue
            rule = rule_of(trg)
            if not rule['times']:
                skipped.append({'id': trg.id, 'why': '没填时间点'})
                continue
            for idx, kw in enumerate(triggers_of(rule)):
                try:
                    trig = CronTrigger(timezone='Asia/Shanghai', **kw)
                except Exception as exc:   # noqa: BLE001
                    skipped.append({'id': trg.id, 'why': str(exc)[:80]})
                    continue
                sch.add_job(_tick, trig, id='wf-%d-%d' % (trg.id, idx), args=[trg.id],
                            replace_existing=True, max_instances=1, coalesce=True)
                added.append({'id': trg.id, 'name': trg.name,
                              'cron': '%d %d %s * %s' % (kw['minute'], kw['hour'],
                                                         kw.get('day') or '*',
                                                         kw.get('day_of_week') or '*')})
    finally:
        db.close()
    return {'added': added, 'skipped': skipped}


def status() -> dict:
    sch = _SCHED.get('s')
    jobs = []
    if sch is not None:
        for j in sch.get_jobs():
            jobs.append({'id': j.id, 'next': str(getattr(j, 'next_run_time', '') or '')})
    return {'running': sch is not None, 'jobs': jobs}


def stop() -> None:
    sch = _SCHED.get('s')
    if sch is not None:
        try:
            sch.shutdown(wait=False)
        except Exception:                 # noqa: BLE001
            pass
        _SCHED['s'] = None
