# -*- coding: utf-8 -*-
"""任务流转服务：下发 / 接单 / 方案 / 回执 / 催办 / 升级 / 自动核销 / tick。

纪律：
  1. 权限只认账号现查（creator / 主责 / 协办 / 抄送 / 上级 / admin），不认请求参数。
  2. 状态迁移白名单（ALLOWED），非法迁移抛 DispatchError(409)，不静默改。
  3. 每次动作都写 `dispatch_task_event`（append-only），时间线只读事件表。
  4. 通知 `dedupe_key` 唯一：tick 重复跑不会重复发。
  5. 自动核销只认规则跑出来的实测值；数据不可得时 `passed=None`，**不闭环**。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import Session

from app import dispatch_rules as DR
from app.models.dispatch import (
    AlertItem,
    DispatchAttachment,
    DispatchCheck,
    DispatchNotice,
    DispatchTask,
    DispatchTaskAssignee,
    DispatchTaskEvent,
    DispatchTaskPlan,
)
from app.secretary import alert_scope
from app.secretary import alerts as A

logger = logging.getLogger(__name__)

ALLOWED = {
    'draft': {'open', 'cancelled'},
    'open': {'doing', 'cancelled', 'open'},
    'doing': {'verify', 'cancelled', 'doing'},
    'verify': {'closed', 'doing', 'cancelled', 'verify'},
    'closed': {'doing'},
    'cancelled': set(),
}


class DispatchError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, detail=None):
        super().__init__(message)
        self.code, self.message, self.status, self.detail = code, message, status, detail


# ─────────────────────────────────────────────────────────── 小工具

def _now():
    return datetime.now(timezone.utc)


def _iso(v):
    return v.isoformat() if v else None


def _q(sql, args=None, limit=200):
    return A._all(sql, args, limit=limit)


def _one(sql, args=None):
    return A._one(sql, args)


def _f(v):
    return A._f(v)


def _i(v):
    return A._i(v)


def _emp(employee_id) -> dict | None:
    e = _one('SELECT employee_id, login_name, actual_name, department_id, disabled_flag, deleted_flag '
             'FROM t_employee WHERE employee_id=%s', [int(employee_id)])
    if not e:
        return None
    return {'employee_id': _i(e['employee_id']), 'login_name': e.get('login_name') or '',
            'name': e.get('actual_name') or '', 'dept_id': _i(e.get('department_id')),
            'disabled': _i(e.get('disabled_flag')) == 1 or _i(e.get('deleted_flag')) == 1}


def _emp_by_login(login: str) -> dict | None:
    e = _one('SELECT employee_id FROM t_employee WHERE login_name=%s AND deleted_flag=0 LIMIT 1', [login])
    return _emp(e['employee_id']) if e else None


def _emp_by_role(role_code: str, dept_id) -> list[dict]:
    rows = _q('SELECT DISTINCT e.employee_id FROM t_employee e '
              'JOIN t_role_employee re ON re.employee_id=e.employee_id '
              'JOIN t_role r ON r.role_id=re.role_id '
              'WHERE e.deleted_flag=0 AND e.disabled_flag=0 AND r.role_code=%s AND e.department_id=%s',
              [role_code, int(dept_id)], limit=50)
    return [x for x in (_emp(r['employee_id']) for r in rows) if x]


def _role_of(employee_id) -> str | None:
    from app.secretary import alert_rules as R
    rows = _q('SELECT r.role_code FROM t_role_employee re '
              'JOIN t_role r ON r.role_id=re.role_id WHERE re.employee_id=%s',
              [int(employee_id)], limit=10)
    codes = [str(r['role_code']) for r in rows if r.get('role_code')]
    if not codes:
        return None
    return next((c for c in R.ROLE_PRIORITY if c in codes), codes[0])


def _station(station_id) -> dict:
    r = _one('SELECT id, name, parent_id, department_id FROM t_power_company WHERE id=%s', [int(station_id)])
    return {'id': _i(r.get('id')), 'name': r.get('name') or '',
            'parent_id': _i(r.get('parent_id')), 'dept_id': _i(r.get('department_id'))} if r else {}


def _dept(dept_id) -> dict:
    r = _one('SELECT department_id, department_name, parent_id FROM t_department WHERE department_id=%s',
             [int(dept_id)])
    return {'id': _i(r.get('department_id')), 'name': r.get('department_name') or '',
            'parent_id': _i(r.get('parent_id'))} if r else {}


def _level_of(alert) -> str:
    if alert.scope_type == 'station':
        return 'station'
    if alert.scope_type == 'county':
        return 'county'
    return 'city'


def _target_role(alert) -> tuple[str, str]:
    """返回 (承办角色, 依据)。"""
    st = alert.subtype or ''
    if st == 'project.approval_stay':
        node = (alert.data or {}).get('node') or ''
        role = DR.NODE_ROLE.get(node)
        if role:
            return role, '按审批节点「%s」的应办角色' % node
    if st.startswith('metric.'):
        code = ((alert.data or {}).get('worst') or {}).get('code') \
            or (alert.data or {}).get('metric') or ''
        role = DR.METRIC_ROLE.get(code)
        if role:
            return role, '按指标「%s」的应办角色' % code
    lv = _level_of(alert)
    if lv == 'station':
        return 'sz', '本所预算/项目由所长承办'
    if lv == 'county':
        return 'xgscwb', '县公司口径由县财务承办'
    return 'sgscwb', '全市口径由市财务承办'


def recommend(db: Session, alert: AlertItem) -> dict:
    """推荐承办人（**只读**，不落库）：按范围 + 应办角色找人，找不到就往上找。"""
    role, reason = _target_role(alert)
    lv = _level_of(alert)
    if lv == 'station':
        st = _station(alert.scope_key)
        dept_id = st.get('dept_id')
        if not dept_id:
            return {'role': role, 'dept_id': None, 'level': lv, 'reason': reason,
                    'candidates': [], 'fallback': '找不到该所部门'}
        dept = _dept(dept_id)
    elif lv == 'county':
        dept_id = _i(alert.scope_key)
        dept = _dept(dept_id)
    else:
        dept_id = 1
        dept = _dept(1)

    tried = []
    cur = dept_id
    for _ in range(4):
        cands = _emp_by_role(role, cur)
        tried.append({'dept_id': cur, 'dept_name': (_dept(cur) or {}).get('name'), 'n': len(cands)})
        if cands:
            cands.sort(key=lambda c: (c['disabled'], c['employee_id']))
            return {'role': role, 'dept_id': cur, 'level': lv, 'reason': reason,
                    'candidates': cands[:10], 'fallback': None if cur == dept_id else '本级无该角色，已上溯',
                    'tried': tried}
        d = _dept(cur)
        nxt = d.get('parent_id')
        if not nxt or nxt in (0, cur) or nxt == cur:
            break
        cur = nxt
    return {'role': role, 'dept_id': dept_id, 'level': lv, 'reason': reason,
            'candidates': [], 'fallback': '该职责线在当前数据里没有账号，需人工指定', 'tried': tried}


# ─────────────────────────────────────────────────────────── 事件 / 通知

def _event(db: Session, task: DispatchTask, action: str, actor: dict | None,
           from_status=None, to_status=None, content=None, payload=None):
    ev = DispatchTaskEvent(
        task_id=task.id, action=action,
        actor_id=(actor or {}).get('app_user_id') or (actor or {}).get('employee_id'),
        actor_name=(actor or {}).get('name'), actor_role=(actor or {}).get('role_code'),
        from_status=from_status, to_status=to_status, content=content,
        payload=payload or {})
    db.add(ev)
    db.flush()
    return ev


def _notice(db: Session, task: DispatchTask | None, ntype: str, receiver: dict,
            title: str, content: str, dedupe: str | None = None):
    if dedupe:
        exists = db.execute(select(DispatchNotice.id).where(
            DispatchNotice.dedupe_key == dedupe)).first()
        if exists:
            return None
    n = DispatchNotice(task_id=task.id if task else None, notice_type=ntype,
                       receiver_id=receiver.get('app_user_id'),
                       receiver_login=receiver.get('login_name'),
                       receiver_name=receiver.get('name'),
                       title=title[:200], content=content, dedupe_key=dedupe)
    db.add(n)
    db.flush()          # 会话是 autoflush=False：不 flush 就查不到刚加的通知
    return n


def _actor(account: str) -> dict:
    sc = alert_scope.resolve(account)
    if not sc.get('ok'):
        raise DispatchError('UNKNOWN_ACCOUNT', '账号解析失败：%s' % (sc.get('error') or ''), 403)
    return {'employee_id': sc['employee_id'], 'login_name': sc['account'],
            'name': sc.get('name') or '', 'role_code': sc.get('role_code'),
            'dept_id': sc.get('dept_id'), 'scope': sc, 'app_user_id': None}


# ─────────────────────────────────────────────────────────── 任务可见性

def _assignees(db: Session, task_id: int) -> list[DispatchTaskAssignee]:
    return db.execute(select(DispatchTaskAssignee).where(
        DispatchTaskAssignee.task_id == task_id)).scalars().all()


def _my_parts(db: Session, task_id: int, employee_id: int) -> list[str]:
    rows = db.execute(select(DispatchTaskAssignee.part).where(
        DispatchTaskAssignee.task_id == task_id,
        DispatchTaskAssignee.employee_id == employee_id)).scalars().all()
    return list(rows)


def _identities(db: Session, task: DispatchTask, act: dict) -> set[str]:
    """一个人的全部身份（可能同时是发起人 + 主责，别只取一个）。"""
    out: set[str] = set()
    if task.creator_id == act.get('employee_id'):
        out.add('creator')
    if task.escalated_to and int(task.escalated_to) == int(act.get('employee_id') or 0):
        out.add('superior')
    out.update(_my_parts(db, task.id, int(act.get('employee_id') or 0)))
    if act.get('role_code') == 'admin':
        out.add('admin')
    return out


def _access(db: Session, task: DispatchTask, act: dict) -> str | None:
    ids = _identities(db, task, act)
    for k in ('creator', 'owner', 'superior', 'collaborator', 'cc', 'admin'):
        if k in ids:
            return k
    return None


def _need(db: Session, task: DispatchTask, act: dict, *roles) -> str:
    ids = _identities(db, task, act)
    hit = ids & set(roles)
    if not hit:
        raise DispatchError('NOT_PARTICIPANT',
                            '无权操作该任务（当前身份：%s）' % ('/'.join(sorted(ids)) or '无'), 403)
    for k in ('creator', 'owner', 'superior', 'admin', 'collaborator', 'cc'):
        if k in hit:
            return k
    return sorted(hit)[0]


def _move(task: DispatchTask, to_status: str):
    if to_status not in (ALLOWED.get(task.status) or set()):
        raise DispatchError('INVALID_TRANSITION', '不允许从 %s 迁移到 %s' % (task.status, to_status), 409,
                            {'from': task.status, 'to': to_status})
    from_status = task.status
    task.status = to_status
    return from_status


# ─────────────────────────────────────────────────────────── 下发

def _next_task_no(db: Session) -> str:
    day = time.strftime('%Y%m%d')
    n = db.execute(select(func.count()).select_from(DispatchTask).where(
        DispatchTask.task_no.like('D%s-%%' % day))).scalar_one()
    return 'D%s-%04d' % (day, int(n) + 1)


def _idem_task(db: Session, key: str) -> DispatchTask | None:
    if not key:
        return None
    row = db.execute(select(DispatchTaskEvent.task_id).where(
        DispatchTaskEvent.payload['idem'].astext == str(key)).limit(1)).first()
    if not row:
        return None
    return db.get(DispatchTask, int(row[0]))


def create(db: Session, account: str, alert_id: int, body: dict) -> dict:
    """确认下发：task + assignees + event + notice 同一事务。"""
    act = _actor(account)
    alert = db.get(AlertItem, int(alert_id))
    if alert is None:
        raise DispatchError('ALERT_NOT_FOUND', '预警不存在', 404)
    sc = act['scope']
    if alert.assignee_role and alert.assignee_role != sc.get('role_code'):
        raise DispatchError('NOT_PARTICIPANT', '这条预警不是派给当前角色的', 403)
    idem = (body.get('idempotency_key') or '').strip()
    dup = _idem_task(db, idem) if idem else None
    if dup:
        return task_detail(db, account, dup.id)

    assignees_in = body.get('assignees') or []
    if not assignees_in:
        raise DispatchError('NO_ASSIGNEE', '至少要有一个主责', 422)
    owners = [x for x in assignees_in if (x.get('part') or 'owner') == 'owner']
    if not owners:
        raise DispatchError('NO_OWNER', '至少要有一个主责（part=owner）', 422)

    now = _now()
    task = DispatchTask(
        task_no=_next_task_no(db), task_type=body.get('task_type') or 'alert',
        title=(body.get('title') or alert.title)[:200],
        requirement=body.get('requirement') or '',
        status='open', level=body.get('level') or alert.level,
        alert_id=alert.id, scope_type=alert.scope_type, scope_key=alert.scope_key,
        scope_name=alert.scope_name, biz_type=alert.object_type,
        biz_keys=[alert.object_key] if alert.object_key and alert.object_key != '*' else [],
        creator_id=int(act['employee_id']), creator_name=act['name'],
        creator_login=act['login_name'], creator_dept_id=act.get('dept_id'),
        require_plan=int(body.get('require_plan') or 0),
        verifier_type=body.get('verifier_type') or 'auto',
        verify_rule=body.get('verify_rule') or alert.verify_hint or {},
        reply_due_at=_parse_dt(body.get('reply_due_at')) or
        (now + timedelta(hours=DR.reply_hours(alert.subtype, alert.level))),
        due_at=_parse_dt(body.get('due_at')) or
        (now + timedelta(days=DR.target_days(alert.subtype))),
    )
    db.add(task)
    db.flush()
    task.root_task_id = task.root_task_id or task.id

    for a in assignees_in:
        emp = _emp(a.get('employee_id'))
        if not emp or emp['disabled']:
            raise DispatchError('BAD_ASSIGNEE', '承办人不存在或已停用：%s' % a.get('employee_id'), 422)
        part = a.get('part') or 'owner'
        if part not in ('owner', 'collaborator', 'cc'):
            raise DispatchError('BAD_PART', 'part 只能是 owner/collaborator/cc', 422)
        db.add(DispatchTaskAssignee(
            task_id=task.id, part=part, employee_id=emp['employee_id'],
            login_name=emp['login_name'], name=emp['name'], dept_id=emp['dept_id'],
            dept_name=(_dept(emp['dept_id']) or {}).get('name'),
            role_code=a.get('role_code') or _role_of(emp['employee_id']),
            state='pending' if part != 'cc' else 'done'))

    _event(db, task, 'confirm', act, 'draft', 'open', body.get('requirement'),
           {'idem': idem, 'assignees': len(assignees_in)})
    for a in assignees_in:
        emp = _emp(a.get('employee_id'))
        if not emp:
            continue
        _notice(db, task, 'assign', emp, '【新任务】%s' % task.title,
                '要求：%s\n回执时限：%s' % (task.requirement, _iso(task.reply_due_at)),
                dedupe='task:%d:assign:%s' % (task.id, emp['login_name']))

    alert.status = 'dispatched'
    alert.task_id = task.id
    db.commit()
    return task_detail(db, account, task.id)


def _parse_dt(v):
    if not v:
        return None
    if isinstance(v, datetime):
        return v
    s = str(v).strip().replace('Z', '+00:00')
    try:
        d = datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# ─────────────────────────────────────────────────────────── 动作

def accept(db: Session, account: str, task_id: int) -> dict:
    act = _actor(account)
    t = _get_task(db, task_id)
    _need(db, t, act, 'owner')
    if t.status != 'open':
        raise DispatchError('INVALID_TRANSITION', '任务当前不是待接单（%s）' % t.status, 409)
    fs = _move(t, 'doing')
    for a in _assignees(db, t.id):
        if a.employee_id == act['employee_id'] and a.part == 'owner':
            a.state = 'accepted'
            a.accepted_at = _now()
    _event(db, t, 'accept', act, fs, 'doing')
    _notify_creator(db, t, 'reply', '【已接单】%s' % t.title, '%s 已接单' % act['name'])
    db.commit()
    return task_detail(db, account, t.id)


def decline(db: Session, account: str, task_id: int, reason: str = '') -> dict:
    act = _actor(account)
    t = _get_task(db, task_id)
    _need(db, t, act, 'owner')
    if t.status != 'open':
        raise DispatchError('INVALID_TRANSITION', '只能拒接待接单任务', 409)
    for a in _assignees(db, t.id):
        if a.employee_id == act['employee_id'] and a.part == 'owner':
            a.state = 'declined'
            a.declined_reason = (reason or '')[:500]
    t.escalate_level = int(t.escalate_level or 0) + 1
    _event(db, t, 'decline', act, 'open', 'open', reason)
    _notify_creator(db, t, 'escalate', '【拒接】%s' % t.title,
                    '%s 拒接：%s' % (act['name'], reason or '未填理由'))
    db.commit()
    return task_detail(db, account, t.id)


def submit_plan(db: Session, account: str, task_id: int, content: str, items=None) -> dict:
    act = _actor(account)
    t = _get_task(db, task_id)
    _need(db, t, act, 'owner')
    if t.status not in ('open', 'doing'):
        raise DispatchError('INVALID_TRANSITION', '当前状态不能提交方案（%s）' % t.status, 409)
    ver = db.execute(select(func.coalesce(func.max(DispatchTaskPlan.version), 0)).where(
        DispatchTaskPlan.task_id == t.id, DispatchTaskPlan.kind == 'owner_plan')).scalar_one()
    db.add(DispatchTaskPlan(task_id=t.id, kind='owner_plan', version=int(ver) + 1,
                            title='实施方案', content=content, items=items,
                            source='human', author_id=act['employee_id'], author_name=act['name']))
    fs = t.status
    if fs == 'open':
        t.status = 'doing'
    _event(db, t, 'plan_submit', act, fs, 'doing', content)
    _notify_creator(db, t, 'plan', '【方案待确认】%s' % t.title, '%s 提交了实施方案' % act['name'])
    db.commit()
    return task_detail(db, account, t.id)


def accept_plan(db: Session, account: str, task_id: int, plan_id: int, ok: bool = True) -> dict:
    act = _actor(account)
    t = _get_task(db, task_id)
    _need(db, t, act, 'creator', 'superior', 'admin')
    p = db.get(DispatchTaskPlan, int(plan_id))
    if p is None or p.task_id != t.id:
        raise DispatchError('PLAN_NOT_FOUND', '方案不存在', 404)
    p.accepted = 1 if ok else 0
    p.accepted_by = act['employee_id']
    p.accepted_at = _now()
    _event(db, t, 'plan_accept' if ok else 'plan_reject', act, t.status, t.status,
           '方案%s' % ('通过' if ok else '驳回'))
    for a in _assignees(db, t.id):
        if a.part == 'owner':
            emp = _emp(a.employee_id) or {'login_name': a.login_name, 'name': a.name}
            _notice(db, t, 'plan', emp, ('【方案通过】' if ok else '【方案驳回】') + t.title,
                    '发起人已%s方案' % ('通过' if ok else '驳回'),
                    dedupe='task:%d:plan:%d:%s:%s' % (t.id, p.id, 'ok' if ok else 'no',
                                                      a.login_name))
    db.commit()
    return task_detail(db, account, t.id)


def reply(db: Session, account: str, task_id: int, content: str, attachments=None,
          attachment_ids=None) -> dict:
    act = _actor(account)
    t = _get_task(db, task_id)
    _need(db, t, act, 'owner')
    if t.status not in ('open', 'doing'):
        raise DispatchError('INVALID_TRANSITION', '当前状态不能提交回执（%s）' % t.status, 409)
    fs = _move(t, 'verify')
    for a in _assignees(db, t.id):
        if a.employee_id == act['employee_id'] and a.part == 'owner':
            a.state = 'replied'
            a.replied_at = _now()
    ev = _event(db, t, 'reply', act, fs, 'verify', content,
                {'attachments': attachments or [], 'attachment_ids': attachment_ids or []})
    db.flush()
    if attachment_ids:
        rows = db.execute(select(DispatchAttachment).where(
            DispatchAttachment.task_id == t.id,
            DispatchAttachment.id.in_([int(x) for x in attachment_ids]))).scalars().all()
        for att in rows:
            att.event_id = ev.id
    _notify_creator(db, t, 'reply', '【回执待核销】%s' % t.title, '%s 提交了回执' % act['name'])
    db.commit()
    return task_detail(db, account, t.id)


def close(db: Session, account: str, task_id: int, conclusion: str = '', reason: str = 'human_confirmed') -> dict:
    act = _actor(account)
    t = _get_task(db, task_id)
    _need(db, t, act, 'creator', 'superior', 'admin')
    if t.status != 'verify':
        raise DispatchError('INVALID_TRANSITION', '只有待核销任务可以核销（当前 %s）' % t.status, 409)
    fs = _move(t, 'closed')
    t.closed_at = _now()
    t.close_reason = reason
    _event(db, t, 'close', act, fs, 'closed', conclusion, {'reason': reason})
    _notify_all(db, t, 'close', '【已闭环】%s' % t.title, conclusion or '任务已核销')
    db.commit()
    return task_detail(db, account, t.id)


def reopen(db: Session, account: str, task_id: int) -> dict:
    act = _actor(account)
    t = _get_task(db, task_id)
    _need(db, t, act, 'creator', 'admin')
    if t.status != 'closed':
        raise DispatchError('INVALID_TRANSITION', '只有已闭环任务可以重开', 409)
    if t.closed_at and (_now() - t.closed_at).days > 7:
        raise DispatchError('REOPEN_EXPIRED', '闭环超过 7 天不能重开', 409)
    fs = _move(t, 'doing')
    t.closed_at = None
    t.close_reason = None
    _event(db, t, 'reopen', act, fs, 'doing')
    _notify_all(db, t, 'reopen', '【任务重开】%s' % t.title, '发起人重新打开了任务')
    db.commit()
    return task_detail(db, account, t.id)


def transfer(db: Session, account: str, task_id: int, to_employee_id: int) -> dict:
    act = _actor(account)
    t = _get_task(db, task_id)
    _need(db, t, act, 'owner', 'creator', 'superior', 'admin')
    emp = _emp(to_employee_id)
    if not emp or emp['disabled']:
        raise DispatchError('BAD_ASSIGNEE', '承接人不存在或已停用', 422)
    for a in _assignees(db, t.id):
        if a.part == 'owner' and a.employee_id == act['employee_id'] and t.creator_id != act['employee_id']:
            a.part = 'collaborator'
    db.add(DispatchTaskAssignee(
        task_id=t.id, part='owner', employee_id=emp['employee_id'],
        login_name=emp['login_name'], name=emp['name'], dept_id=emp['dept_id'],
        dept_name=(_dept(emp['dept_id']) or {}).get('name'),
        role_code=_role_of(emp['employee_id']), state='pending'))
    _event(db, t, 'transfer', act, t.status, t.status, '转派给 %s' % emp['name'])
    _notice(db, t, 'assign', emp, '【转派任务】%s' % t.title, '由 %s 转派' % act['name'],
            dedupe='task:%d:transfer:%s:%d' % (t.id, emp['login_name'], int(time.time())))
    db.commit()
    return task_detail(db, account, t.id)


def subtask(db: Session, account: str, task_id: int, body: dict) -> dict:
    act = _actor(account)
    parent = _get_task(db, task_id)
    _need(db, parent, act, 'owner', 'creator', 'superior', 'admin')
    assignees_in = body.get('assignees') or []
    if not assignees_in:
        raise DispatchError('NO_ASSIGNEE', '子任务至少要有一个主责', 422)
    now = _now()
    child = DispatchTask(
        task_no=_next_task_no(db), task_type='alert', title=(body.get('title') or parent.title)[:200],
        requirement=body.get('requirement') or parent.requirement, status='open',
        level=body.get('level') or parent.level, parent_task_id=parent.id,
        root_task_id=parent.root_task_id or parent.id,
        alert_id=parent.alert_id, scope_type=parent.scope_type, scope_key=parent.scope_key,
        scope_name=parent.scope_name, biz_type=parent.biz_type, biz_keys=parent.biz_keys,
        creator_id=int(act['employee_id']), creator_name=act['name'],
        creator_login=act['login_name'], creator_dept_id=act.get('dept_id'),
        require_plan=int(body.get('require_plan') or 0),
        verifier_type=body.get('verifier_type') or 'human',
        verify_rule=body.get('verify_rule') or {},
        reply_due_at=_parse_dt(body.get('reply_due_at')) or now + timedelta(hours=72),
        due_at=_parse_dt(body.get('due_at')) or now + timedelta(days=30),
    )
    db.add(child)
    db.flush()
    for a in assignees_in:
        emp = _emp(a.get('employee_id'))
        if not emp or emp['disabled']:
            raise DispatchError('BAD_ASSIGNEE', '承办人不存在或已停用', 422)
        part = a.get('part') or 'owner'
        db.add(DispatchTaskAssignee(
            task_id=child.id, part=part, employee_id=emp['employee_id'],
            login_name=emp['login_name'], name=emp['name'], dept_id=emp['dept_id'],
            dept_name=(_dept(emp['dept_id']) or {}).get('name'),
            role_code=_role_of(emp['employee_id']), state='pending'))
        _notice(db, child, 'assign', emp, '【子任务】%s' % child.title,
                '要求：%s' % child.requirement, dedupe='task:%d:assign:%s' % (child.id, emp['login_name']))
    _event(db, parent, 'subtask', act, parent.status, parent.status,
           '继续下发给 %d 人' % len(assignees_in), {'child_task_id': child.id})
    db.commit()
    return task_detail(db, account, child.id)


def urge(db: Session, account: str, task_id: int, note: str = '') -> dict:
    act = _actor(account)
    t = _get_task(db, task_id)
    _need(db, t, act, 'creator', 'superior', 'admin')
    _event(db, t, 'urge', act, t.status, t.status, note or '手动催办')
    for a in _assignees(db, t.id):
        if a.part == 'owner':
            _notice(db, t, 'urge', '', '【催办】%s' % t.title, note or '请尽快处理',
                    dedupe='task:%d:urge:%s:%s' % (t.id, a.login_name, time.strftime('%Y%m%d')))
    db.commit()
    return task_detail(db, account, t.id)


def supervise(db: Session, account: str, task_id: int, note: str = '') -> dict:
    act = _actor(account)
    t = _get_task(db, task_id)
    _need(db, t, act, 'creator', 'superior', 'admin')
    _event(db, t, 'supervise', act, t.status, t.status, note or '督办')
    for a in _assignees(db, t.id):
        if a.part == 'owner':
            emp = _emp(a.employee_id) or {'login_name': a.login_name, 'name': a.name}
            _notice(db, t, 'supervise', emp, '【督办】%s' % t.title, note or '领导督办，请限期办理',
                    dedupe='task:%d:supervise:%s:%d' % (t.id, a.login_name, int(time.time())))
    db.commit()
    return task_detail(db, account, t.id)


def escalate(db: Session, account: str, task_id: int, note: str = '') -> dict:
    act = _actor(account)
    t = _get_task(db, task_id)
    _need(db, t, act, 'creator', 'superior', 'admin')
    got = _escalate_once(db, t, note or '手动上报', act)
    db.commit()
    return dict(task_detail(db, account, t.id), escalated=got)


def cancel(db: Session, account: str, task_id: int, note: str = '') -> dict:
    act = _actor(account)
    t = _get_task(db, task_id)
    _need(db, t, act, 'creator', 'admin')
    fs = _move(t, 'cancelled')
    _event(db, t, 'cancel', act, fs, 'cancelled', note)
    _notify_all(db, t, 'close', '【已撤回】%s' % t.title, note or '发起人撤回任务')
    db.commit()
    return task_detail(db, account, t.id)


def archive(db: Session, account: str, task_id: int) -> dict:
    act = _actor(account)
    t = _get_task(db, task_id)
    _need(db, t, act, 'creator', 'admin')
    if t.status != 'closed':
        raise DispatchError('INVALID_TRANSITION', '只有闭环任务可以归档', 409)
    t.archived_flag = 1
    _event(db, t, 'archive', act, 'closed', 'closed')
    db.commit()
    return task_detail(db, account, t.id)


# ─────────────────────────────────────────────────────────── 通知辅助

def _notify_creator(db: Session, t: DispatchTask, ntype: str, title: str, content: str):
    emp = _emp_by_login(t.creator_login)
    if emp:
        _notice(db, t, ntype, emp, title, content,
                dedupe='task:%d:%s:creator:%s' % (t.id, ntype, time.strftime('%Y%m%d%H%M')))


def _notify_all(db: Session, t: DispatchTask, ntype: str, title: str, content: str):
    for a in _assignees(db, t.id):
        emp = _emp(a.employee_id) or {'login_name': a.login_name, 'name': a.name}
        _notice(db, t, ntype, emp, title, content,
                dedupe='task:%d:%s:%s' % (t.id, ntype, a.login_name))
    _notify_creator(db, t, ntype, title, content)


# ─────────────────────────────────────────────────────────── 升级

def _escalate_once(db: Session, t: DispatchTask, reason: str, actor: dict | None = None) -> bool:
    """升级一级：只在职责线内找上一级；市领导不接执行层。"""
    cat = _category_of_task(db, t)
    owners = [a for a in _assignees(db, t.id) if a.part == 'owner']
    role = (owners[0].role_code if owners and owners[0].role_code else None) or 'sz'
    level = int(t.escalate_level or 0) + 1
    if level > 2:
        return False
    today = _now().date()
    recent = db.execute(select(DispatchTaskEvent.create_time).where(
        DispatchTaskEvent.task_id == t.id,
        DispatchTaskEvent.action == 'escalate').order_by(
        DispatchTaskEvent.id.desc()).limit(1)).first()
    if recent and recent[0] and recent[0].date() == today:
        return False
    already = db.execute(select(DispatchTaskEvent.id).where(
        DispatchTaskEvent.task_id == t.id,
        DispatchTaskEvent.action == 'escalate',
        DispatchTaskEvent.payload['level'].astext == str(level))).first()
    if already:
        return False

    receivers: list[dict] = []
    for nxt in DR.next_roles(cat, role):
        if nxt == 'master' and DR.MASTER_ESCALATION_BLOCK and t.status != 'closed':
            break
        emps = _emp_by_role(nxt, owners[0].dept_id if owners else 1) or _emp_by_role(nxt, 1)
        if emps:
            receivers = emps[:3]
            break
    if not receivers:
        emp = _emp_by_login(t.creator_login)
        if emp:
            receivers = [emp]
    t.escalate_level = level
    t.overdue_flag = 1
    if receivers:
        t.escalated_to = receivers[0]['employee_id']
    _event(db, t, 'escalate', actor, t.status, t.status, reason,
           {'level': level, 'receivers': [r['login_name'] for r in receivers]})
    db.flush()      # 同一次 tick 里再判"今天是否已升级"必须看得到这条
    for r in receivers:
        _notice(db, t, 'escalate', r, '【升级】%s' % t.title,
                '任务未按时办理，已升级到你：%s' % reason,
                dedupe='task:%d:escalate:%d:%s' % (t.id, level, r.get('login_name') or ''))
    return True


def _category_of_task(db: Session, t: DispatchTask) -> str:
    if t.alert_id:
        a = db.get(AlertItem, int(t.alert_id))
        if a is not None:
            return a.alert_type
    return 'project'


# ─────────────────────────────────────────────────────────── 核销

def check_task(db: Session, t: DispatchTask) -> dict:
    """按 verify_rule 跑一次实测；返回 {passed, measured, message}。"""
    rule = t.verify_rule or {}
    kind = rule.get('kind')
    try:
        if kind == 'budget_rate':
            got = _check_budget(rule)
        elif kind == 'audit_node':
            got = _check_audit_node(rule)
        elif kind == 'status_count':
            got = _check_status_count(rule)
        elif kind == 'metric_value':
            got = _check_metric(rule)
        elif kind == 'task_tree':
            got = _check_tree(db, t)
        else:
            return {'passed': None, 'measured': {}, 'message': '人工核销规则（%s）' % (kind or 'manual')}
    except Exception as exc:  # noqa: BLE001 - 数据不可得不算通过
        logger.warning('核销规则执行失败 task=%s：%s', t.id, exc)
        return {'passed': None, 'measured': {}, 'message': '数据不可得：%s' % str(exc)[:200]}

    op, target = rule.get('op'), rule.get('target')
    val = got.get('value')
    passed = None
    if val is not None and target is not None and op:
        try:
            v, tg = float(val), float(target)
            passed = {'>=': v >= tg, '<=': v <= tg, '>': v > tg, '<': v < tg}.get(op)
        except (TypeError, ValueError):
            passed = None
    got['passed'] = passed
    got['message'] = '实测 %s %s 目标 %s' % (val, op, target)
    return got


def _station_marks(ids):
    return ','.join(['%s'] * len(ids))


def _check_budget(rule) -> dict:
    ids = [int(x) for x in (rule.get('station_ids') or [])]
    if not ids:
        return {'value': None, 'measured': {}}
    mk = _station_marks(ids)
    year = int(rule.get('year') or time.strftime('%Y'))
    alloc = _f(_one('SELECT COALESCE(SUM(budget_amount),0) AS n FROM t_power_company_budget_result '
                    'WHERE deleted_flag=0 AND year=%s AND power_station_id IN (' + mk + ')',
                    [year] + ids).get('n'))
    appr = _f(_one("SELECT COALESCE(SUM(budget_total),0) AS n FROM t_power_work_order "
                   "WHERE deleted_flag='0' AND work_status IN ('3','6','7') "
                   'AND YEAR(create_time)=%s AND power_company_id IN (' + mk + ')',
                   [year] + ids).get('n'))
    rate = (appr / alloc) if alloc > 0 else 0.0
    return {'value': round(rate, 4), 'measured': {'alloc': round(alloc, 2), 'approved': round(appr, 2),
                                                  'rate': round(rate, 4), 'year': year}}


def _check_audit_node(rule) -> dict:
    ids = [int(x) for x in (rule.get('station_ids') or [])]
    node = rule.get('node') or ''
    if not ids or not node:
        return {'value': None, 'measured': {}}
    mk = _station_marks(ids)
    n = _i(_one('SELECT COUNT(*) AS n FROM t_work_order_audit_task a '
               'JOIN t_power_work_order w ON w.id=a.work_order_id '
               'WHERE a.is_deleted=0 AND a.task_status=0 AND a.node_name=%s '
               "AND w.deleted_flag='0' AND w.power_company_id IN (" + mk + ')',
               [node] + ids).get('n'))
    return {'value': n, 'measured': {'node': node, 'pending': n}}


def _check_status_count(rule) -> dict:
    ids = [int(x) for x in (rule.get('station_ids') or [])]
    st = str(rule.get('status') or '')
    if not ids or not st:
        return {'value': None, 'measured': {}}
    mk = _station_marks(ids)
    n = _i(_one("SELECT COUNT(*) AS n FROM t_power_work_order WHERE deleted_flag='0' "
                'AND work_status=%s AND YEAR(create_time)=%s AND power_company_id IN (' + mk + ')',
                [st, int(rule.get('year') or time.strftime('%Y'))] + ids).get('n'))
    return {'value': n, 'measured': {'status': st, 'count': n}}


def _check_metric(rule) -> dict:
    code = rule.get('metric_code') or ''
    if not code:
        return {'value': None, 'measured': {}}
    ptype = rule.get('period_type') or 'yearToDate'
    per = _one('SELECT MAX(period_key) AS k FROM t_power_ai_metric_snapshot '
               'WHERE deleted_flag=0 AND period_type=%s', [ptype])
    key = str(per.get('k') or '')
    if not key:
        return {'value': None, 'measured': {}}
    st = rule.get('scope_type') or 'station'
    sk = str(rule.get('scope_key') or '')
    if st == 'station':
        station = _station(sk)
        rows = _q('SELECT scope_name, metric_value FROM t_power_ai_metric_snapshot '
                  'WHERE deleted_flag=0 AND period_type=%s AND period_key=%s AND metric_code=%s '
                  "AND scope_type='station' AND scope_name=%s",
                  [ptype, key, code, station.get('name')], limit=5)
    elif st == 'county':
        d = _dept(sk)
        rows = _q('SELECT scope_name, metric_value FROM t_power_ai_metric_snapshot '
                  'WHERE deleted_flag=0 AND period_type=%s AND period_key=%s AND metric_code=%s '
                  "AND scope_type='district'",
                  [ptype, key, code], limit=50)
        rows = [r for r in rows
                if alerts4_norm(r.get('scope_name')) == alerts4_norm(d.get('name'))]
    else:
        rows = _q('SELECT scope_name, metric_value FROM t_power_ai_metric_snapshot '
                  'WHERE deleted_flag=0 AND period_type=%s AND period_key=%s AND metric_code=%s '
                  "AND scope_type='city'", [ptype, key, code], limit=5)
    if not rows:
        return {'value': None, 'measured': {'period': key}}
    v = _f(rows[0].get('metric_value'))
    return {'value': round(v, 4), 'measured': {'period': key, 'metric': code,
                                              'value': round(v, 4),
                                              'scope_name': rows[0].get('scope_name')}}


def alerts4_norm(name):
    from app.secretary.alerts4 import _norm_company
    return _norm_company(name)


def _check_tree(db: Session, t: DispatchTask) -> dict:
    rows = db.execute(select(DispatchTask.status).where(
        DispatchTask.parent_task_id == t.id, DispatchTask.deleted_flag == 0)).scalars().all()
    open_n = sum(1 for s in rows if s not in ('closed', 'cancelled'))
    return {'value': open_n, 'measured': {'children': len(rows), 'open_children': open_n}}


# ─────────────────────────────────────────────────────────── tick

def tick(db: Session, now: datetime | None = None) -> dict:
    """催办 / 升级 / 自动核销 / 停用转派。可重复执行，不重复发通知。"""
    now = now or _now()
    acts = {'checked': 0, 'urged': 0, 'escalated': 0, 'closed': 0, 'transferred': 0}
    tasks = db.execute(select(DispatchTask).where(
        DispatchTask.status.in_(('open', 'doing', 'verify')),
        DispatchTask.deleted_flag == 0)).scalars().all()
    for t in tasks:
        acts['checked'] += 1
        # 1) 停用账号转派
        try:
            acts['transferred'] += _reassign_disabled(db, t)
        except Exception:  # noqa: BLE001
            logger.exception('转派检查失败 task=%s', t.id)
        # 2) 自动核销
        if t.verifier_type == 'auto' and (t.verify_rule or {}).get('kind'):
            res = check_task(db, t)
            db.add(DispatchCheck(task_id=t.id, check_kind='auto', rule=t.verify_rule or {},
                                 measured=res.get('measured') or {}, passed=res.get('passed'),
                                 message=(res.get('message') or '')[:500]))
            t.last_check_at = now
            t.verify_state = ('passed' if res.get('passed') else
                              ('failed' if res.get('passed') is False else 'unavailable'))
            if res.get('passed') and (t.verify_rule or {}).get('close_when_met', True):
                _auto_close(db, t, res, now)
                acts['closed'] += 1
                db.commit()
                continue
        # 3) 回执催办 / 升级
        if t.reply_due_at and t.status in ('open', 'doing'):
            if now > t.reply_due_at:
                if _urge_daily(db, t, now, overdue=True):
                    acts['urged'] += 1
                grace = DR.LEVEL_GRACE_DAYS.get(t.level, 1)
                if now > t.reply_due_at + timedelta(days=grace):
                    if _escalate_once(db, t, '回执超期（%s）' % _iso(t.reply_due_at)):
                        acts['escalated'] += 1
            elif now > t.reply_due_at - timedelta(hours=DR.URGE_BEFORE_HOURS):
                if _urge_daily(db, t, now, overdue=False):
                    acts['urged'] += 1
        # 4) 目标到期未达标
        if t.due_at and now > t.due_at and t.status in ('open', 'doing', 'verify'):
            if _escalate_once(db, t, '到目标时限未达标（%s）' % _iso(t.due_at)):
                acts['escalated'] += 1
        db.commit()
    return dict(acts, at=now.isoformat())


def _urge_daily(db: Session, t: DispatchTask, now: datetime, overdue: bool) -> bool:
    key = 'task:%d:urge:%s' % (t.id, now.strftime('%Y%m%d'))
    if db.execute(select(DispatchNotice.id).where(DispatchNotice.dedupe_key == key)).first():
        return False
    sent = False
    for a in _assignees(db, t.id):
        if a.part != 'owner':
            continue
        emp = _emp(a.employee_id) or {'login_name': a.login_name, 'name': a.name}
        _notice(db, t, 'urge', emp, '【催办】%s' % t.title,
                ('已超过回执时限：%s' if overdue else '还有 %d 小时到回执时限') %
                (_iso(t.reply_due_at) if overdue else DR.URGE_BEFORE_HOURS),
                dedupe=key + ':' + a.login_name)
        sent = True
    return sent


def _auto_close(db: Session, t: DispatchTask, res: dict, now: datetime):
    t.status = 'closed'
    t.closed_at = now
    t.close_reason = 'auto_verified'
    _event(db, t, 'auto_close', None, 'doing', 'closed',
           res.get('message'), {'measured': res.get('measured') or {}})
    for a in _assignees(db, t.id):
        emp = _emp(a.employee_id) or {'login_name': a.login_name, 'name': a.name}
        _notice(db, t, 'verify', emp, '【已自动闭环】%s' % t.title,
                res.get('message') or '实测已达标', dedupe='task:%d:auto:%s' % (t.id, a.login_name))
    _notify_creator(db, t, 'verify', '【已自动闭环】%s' % t.title, res.get('message') or '实测已达标')


def _reassign_disabled(db: Session, t: DispatchTask) -> int:
    n = 0
    for a in _assignees(db, t.id):
        if a.part != 'owner':
            continue
        emp = _emp(a.employee_id)
        if emp and not emp['disabled']:
            continue
        alt = [e for e in _emp_by_role('sz', a.dept_id) if e['employee_id'] != a.employee_id]
        if not alt:
            continue
        a.part = 'collaborator'
        db.add(DispatchTaskAssignee(task_id=t.id, part='owner', employee_id=alt[0]['employee_id'],
                                    login_name=alt[0]['login_name'], name=alt[0]['name'],
                                    dept_id=alt[0]['dept_id'], state='pending'))
        _event(db, t, 'transfer', None, t.status, t.status,
               '原承办人已停用，系统转派给 %s' % alt[0]['name'])
        _notice(db, t, 'assign', alt[0], '【转派任务】%s' % t.title, '原承办人已停用',
                dedupe='task:%d:reassign:%s' % (t.id, alt[0]['login_name']))
        n += 1
    return n


# ─────────────────────────────────────────────────────────── 查询

def _get_task(db: Session, task_id: int) -> DispatchTask:
    t = db.get(DispatchTask, int(task_id))
    if t is None or t.deleted_flag:
        raise DispatchError('TASK_NOT_FOUND', '任务不存在', 404)
    return t


def serialize_task(t: DispatchTask) -> dict:
    return {
        'id': t.id, 'task_no': t.task_no, 'task_type': t.task_type, 'title': t.title,
        'requirement': t.requirement, 'status': t.status, 'level': t.level,
        'alert_id': t.alert_id, 'root_task_id': t.root_task_id, 'parent_task_id': t.parent_task_id,
        'scope_type': t.scope_type, 'scope_key': t.scope_key, 'scope_name': t.scope_name,
        'biz_type': t.biz_type, 'biz_keys': t.biz_keys or [],
        'creator': {'employee_id': t.creator_id, 'name': t.creator_name,
                    'login_name': t.creator_login},
        'reply_due_at': _iso(t.reply_due_at), 'due_at': _iso(t.due_at),
        'require_plan': int(t.require_plan or 0),
        'verifier_type': t.verifier_type, 'verify_rule': t.verify_rule or {},
        'verify_state': t.verify_state, 'last_check_at': _iso(t.last_check_at),
        'escalate_level': t.escalate_level, 'overdue_flag': t.overdue_flag,
        'archived_flag': t.archived_flag,
        'closed_at': _iso(t.closed_at), 'close_reason': t.close_reason,
        'create_time': _iso(t.create_time), 'update_time': _iso(t.update_time),
    }


def list_tasks(db: Session, account: str, role: str = 'mine', status: str | None = None,
               category: str | None = None, limit: int = 50, offset: int = 0) -> dict:
    act = _actor(account)
    emp_id = int(act['employee_id'])
    q = select(DispatchTask).where(DispatchTask.deleted_flag == 0)
    if role == 'created':
        q = q.where(DispatchTask.creator_id == emp_id)
    elif role == 'all':
        if act.get('role_code') != 'admin':
            raise DispatchError('NO_PERMISSION', '只有管理员能看全部任务', 403)
    else:
        sub = select(DispatchTaskAssignee.task_id).where(
            DispatchTaskAssignee.employee_id == emp_id)
        if role == 'mine':
            q = q.where(or_(DispatchTask.creator_id == emp_id,
                            DispatchTask.id.in_(sub)))
        elif role == 'cc':
            q = q.where(DispatchTask.id.in_(
                select(DispatchTaskAssignee.task_id).where(
                    DispatchTaskAssignee.employee_id == emp_id,
                    DispatchTaskAssignee.part == 'cc')))
    if status:
        q = q.where(DispatchTask.status == status)
    if category:
        q = q.where(DispatchTask.id.in_(
            select(AlertItem.task_id).where(AlertItem.alert_type == category)))
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    rows = db.execute(q.order_by(DispatchTask.update_time.desc())
                      .limit(max(1, min(200, int(limit)))).offset(max(0, int(offset)))).scalars().all()
    return {'role': role, 'total': total,
            'items': [serialize_task(t) for t in rows]}


def task_detail(db: Session, account: str, task_id: int) -> dict:
    act = _actor(account)
    t = _get_task(db, task_id)
    if _access(db, t, act) is None:
        raise DispatchError('NOT_PARTICIPANT', '无权查看该任务', 403)
    out = serialize_task(t)
    out['my_parts'] = _my_parts(db, t.id, int(act['employee_id']))
    out['assignees'] = [{'employee_id': a.employee_id, 'login_name': a.login_name,
                         'name': a.name, 'dept_name': a.dept_name, 'part': a.part,
                         'state': a.state, 'accepted_at': _iso(a.accepted_at),
                         'replied_at': _iso(a.replied_at)} for a in _assignees(db, t.id)]
    out['events'] = [{'id': e.id, 'action': e.action, 'actor_name': e.actor_name,
                      'from_status': e.from_status, 'to_status': e.to_status,
                      'content': e.content, 'payload': e.payload,
                      'create_time': _iso(e.create_time)}
                     for e in db.execute(select(DispatchTaskEvent).where(
                         DispatchTaskEvent.task_id == t.id)
                         .order_by(DispatchTaskEvent.id)).scalars().all()]
    out['plans'] = [{'id': p.id, 'kind': p.kind, 'version': p.version, 'title': p.title,
                     'content': p.content, 'items': p.items, 'source': p.source,
                     'author_name': p.author_name, 'accepted': p.accepted,
                     'create_time': _iso(p.create_time)}
                    for p in db.execute(select(DispatchTaskPlan).where(
                        DispatchTaskPlan.task_id == t.id)
                        .order_by(DispatchTaskPlan.id)).scalars().all()]
    out['checks'] = [{'id': c.id, 'passed': c.passed, 'measured': c.measured,
                      'message': c.message, 'check_time': _iso(c.check_time)}
                     for c in db.execute(select(DispatchCheck).where(
                         DispatchCheck.task_id == t.id)
                         .order_by(DispatchCheck.id.desc()).limit(10)).scalars().all()]
    out['children'] = [serialize_task(x) for x in db.execute(
        select(DispatchTask).where(DispatchTask.parent_task_id == t.id,
                                   DispatchTask.deleted_flag == 0)).scalars().all()]
    if t.alert_id:
        a = db.get(AlertItem, int(t.alert_id))
        if a is not None:
            out['alert'] = {'id': a.id, 'title': a.title, 'category': a.alert_type,
                            'subtype': a.subtype, 'level': a.level, 'status': a.status}
    return out


def preview(db: Session, account: str, alert_id: int) -> dict:
    """下发确认页数据：推荐承办人 + 默认时限 + AI 拟办草稿（**不落库**）。"""
    act = _actor(account)
    alert = db.get(AlertItem, int(alert_id))
    if alert is None:
        raise DispatchError('ALERT_NOT_FOUND', '预警不存在', 404)
    sc = act['scope']
    if alert.assignee_role and alert.assignee_role != sc.get('role_code'):
        raise DispatchError('NOT_PARTICIPANT', '这条预警不是派给当前角色的', 403)
    rec = recommend(db, alert)
    from app.services import dispatch_ai
    card = {'title': alert.title, 'level': alert.level, 'subtype': alert.subtype,
            'data': alert.data or {}, 'verify_hint': alert.verify_hint or {},
            'scope_name': alert.scope_name, 'alert_type': alert.alert_type}
    draft = dispatch_ai.propose(card, option=1)
    brief = dispatch_ai.brief(card)
    return {'alert': {'id': alert.id, 'title': alert.title, 'level': alert.level,
                      'subtype': alert.subtype, 'scope_name': alert.scope_name,
                      'data': alert.data or {}, 'status': alert.status},
            'recommend': rec,
            'options': [{'option': i + 1, **a} for i, a in enumerate(brief['advice'])],
            'draft': draft,
            'defaults': {'reply_due_hours': draft['reply_due_hours'],
                         'target_days': draft['target_days'],
                         'require_plan': draft['require_plan'],
                         'verifier_type': draft['verifier_type'],
                         'verify_rule': draft['verify_rule']}}


def stats(db: Session) -> dict:
    rows = db.execute(select(DispatchTask.alert_id, DispatchTask.status,
                             DispatchTask.overdue_flag, DispatchTask.escalate_level,
                             DispatchTask.create_time, DispatchTask.closed_at)).all()
    alerts = {a.id: a.alert_type for a in db.execute(select(AlertItem)).scalars().all()}
    out = {'total': len(rows), 'by_status': {}, 'by_category': {},
           'overdue': 0, 'escalated': 0, 'closed': 0}
    closed_hours = []
    for alert_id, status, overdue, esc, created, closed in rows:
        out['by_status'][status] = out['by_status'].get(status, 0) + 1
        cat = alerts.get(alert_id) or 'manual'
        out['by_category'][cat] = out['by_category'].get(cat, 0) + 1
        out['overdue'] += 1 if overdue else 0
        out['escalated'] += 1 if int(esc or 0) > 0 else 0
        if status == 'closed':
            out['closed'] += 1
            if created and closed:
                closed_hours.append((closed - created).total_seconds() / 3600.0)
    out['closed_rate'] = round(out['closed'] / len(rows), 4) if rows else 0.0
    out['avg_close_hours'] = round(sum(closed_hours) / len(closed_hours), 1) if closed_hours else None
    alerts_total = db.execute(select(func.count()).select_from(AlertItem)).scalar_one()
    out['alerts'] = {'total': alerts_total,
                     'by_category': dict(db.execute(
                         select(AlertItem.alert_type, func.count())
                         .group_by(AlertItem.alert_type)).all()),
                     'open': db.execute(select(func.count()).select_from(AlertItem)
                                        .where(AlertItem.status == 'open')).scalar_one()}
    return out


# ─────────────────────────────────────────────────────────── 附件 / 报告 / 追问

# 附件/报告落盘根目录：默认 <server>/data/dispatch（与 cwd 无关，测试与服务一致）
FILES_ROOT = Path(os.environ.get('DISPATCH_FILES_DIR')
                  or (Path(__file__).resolve().parents[2] / 'data' / 'dispatch'))
MAX_ATTACH_MB = 20


def _safe_name(name) -> str:
    base = os.path.basename(str(name or 'file'))
    for ch in '\\/:*?"<>|\r\n\t':
        base = base.replace(ch, '_')
    return (base or 'file')[:120]


def _task_dir(task_id) -> Path:
    d = FILES_ROOT / ('t%d' % int(task_id))
    d.mkdir(parents=True, exist_ok=True)
    return d


def serialize_attachment(a: DispatchAttachment) -> dict:
    return {'id': a.id, 'task_id': a.task_id, 'file_name': a.file_name,
            'size_bytes': a.size_bytes, 'md5': a.md5,
            'uploader_name': a.uploader_name,
            'create_time': _iso(a.create_time),
            'download_url': '/api/tasks/%d/attachments/%d/download' % (a.task_id, a.id)}


def add_attachment(db: Session, account: str, task_id: int, file_name: str, content: bytes) -> dict:
    """上传附件（回执材料/报告/复盘）：落盘 + 记 `dispatch_attachment`。"""
    act = _actor(account)
    t = _get_task(db, task_id)
    _need(db, t, act, 'owner', 'collaborator', 'creator', 'superior', 'admin')
    if len(content) > MAX_ATTACH_MB * 1024 * 1024:
        raise DispatchError('FILE_TOO_LARGE', '附件超过 %d MB' % MAX_ATTACH_MB, 422)
    if not content:
        raise DispatchError('EMPTY_FILE', '空文件', 422)
    name = _safe_name(file_name)
    md5 = hashlib.md5(content).hexdigest()
    path = _task_dir(t.id) / ('%s_%s' % (md5[:8], name))
    path.write_bytes(content)
    att = DispatchAttachment(task_id=t.id, file_name=name, storage_path=str(path),
                             size_bytes=len(content), md5=md5,
                             uploader_id=act['employee_id'], uploader_name=act['name'])
    db.add(att)
    db.flush()
    _event(db, t, 'comment', act, t.status, t.status, '上传附件：%s' % name,
           {'attachment_id': att.id})
    db.commit()
    return serialize_attachment(att)


def list_attachments(db: Session, account: str, task_id: int) -> dict:
    act = _actor(account)
    t = _get_task(db, task_id)
    if _access(db, t, act) is None:
        raise DispatchError('NOT_PARTICIPANT', '无权查看该任务', 403)
    rows = db.execute(select(DispatchAttachment).where(
        DispatchAttachment.task_id == t.id).order_by(DispatchAttachment.id)).scalars().all()
    return {'total': len(rows), 'items': [serialize_attachment(a) for a in rows]}


def attachment_file(db: Session, account: str, task_id: int, att_id: int) -> tuple[Path, str]:
    act = _actor(account)
    t = _get_task(db, task_id)
    if _access(db, t, act) is None:
        raise DispatchError('NOT_PARTICIPANT', '无权下载', 403)
    a = db.get(DispatchAttachment, int(att_id))
    if a is None or a.task_id != t.id:
        raise DispatchError('ATTACHMENT_NOT_FOUND', '附件不存在', 404)
    p = Path(a.storage_path)
    if not p.is_file():
        raise DispatchError('ATTACHMENT_MISSING', '附件文件已丢失', 404)
    return p, a.file_name


def report_docx(db: Session, account: str, task_id: int) -> dict:
    """生成上报报告 docx（复用 docgen 的公文样式），落盘并登记附件。"""
    from app.secretary import docgen
    from app.services import dispatch_ai

    d = task_detail(db, account, task_id)
    alert = None
    if d.get('alert_id'):
        a = db.get(AlertItem, int(d['alert_id']))
        if a is not None:
            alert = {'id': a.id, 'title': a.title, 'level': a.level, 'subtype': a.subtype,
                     'scope_name': a.scope_name, 'data': a.data or {}}
    spec = dispatch_ai.report_sections(d, alert)
    name = '处置情况报告_%s.docx' % (d.get('task_no') or task_id)
    path = _task_dir(task_id) / name
    docgen.write_docx(path, spec['title'], spec['sections'], spec['tables'])
    data = path.read_bytes()
    md5 = hashlib.md5(data).hexdigest()
    att = DispatchAttachment(task_id=int(task_id), file_name=name, storage_path=str(path),
                             size_bytes=len(data), md5=md5,
                             uploader_id=None, uploader_name='系统生成')
    db.add(att)
    db.flush()
    _event(db, _get_task(db, task_id), 'comment', None, None, None,
           '生成处置报告 docx', {'attachment_id': att.id})
    db.commit()
    return {'attachment': serialize_attachment(att),
            'markdown': dispatch_ai.report_markdown(d, alert)['markdown']}


def ask(db: Session, account: str, task_id: int, question: str, today: str | None = None,
        history_turns: int = 5) -> dict:
    """任务内追问：把任务上下文塞进问题 → 复用 agent.ask → 消息落到任务会话。"""
    act = _actor(account)
    t = _get_task(db, task_id)
    who = _access(db, t, act)
    if who is None:
        raise DispatchError('NOT_PARTICIPANT', '无权在该任务里提问', 403)
    q = (question or '').strip()
    if not q:
        raise DispatchError('EMPTY_QUESTION', '问题不能为空', 422)

    sid = None
    field = None
    if t.creator_id == act['employee_id']:
        sid, field = t.creator_session_id, 'creator_session_id'
    else:
        for a in _assignees(db, t.id):
            if a.employee_id == act['employee_id']:
                sid, field = a.session_id, 'assignee'
                break
    new_session = not sid
    if not sid:
        sid = 'task-%d-%s' % (t.id, uuid.uuid4().hex[:8])

    prompt = (
        '【任务上下文】编号 %s｜%s｜责任单位 %s｜状态 %s｜回执时限 %s｜完成时限 %s\n'
        '任务要求：%s\n'
        '我的问题：%s'
        % (t.task_no, t.title, t.scope_name or '', t.status,
           _iso(t.reply_due_at) or '—', _iso(t.due_at) or '—',
           (t.requirement or '')[:600], q)
    )
    from app.secretary import agent as agent_mod
    try:
        r = agent_mod.ask(prompt, scope=t.scope_name or None, session_id=sid,
                          user_code=act['login_name'], today=today,
                          history_turns=max(0, int(history_turns)))
    except Exception as exc:  # noqa: BLE001 - 模型不可用不影响任务
        logger.warning('任务追问失败 task=%s：%s', t.id, exc)
        r = {'answer': '（模型暂不可用）%s: %s' % (type(exc).__name__, str(exc)[:200]),
             'session_id': sid, 'trace': [], 'elapsed_ms': 0}

    if field and field != 'assignee':
        t.creator_session_id = sid
    elif field == 'assignee':
        for a in _assignees(db, t.id):
            if a.employee_id == act['employee_id']:
                a.session_id = sid
    db.commit()
    if new_session:
        try:
            db.execute(text("UPDATE t_chat_session SET title=:ti WHERE session_id=:s"),
                       {'ti': ('【任务】%s %s' % (t.task_no, t.title))[:255], 's': sid})
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
    return {'task_id': t.id, 'session_id': r.get('session_id') or sid,
            'question': q, 'answer': r.get('answer') or '',
            'elapsed_ms': r.get('elapsed_ms'), 'model': r.get('model'),
            'trace_steps': len(r.get('trace') or [])}


# ─────────────────────────────────────────────────────────── 通知

def list_notices(db: Session, account: str, unread: bool = False, limit: int = 50) -> dict:
    act = _actor(account)
    q = select(DispatchNotice).where(
        or_(DispatchNotice.receiver_login == act['login_name'],
            DispatchNotice.receiver_id == act['employee_id']))
    if unread:
        q = q.where(DispatchNotice.read_flag == 0)
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    rows = db.execute(q.order_by(DispatchNotice.sent_at.desc())
                      .limit(max(1, min(200, int(limit))))).scalars().all()
    return {'total': total, 'unread': db.execute(
        select(func.count()).select_from(DispatchNotice).where(
            DispatchNotice.receiver_login == act['login_name'],
            DispatchNotice.read_flag == 0)).scalar_one(),
        'items': [{'id': n.id, 'task_id': n.task_id, 'notice_type': n.notice_type,
                   'title': n.title, 'content': n.content, 'read': int(n.read_flag),
                   'sent_at': _iso(n.sent_at)} for n in rows]}


def read_notice(db: Session, account: str, notice_id: int) -> dict:
    act = _actor(account)
    n = db.get(DispatchNotice, int(notice_id))
    if n is None or n.receiver_login != act['login_name']:
        raise DispatchError('NOTICE_NOT_FOUND', '通知不存在', 404)
    n.read_flag = 1
    n.read_at = _now()
    db.commit()
    return {'id': n.id, 'read': 1, 'read_at': _iso(n.read_at)}


__all__ = ['DispatchError', 'recommend', 'preview', 'create', 'accept', 'decline',
           'submit_plan', 'accept_plan', 'reply', 'close', 'reopen', 'transfer',
           'subtask', 'urge', 'supervise', 'escalate', 'cancel', 'archive',
           'tick', 'check_task', 'list_tasks', 'task_detail', 'stats',
           'list_notices', 'read_notice',
           'add_attachment', 'list_attachments', 'attachment_file', 'report_docx', 'ask']
