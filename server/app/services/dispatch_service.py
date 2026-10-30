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
import re
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
from app.services import dispatch_chain as CH

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


def _cn_dt(v) -> str:
    """给人看的时间：10月30日 18:00。接口里的 ISO 值不动，这是给人读的那一份。"""
    if not v:
        return '—'
    try:
        d = v if isinstance(v, datetime) else datetime.fromisoformat(
            str(v).replace('Z', '+00:00'))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        d = d.astimezone(timezone(timedelta(hours=8)))
    except (ValueError, TypeError):
        return str(v)[:16]
    return '%d月%d日 %02d:%02d' % (d.month, d.day, d.hour, d.minute)


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


def _emps_up(role_code: str, dept_id) -> list[dict]:
    """按组织树往上找这个角色的人。

    为什么必须上溯：所长在 dept 56，县财务在 dept 10，`_emp_by_role('xgscwb', 56)`
    永远是空的 —— 原实现找不到人就直接退化成"通知发起人自己"，
    等于**所长上报给自己**，层级链其实是断的（2026-10-21 发现）。
    """
    for d in CH.ancestor_chain(dept_id, _dept_parent):
        emps = _emp_by_role(role_code, d)
        if emps:
            return emps
    return []


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
    reason = {'station': '本所预算/项目由所长承办',
              'county': '县公司口径由县财务承办'}.get(lv, '全市口径由市财务承办')
    return DR.LEVEL_ROLE.get(lv, DR.LEVEL_ROLE['city']), reason


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


def _next_case_id(db: Session) -> str:
    """对外事项号 C<YYYYMMDD>-<5位>（人看着能念，机器看着唯一）。"""
    day = time.strftime('%Y%m%d')
    n = db.execute(select(func.count()).select_from(DispatchTask).where(
        DispatchTask.case_id.like('C%s-%%' % day))).scalar_one()
    seq = int(n) + 1
    while True:
        cid = 'C%s-%05d' % (day, seq)
        if not db.execute(select(DispatchTask.id).where(
                DispatchTask.case_id == cid).limit(1)).first():
            return cid
        seq += 1


def case_key_of_alert(alert: AlertItem) -> str:
    """一件事的指纹：和 `alert_service.case_key_of` 同一口径（= dedupe_key 去掉角色段）。"""
    return '%s|%s|%s|%s|%s' % (alert.alert_type, alert.subtype, alert.scope_type,
                                alert.scope_key, alert.object_key or '*')


def case_root(db: Session, case_key: str) -> DispatchTask | None:
    """这个事项上还活着的那棵树（根任务）。闭环/作废的不算，可以再建新的。"""
    return db.execute(select(DispatchTask).where(
        DispatchTask.case_key == case_key,
        DispatchTask.parent_task_id.is_(None),
        DispatchTask.deleted_flag == 0,
        DispatchTask.status.notin_(('cancelled', 'closed')))
        .order_by(DispatchTask.id.desc()).limit(1)).scalar_one_or_none()


def _case_exists_error(act: dict, root: DispatchTask) -> DispatchError:
    """同一件事已经有任务了 —— 明确告诉他能做什么，不许他再建一棵。"""
    who = root.creator_name or root.creator_login
    return DispatchError(
        'CASE_EXISTS',
        '这件事已经有任务在办了（%s，%s 发起，当前 %s）。同一件事只能有一张单，'
        '你可以：① 补充要求 ② 修改方案 ③ 写说明。不能重复建任务。'
        % (root.task_no, who, root.status),
        409,
        {'existing_task_id': root.id, 'existing_task_no': root.task_no,
         'case_id': root.case_id, 'case_key': root.case_key,
         'creator': who, 'status': root.status,
         'actions': ['comment', 'supplement', 'plan'],
         'endpoints': {'comment': '/api/tasks/%d/comment' % root.id,
                       'supplement': '/api/tasks/%d/supplement' % root.id,
                       'plan': '/api/tasks/%d/plan' % root.id,
                       'case': '/api/tasks/case/%s' % (root.case_id or '')}})


def _dept_parent(dept_id) -> int | None:
    d = _dept(dept_id) if dept_id else {}
    return _i(d.get('parent_id')) or None


def _guard_chain(db: Session, act: dict, action: str, emp: dict,
                 task: DispatchTask | None = None) -> None:
    """层级守卫：越级/同级/跨组织一律 409，并写清"该发给谁"。

    action: assign（派活）/ subtask（继续拆）/ transfer（转派）/ escalate（上报）
    """
    actor_role = act.get('role_code')
    target_role = _role_of(emp['employee_id']) or None
    ok, rel = CH.check(action, actor_role, target_role)
    if not ok:
        raise DispatchError('LEVEL_SKIP', CH.explain(action, actor_role, target_role, rel),
                            409, {'reason': rel, 'actor_role': actor_role,
                                  'target_role': target_role,
                                  'allowed': sorted(CH.ACTION_ALLOW.get(action) or []),
                                  'next_up': CH.next_ranks(actor_role, 1),
                                  'next_down': CH.next_ranks(actor_role, -1)})
    upward = rel == 'up' or (action == 'escalate')
    if rel not in ('same', 'peer') and not CH.check_org(
            act.get('dept_id'), emp.get('dept_id'), _dept_parent, upward=upward):
        raise DispatchError(
            'OUT_OF_CHAIN',
            '层级对了但不在你的组织里：%s 的上级不是你这条线，只能在本单位往下传/往上报。'
            % (emp.get('name') or emp.get('login_name') or ''),
            409, {'actor_dept': act.get('dept_id'), 'target_dept': emp.get('dept_id')})
    if action == 'escalate' and target_role == 'master' and DR.MASTER_ESCALATION_BLOCK:
        raise DispatchError('MASTER_GUARD',
                            '市领导只接大方向结论，执行层的事不要直接上报到市领导。', 409)


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

    # 一件事只能一棵树：别人已经建了，就让他在那张单上补充/说明，不许再建
    ckey = case_key_of_alert(alert)
    root = case_root(db, ckey)
    if root is not None:
        raise _case_exists_error(act, root)

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
        case_id=_next_case_id(db), case_key=ckey,
        alert_id=alert.id, scope_type=alert.scope_type, scope_key=alert.scope_key,
        scope_name=alert.scope_name, biz_type=alert.object_type,
        biz_keys=[alert.object_key] if alert.object_key and alert.object_key != '*' else [],
        creator_id=int(act['employee_id']), creator_name=act['name'],
        creator_login=act['login_name'], creator_dept_id=act.get('dept_id'),
        require_plan=int(body.get('require_plan') or 0),
        require_reply=int(body.get('require_reply', 1) or 0),
        verifier_type=body.get('verifier_type') or 'auto',
        verify_rule=body.get('verify_rule') or alert.verify_hint or {},
        reply_due_at=(None if int(body.get('require_reply', 1) or 0) == 0 else
                      (_parse_dt(body.get('reply_due_at')) or
                       (now + timedelta(hours=DR.reply_hours(alert.subtype, alert.level))))),
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
        if part == 'owner':                    # 主责才叫派活
            _guard_chain(db, act, 'assign', emp, task)
        elif part == 'collaborator':           # 协办是请人配合，可相邻上下
            _guard_chain(db, act, 'collab', emp, task)
        # cc 只是知会，不限层级
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
    # 通知类型必须和"回执"分开：同一个 dedupe_key（分钟级）会把后面的回执通知挤掉
    _notify_creator(db, t, 'accept', '【已接单】%s' % t.title, '%s 已接单' % act['name'])
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
    _notify_creator(db, t, 'reply', '【回执待核销】%s' % t.title,
                    '%s 提交了回执：%s' % (act['name'], (content or '')[:80]))
    db.commit()
    return task_detail(db, account, t.id)


def close(db: Session, account: str, task_id: int, conclusion: str = '', reason: str = 'human_confirmed') -> dict:
    act = _actor(account)
    t = _get_task(db, task_id)
    _need(db, t, act, 'creator', 'superior', 'admin')
    if t.status != 'verify':
        raise DispatchError('INVALID_TRANSITION', '只有待核销任务可以核销（当前 %s）' % t.status, 409)
    forced = act.get('role_code') == DR.ADMIN_ROLE
    kids = _children_closed(db, t)
    if kids['has_children'] and not kids['closed'] and not forced:
        raise DispatchError(
            'CHILDREN_OPEN',
            '还有 %d 条子任务没闭环（%s）。下级都办完才能闭环；管理员可以强制闭环并留痕。'
            % (len(kids['waiting']), '、'.join(kids['waiting'][:5])),
            409, {'waiting': kids['waiting']})
    res = check_task(db, t)
    # 数据这一关：上级一定看（两个条件缺一不可）；末级只有"按数据核销"的任务才卡，
    # 由人判定（verifier_type=human）的末级任务不拿规则挡人。
    if ((kids['has_children'] or t.verifier_type == 'auto')
            and res.get('passed') is False and not forced):
        raise DispatchError(
            'DATA_NOT_MET',
            '本级数据还没达标（%s）。等数据达标再闭环，或由管理员强制闭环并留痕。'
            % (res.get('message') or ''),
            409, {'measured': res.get('measured'), 'children_closed': kids['closed']})
    fs = _move(t, 'closed')
    t.closed_at = _now()
    t.close_reason = 'admin_forced' if forced else reason
    _event(db, t, 'close', act, fs, 'closed', conclusion,
           {'reason': t.close_reason, 'forced': forced,
            'children_closed': kids['closed'], 'waiting': kids['waiting'],
            'data_ok': (res.get('measured') or {}).get('data_ok', res.get('passed')),
            'measured': res.get('measured') or {}})
    _notify_all(db, t, 'close', '【已闭环】%s' % t.title,
                (conclusion or '任务已核销') + ('（管理员强制闭环）' if forced else ''))
    db.commit()
    return task_detail(db, account, t.id)


def reopen(db: Session, account: str, task_id: int) -> dict:
    act = _actor(account)
    t = _get_task(db, task_id)
    _need(db, t, act, 'creator', 'admin')
    if t.status != 'closed':
        raise DispatchError('INVALID_TRANSITION', '只有已闭环任务可以重开', 409)
    if t.closed_at and (_now() - t.closed_at).days > DR.REOPEN_DAYS:
        raise DispatchError('REOPEN_EXPIRED',
                            '闭环超过 %d 天不能重开' % DR.REOPEN_DAYS, 409)
    fs = _move(t, 'doing')
    t.closed_at = None
    t.close_reason = None
    _event(db, t, 'reopen', act, fs, 'doing')
    _notify_all(db, t, 'reopen', '【任务重开】%s' % t.title, '发起人重新打开了任务')
    db.commit()
    return task_detail(db, account, t.id)


def adjust(db: Session, account: str, task_id: int, body: dict) -> dict:
    """发起人改时限/要求（Fred 2026-10-28：改权限归发起人）。

    子任务改期限必须夹在父任务窗口内 —— 同 `fanout` 那条口径，不许改出"父已逾期、子还早"。
    """
    act = _actor(account)
    t = _get_task(db, task_id)
    _need(db, t, act, 'creator', 'superior', 'admin')
    if t.status in ('closed', 'cancelled'):
        raise DispatchError('INVALID_TRANSITION', '任务已%s，不能再改' % t.status, 409)
    parent = db.get(DispatchTask, int(t.parent_task_id)) if t.parent_task_id else None
    before = {'due_at': _iso(t.due_at), 'reply_due_at': _iso(t.reply_due_at),
              'requirement': t.requirement, 'require_reply': t.require_reply}
    changed = []
    if body.get('requirement') is not None and body['requirement'] != t.requirement:
        t.requirement = str(body['requirement'])[:2000]
        changed.append('要求')
    due = _parse_dt(body.get('due_at'))
    if due is not None:
        t.due_at = _min_dt(due, parent.due_at) if parent is not None else due
        changed.append('完成时限')
    reply_due = _parse_dt(body.get('reply_due_at'))
    if reply_due is not None:
        t.reply_due_at = (_min_dt(reply_due, parent.reply_due_at)
                          if parent is not None else reply_due)
        t.require_reply = 1          # 定了回执时限就是要回执
        changed.append('回执时限')
    if not changed:
        raise DispatchError('NO_CHANGE',
                            '没带要改的东西（due_at / reply_due_at / requirement）', 422)
    after = {'due_at': _iso(t.due_at), 'reply_due_at': _iso(t.reply_due_at),
             'requirement': t.requirement, 'require_reply': t.require_reply}
    _event(db, t, 'adjust', act, t.status, t.status, '改了%s' % '、'.join(changed),
           {'before': before, 'after': after})
    for a in _assignees(db, t.id):
        if a.part != 'owner':
            continue
        emp = _emp(a.employee_id) or {'login_name': a.login_name, 'name': a.name}
        _notice(db, t, 'adjust', emp, '【时限/要求有调整】%s' % t.title,
                '完成时限：%s\n回执时限：%s\n要求：%s'
                % (_cn_dt(t.due_at), _cn_dt(t.reply_due_at), t.requirement or '—'),
                dedupe='task:%d:adjust:%s:%s' % (t.id, a.login_name,
                                                 time.strftime('%Y%m%d%H%M%S')))
    db.commit()
    return task_detail(db, account, t.id)


def request_extend(db: Session, account: str, task_id: int, body: dict) -> dict:
    """承办人交不出时申请延期：只留记录 + 通知发起人，**不改时限**（改由发起人批）。

    为什么不当场改：时限是发起人定的窗口，承办人自己往后退等于自己给自己批假。
    """
    act = _actor(account)
    t = _get_task(db, task_id)
    _need(db, t, act, 'owner')
    if t.status not in ('open', 'doing', 'verify'):
        raise DispatchError('INVALID_TRANSITION',
                            '当前状态不能申请延期（%s）' % t.status, 409)
    want = _parse_dt(body.get('due_at'))
    reason = (body.get('reason') or '').strip()
    if want is None and not reason:
        raise DispatchError('BAD_EXTEND', '延期要说清楚延到什么时候，或者说明原因', 422)
    _event(db, t, 'extend_request', act, t.status, t.status,
           '申请延期到 %s：%s' % (_cn_dt(want) if want else '（没给日期）', reason),
           {'want_due_at': _iso(want), 'reason': reason,
            'current_due_at': _iso(t.due_at)})
    _notify_creator(db, t, 'extend', '【申请延期】%s' % t.title,
                    '%s 申请延期到 %s：%s'
                    % (act['name'], _cn_dt(want) if want else '（没给日期）',
                       reason or '未说明原因'))
    db.commit()
    return task_detail(db, account, t.id)


def transfer(db: Session, account: str, task_id: int, to_employee_id: int) -> dict:
    act = _actor(account)
    t = _get_task(db, task_id)
    _need(db, t, act, 'owner', 'creator', 'superior', 'admin')
    emp = _emp(to_employee_id)
    if not emp or emp['disabled']:
        raise DispatchError('BAD_ASSIGNEE', '承接人不存在或已停用', 422)
    _guard_chain(db, act, 'transfer', emp, t)
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
    # 子任务不改事项：整棵树共用一个 case_id / case_key，谁看都是同一件事
    child_case_key = parent.case_key
    if not child_case_key:
        pa = db.get(AlertItem, parent.alert_id) if parent.alert_id else None
        child_case_key = (case_key_of_alert(pa) if pa
                          else 'task|%d' % (parent.root_task_id or parent.id))
    child_case_id = parent.case_id or _next_case_id(db)
    child = DispatchTask(
        task_no=_next_task_no(db), task_type='alert', title=(body.get('title') or parent.title)[:200],
        requirement=body.get('requirement') or parent.requirement, status='open',
        level=body.get('level') or parent.level, parent_task_id=parent.id,
        root_task_id=parent.root_task_id or parent.id,
        case_id=child_case_id, case_key=child_case_key,
        alert_id=parent.alert_id, scope_type=parent.scope_type, scope_key=parent.scope_key,
        scope_name=parent.scope_name, biz_type=parent.biz_type, biz_keys=parent.biz_keys,
        creator_id=int(act['employee_id']), creator_name=act['name'],
        creator_login=act['login_name'], creator_dept_id=act.get('dept_id'),
        require_plan=int(body.get('require_plan') or 0),
        verifier_type=body.get('verifier_type') or 'human',
        verify_rule=body.get('verify_rule') or {},
        reply_due_at=_parse_dt(body.get('reply_due_at')) or now + timedelta(
            hours=DR.LEVEL_REPLY_HOURS.get(parent.level, 72)),
        due_at=_parse_dt(body.get('due_at')) or now + timedelta(
            days=DR.LEVEL_TARGET_DAYS.get(parent.level, 30)),
    )
    db.add(child)
    db.flush()
    for a in assignees_in:
        emp = _emp(a.get('employee_id'))
        if not emp or emp['disabled']:
            raise DispatchError('BAD_ASSIGNEE', '承办人不存在或已停用', 422)
        part = a.get('part') or 'owner'
        if part == 'owner':
            _guard_chain(db, act, 'subtask', emp, child)
        elif part == 'collaborator':
            _guard_chain(db, act, 'collab', emp, child)
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


# ─────────────────────────────────────────────────────────── 批量裂变

def _down_roles(category: str, role_code: str | None) -> list[str]:
    """职责线再往下一级是谁：把 `DR.DUTY_CHAIN` 反过来用（它写的是"往上报给谁"）。

    例：budget 线 sgscwb → xgscwb → sz，所以市财务裂变的目标角色就是县财务。
    这里不再写一份角色表 —— 口径只活在 dispatch_rules。
    """
    up = DR.DUTY_CHAIN.get(category) or {}
    return [r for r, ups in up.items() if role_code in (ups or [])]


def _child_scopes(parent: DispatchTask) -> list[dict]:
    """下一级单位（只拆一层）：市级 → 县公司，县级 → 各供电所，所级 → 没有下级。"""
    if parent.scope_type == 'county':
        dept = _i(parent.scope_key)
        if not dept:
            return []
        ids = alert_scope._dept_tree_ids(dept)
        marks = ','.join(['%s'] * len(ids))
        rows = _q('SELECT id, name, department_id FROM t_power_company '
                  'WHERE deleted_flag=0 AND department_id IN (' + marks + ') ORDER BY id',
                  ids, limit=200)
        return [{'scope_type': 'station', 'scope_key': str(_i(r['id'])),
                 'scope_name': r.get('name') or '', 'dept_id': _i(r.get('department_id'))}
                for r in rows]
    if parent.scope_type == 'city':
        rows = _q('SELECT department_id, department_name FROM t_department '
                  'WHERE parent_id=%s ORDER BY department_id',
                  [alert_scope._cfg()['city_dept_id']], limit=50)
        return [{'scope_type': 'county', 'scope_key': str(_i(r['department_id'])),
                 'scope_name': r.get('department_name') or '',
                 'dept_id': _i(r.get('department_id'))} for r in rows]
    return []


def _emp_scope_of(sc: dict | None) -> dict | None:
    from app.services import alert_service
    if not sc or not sc.get('ok'):
        return None
    return {'scope_type': sc.get('scope_type'),
            'scope_key': alert_service._scope_key(sc),
            'scope_name': sc.get('scope') or ''}


def _emps_at(roles: list[str], dept_ids: list[int]) -> list[dict]:
    """一次把这几个岗位在这几个部门的人查出来。

    **别按单位一个个查**：县里 17 个所就是 17 次业务库往返，下发按钮会转半分钟
    （交互接口要快是硬口径，见交接红线 8）。
    """
    if not roles or not dept_ids:
        return []
    mk_r = ','.join(['%s'] * len(roles))
    mk_d = ','.join(['%s'] * len(dept_ids))
    rows = _q('SELECT DISTINCT e.employee_id, e.login_name, e.actual_name, e.department_id, '
              'r.role_code FROM t_employee e '
              'JOIN t_role_employee re ON re.employee_id=e.employee_id '
              'JOIN t_role r ON r.role_id=re.role_id '
              'WHERE e.deleted_flag=0 AND e.disabled_flag=0 AND r.role_code IN (' + mk_r + ') '
              'AND e.department_id IN (' + mk_d + ') ORDER BY e.employee_id',
              list(roles) + list(dept_ids), limit=500)
    return [{'employee_id': _i(r['employee_id']), 'login_name': r.get('login_name') or '',
             'name': r.get('actual_name') or '', 'dept_id': _i(r.get('department_id')),
             'role_code': r.get('role_code'), 'disabled': False} for r in rows]


def _dept_names(dept_ids) -> dict:
    """一次把部门名查回来（每开一条单查一次部门，下发按钮会转十几秒）。"""
    ids = sorted({int(x) for x in (dept_ids or []) if x})
    if not ids:
        return {}
    rows = _q('SELECT department_id, department_name FROM t_department WHERE department_id IN ('
              + ','.join(['%s'] * len(ids)) + ')', ids, limit=500)
    return {_i(r['department_id']): (r.get('department_name') or '') for r in rows}


def _fanout_targets(db: Session, parent: DispatchTask, act: dict,
                    body: dict) -> tuple[list[dict], list[dict], bool]:
    """拆出目标：给了人就按人（坏账号进 skipped），没给就按"下一级每个单位找一个承办人"。

    第三个返回值 `auto` 表示"目标是从组织树上自己找出来的"：这类目标的位置和层级
    由 `_child_scopes` + 职责线保证，不用再逐个 `_guard_chain`（每次都要打业务库）。
    """
    skipped: list[dict] = []
    targets: list[dict] = []
    explicit = body.get('targets') or []
    if explicit:
        for t in explicit:
            eid = _i((t or {}).get('employee_id'))
            emp = _emp(eid) if eid else None
            if not emp or emp['disabled']:
                skipped.append({'target': str(eid or (t or {}).get('employee_id') or t),
                                'reason': '该账号不存在或已停用'})
                continue
            targets.append(emp)
        return targets, skipped, False

    category = _category_of_task(db, parent)
    roles = _down_roles(category, act.get('role_code'))
    if not roles:
        raise DispatchError('NO_TARGET',
                            '%s下面没有可下发的岗位，这一级只能自己办。' % CH.name_of(act.get('role_code')),
                            422)
    scopes = _child_scopes(parent)
    emps = _emps_at(roles, [s['dept_id'] for s in scopes if s['dept_id']])
    by_key: dict[tuple, list[dict]] = {}
    for e in emps:                      # 已按 employee_id 排序，取第一个就是确定性的人
        by_key.setdefault((e['dept_id'], e['role_code']), []).append(e)
    seen: set[int] = set()
    for sc in scopes:
        hit = []
        for role in roles:
            # 同一单位有多个同岗位账号时取第一个：一个单位只开一张单（不然一件事两张单）
            cand = [e for e in (by_key.get((sc['dept_id'], role)) or [])
                    if e['employee_id'] not in seen]
            if cand:
                hit.append(cand[0])
        if not hit:
            skipped.append({'target': sc['scope_name'],
                            'reason': '该单位没有可用的%s账号'
                                      % '、'.join(CH.name_of(r) for r in roles)})
            continue
        for emp in hit:
            seen.add(emp['employee_id'])
            targets.append(emp)
    return targets, skipped, True


def _own_alert(db: Session, parent: DispatchTask, sc: dict) -> AlertItem | None:
    """目标自己那条预警：同一子类 + 范围正好是目标这一级。查不到就退 manual。"""
    pa = db.get(AlertItem, int(parent.alert_id)) if parent.alert_id else None
    if pa is None or not sc.get('scope_key'):
        return None
    return db.execute(select(AlertItem).where(
        AlertItem.subtype == pa.subtype, AlertItem.scope_type == sc['scope_type'],
        AlertItem.scope_key == str(sc['scope_key']),
        AlertItem.status != 'closed').order_by(AlertItem.id.desc()).limit(1)).scalar_one_or_none()


def _reuse_child(db: Session, parent: DispatchTask, emp: dict) -> DispatchTask | None:
    """已经给他发过这一单了吗（同父 + 同主责 + 未闭环）。"""
    ids = db.execute(select(DispatchTaskAssignee.task_id).where(
        DispatchTaskAssignee.employee_id == emp['employee_id'],
        DispatchTaskAssignee.part == 'owner')).scalars().all()
    if not ids:
        return None
    return db.execute(select(DispatchTask).where(
        DispatchTask.id.in_(list(ids)), DispatchTask.parent_task_id == parent.id,
        DispatchTask.deleted_flag == 0,
        DispatchTask.status.notin_(('closed', 'cancelled')))
        .order_by(DispatchTask.id.desc()).limit(1)).scalar_one_or_none()


def _min_dt(a, b):
    return min(a, b) if (a and b) else (a or b)


def _fanout_title(parent: DispatchTask, body: dict, scope_name: str) -> str:
    tpl = (body.get('title_template') or '').strip()
    title = tpl.replace('{scope}', scope_name) if tpl else '%s：%s' % (parent.title, scope_name)
    return title[:200]


def _fanout_time(parent: DispatchTask, body: dict, alert: AlertItem | None,
                 now: datetime) -> tuple[int, object, object]:
    """时限一律夹在父任务窗口内 —— 不然会出现"父已逾期升级、子还早"的荒唐状态。"""
    reply = int(body.get('require_reply', 1) or 0)
    due = _min_dt(_parse_dt(body.get('due_at')) or parent.due_at, parent.due_at)
    if reply == 0:
        return reply, due, None
    default_reply = (parent.reply_due_at or
                     now + timedelta(hours=DR.reply_hours(
                         (alert.subtype if alert is not None else None),
                         (alert.level if alert is not None else parent.level))))
    reply_due = _min_dt(_parse_dt(body.get('reply_due_at')) or default_reply, parent.reply_due_at)
    return reply, due, reply_due


def _fanout_child(db: Session, parent: DispatchTask, act: dict, emp: dict, sc: dict,
                  alert: AlertItem | None, body: dict, dept_name: str | None = None) -> DispatchTask:
    """给一个目标开一条自己的单：核销范围、预警、对象全取他自己那条。"""
    now = _now()
    hint = dict((alert.verify_hint or {}) if alert is not None else {})
    rule = hint if hint.get('kind') else {'kind': 'manual'}
    reply, due, reply_due = _fanout_time(parent, body, alert, now)
    task = DispatchTask(
        task_no=_next_task_no(db), task_type=parent.task_type or 'alert',
        title=_fanout_title(parent, body, sc['scope_name']),
        requirement=(body.get('requirement') or parent.requirement or ''),
        status='open', level=(alert.level if alert is not None else parent.level),
        parent_task_id=parent.id, root_task_id=parent.root_task_id or parent.id,
        case_id=parent.case_id or _next_case_id(db), case_key=parent.case_key,
        alert_id=(alert.id if alert is not None else None),
        scope_type=sc['scope_type'], scope_key=sc['scope_key'], scope_name=sc['scope_name'],
        biz_type=(alert.object_type if alert is not None else None),
        biz_keys=([alert.object_key] if alert is not None and alert.object_key
                  and alert.object_key != '*' else []),
        creator_id=int(act['employee_id']), creator_name=act['name'],
        creator_login=act['login_name'], creator_dept_id=act.get('dept_id'),
        require_plan=int(body.get('require_plan') or 0), require_reply=reply,
        verifier_type=('auto' if rule.get('kind') not in (None, '', 'manual') else 'human'),
        verify_rule=rule, reply_due_at=reply_due, due_at=due or now,
    )
    db.add(task)
    db.flush()
    db.add(DispatchTaskAssignee(
        task_id=task.id, part='owner', employee_id=emp['employee_id'],
        login_name=emp['login_name'], name=emp['name'], dept_id=emp['dept_id'],
        dept_name=(dept_name if dept_name is not None
                   else (_dept(emp['dept_id']) or {}).get('name')),
        role_code=(emp.get('role_code') or _role_of(emp['employee_id'])), state='pending'))
    _event(db, task, 'create', act, 'draft', 'open',
           '由 %s 裂变下发' % parent.task_no,
           {'parent_task_id': parent.id, 'source': 'fanout'})
    _notice(db, task, 'assign', emp, '【新任务】%s' % task.title,
            '要求：%s\n回执时限：%s' % (task.requirement, _iso(task.reply_due_at)),
            dedupe='task:%d:assign:%s' % (task.id, emp['login_name']))
    return task


def _fanout_joint(db: Session, parent: DispatchTask, act: dict, targets: list[dict],
                  body: dict) -> DispatchTask:
    """协同：一件事几个单位一起干，拆不开范围 → 一条任务、多个主责、核销看父的整范围。"""
    now = _now()
    reply, due, reply_due = _fanout_time(parent, body, None, now)
    task = DispatchTask(
        task_no=_next_task_no(db), task_type=parent.task_type or 'alert',
        title=_fanout_title(parent, body, parent.scope_name or '协同'),
        requirement=(body.get('requirement') or parent.requirement or ''),
        status='open', level=parent.level,
        parent_task_id=parent.id, root_task_id=parent.root_task_id or parent.id,
        case_id=parent.case_id or _next_case_id(db), case_key=parent.case_key,
        alert_id=parent.alert_id, scope_type=parent.scope_type,
        scope_key=parent.scope_key, scope_name=parent.scope_name,
        biz_type=parent.biz_type, biz_keys=list(parent.biz_keys or []),
        creator_id=int(act['employee_id']), creator_name=act['name'],
        creator_login=act['login_name'], creator_dept_id=act.get('dept_id'),
        require_plan=int(body.get('require_plan') or 0), require_reply=reply,
        verifier_type=parent.verifier_type or 'auto',
        verify_rule=dict(parent.verify_rule or {}),   # 整个范围，谁也别想只看自己那段
        reply_due_at=reply_due, due_at=due or now,
    )
    db.add(task)
    db.flush()
    for emp in targets:
        db.add(DispatchTaskAssignee(
            task_id=task.id, part='owner', employee_id=emp['employee_id'],
            login_name=emp['login_name'], name=emp['name'], dept_id=emp['dept_id'],
            dept_name=(_dept(emp['dept_id']) or {}).get('name'),
            role_code=_role_of(emp['employee_id']), state='pending'))
        _notice(db, task, 'assign', emp, '【协同任务】%s' % task.title,
                '这件事几个单位一起干，各自按分工推进',
                dedupe='task:%d:assign:%s' % (task.id, emp['login_name']))
    _event(db, task, 'create', act, 'draft', 'open', '由 %s 协同下发' % parent.task_no,
           {'parent_task_id': parent.id, 'source': 'fanout', 'mode': 'joint'})
    return task


def fanout(db: Session, account: str, task_id: int, body: dict) -> dict:
    """批量裂变：同一事项下一次给 N 个直接下级各开一条单。

    三条口径（Fred 2026-10-28 定）：
      ① 只拆一层：市 → 县、县 → 所；跨级/跨单位一律 409。
      ② 每条子任务的核销范围是他**自己**那条预警，不继承父的全市。
      ③ 时限夹在父任务窗口内。
    范围拆不开（一件事几个单位一起干）→ `mode=joint`：一条任务、多个主责。
    """
    act = _actor(account)
    parent = _get_task(db, task_id)
    _need(db, parent, act, 'owner', 'creator', 'superior', 'admin')
    if parent.status in ('closed', 'cancelled'):
        raise DispatchError('INVALID_TRANSITION',
                            '任务已%s，不能再下发' % parent.status, 409)
    mode = (body.get('mode') or 'per_target').strip()
    if mode not in ('per_target', 'joint'):
        raise DispatchError('INVALID_MODE',
                            'mode 只能是 per_target（一任务一所）或 joint（协同）', 422)

    targets, skipped, auto = _fanout_targets(db, parent, act, body)
    created: list[dict] = []
    reused: list[dict] = []
    adopted: list[dict] = []

    if mode == 'joint':
        want = sorted(e['login_name'] for e in targets)
        for other in db.execute(select(DispatchTask).where(
                DispatchTask.parent_task_id == parent.id, DispatchTask.deleted_flag == 0,
                DispatchTask.status.notin_(('closed', 'cancelled')))).scalars().all():
            owners = sorted(a.login_name for a in _assignees(db, other.id) if a.part == 'owner')
            if owners == want:
                reused.append({'task_id': other.id, 'task_no': other.task_no,
                               'reason': '这批人已经有协同任务了'})
                break
        else:
            if targets:
                joint = _fanout_joint(db, parent, act, targets, body)
                created.append({'task_id': joint.id, 'task_no': joint.task_no,
                                'assignee': '、'.join(e['name'] for e in targets),
                                'scope_name': joint.scope_name,
                                'verify_rule': dict(joint.verify_rule or {})})
    else:
        # 一次批量解析所有人的范围（单个人一条 SQL × N 会让下发按钮转很久）
        scopes = alert_scope.resolve_many([e['login_name'] for e in targets])
        dept_names = _dept_names([e.get('dept_id') for e in targets])
        for emp in targets:
            sc = _emp_scope_of(scopes.get(emp['login_name']))
            if sc is None:
                skipped.append({'target': emp['name'] or emp['login_name'],
                                'reason': '解析不出这个人的数据范围'})
                continue
            if not auto:            # 自动找出来的目标位置由组织树保证，不用再逐个查库
                _guard_chain(db, act, 'subtask', emp, parent)
            dup = _reuse_child(db, parent, emp)
            if dup is not None:
                reused.append({'task_id': dup.id, 'task_no': dup.task_no,
                               'reason': '已给他发过这一单（%s）' % dup.status})
                continue
            alert = _own_alert(db, parent, sc)
            if alert is not None:
                root = case_root(db, case_key_of_alert(alert))
                if root is not None and root.id != parent.id:
                    adopted.append({'task_id': root.id, 'task_no': root.task_no,
                                    'reason': '该单位已自行开单，已并入汇总'})
                    continue
            child = _fanout_child(db, parent, act, emp, sc, alert, body,
                                  dept_name=dept_names.get(emp.get('dept_id')))
            created.append({'task_id': child.id, 'task_no': child.task_no,
                            'assignee': emp['name'] or emp['login_name'],
                            'scope_name': sc['scope_name'],
                            'verify_rule': dict(child.verify_rule or {})})

    if not (created or reused or adopted):
        raise DispatchError('NO_TARGET', '一个可下发的目标都没有：%s'
                            % ('；'.join('%s（%s）' % (s['target'], s['reason'])
                                        for s in skipped) or '没有下级单位'),
                            422, {'skipped': skipped})

    _event(db, parent, 'fanout', act, parent.status, parent.status,
           '下发 %d 条，复用 %d 条，认领 %d 条，跳过 %d 条'
           % (len(created), len(reused), len(adopted), len(skipped)),
           {'mode': mode, 'targets': [e['employee_id'] for e in targets],
            'created': [c['task_id'] for c in created],
            'reused': [c['task_id'] for c in reused],
            'adopted': [c['task_id'] for c in adopted], 'skipped': skipped})
    _notice(db, parent, 'fanout', act, '【已下发】%s' % parent.title,
            '已下发 %d 条，复用 %d 条，跳过 %d 条'
            % (len(created), len(reused), len(skipped)),
            dedupe='task:%d:fanout:%s' % (parent.id, time.strftime('%Y%m%d%H%M%S')))
    db.commit()
    return {'parent_task_id': parent.id, 'case_id': parent.case_id, 'mode': mode,
            'created': created, 'reused': reused, 'adopted': adopted, 'skipped': skipped}


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
    role = (owners[0].role_code if owners and owners[0].role_code else None) \
        or DR.LEVEL_ROLE.get(t.scope_type, DR.LEVEL_ROLE['station'])
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
    start_dept = (owners[0].dept_id if owners else None) or t.creator_dept_id
    for nxt in DR.next_roles(cat, role):
        if nxt == 'master' and DR.MASTER_ESCALATION_BLOCK and t.status != 'closed':
            break
        if not CH.check('escalate', role, nxt)[0]:      # 职责线里若混进跨级项，跳过
            continue
        emps = _emps_up(nxt, start_dept)
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

def _child_ids(db: Session, t: DispatchTask) -> list[int]:
    """这条任务的"下级"：真子任务 + 认领（adopt）进来的那几棵树。"""
    ids = [int(x) for x in db.execute(select(DispatchTask.id).where(
        DispatchTask.parent_task_id == t.id, DispatchTask.deleted_flag == 0)).scalars().all()]
    rows = db.execute(select(DispatchTaskEvent.payload).where(
        DispatchTaskEvent.task_id == t.id,
        DispatchTaskEvent.action == 'fanout')).scalars().all()
    for payload in rows:
        for x in ((payload or {}).get('adopted') or []):
            x = _i(x)
            if x and x not in ids:
                ids.append(x)
    return ids


def _children_closed(db: Session, t: DispatchTask) -> dict:
    """上级闭环的第一个条件：子任务全闭环。

    口径照设计原文 `status == closed`；作废（cancelled）也算没闭环 ——
    下级那单被撤了，上级这件事本来就没办完（要闭环只能管理员强制留痕）。
    """
    ids = _child_ids(db, t)
    if not ids:
        return {'has_children': False, 'closed': True, 'waiting': []}
    rows = db.execute(select(DispatchTask.task_no, DispatchTask.status).where(
        DispatchTask.id.in_(ids), DispatchTask.deleted_flag == 0)).all()
    waiting = [r[0] for r in rows if r[1] != 'closed']
    return {'has_children': True, 'closed': not waiting, 'waiting': waiting}


def check_task(db: Session, t: DispatchTask) -> dict:
    """按 verify_rule 跑一次实测；**上级还要看子任务是否全闭环**。

    上级闭环要两个条件（Fred 2026-10-28 定）：子任务全闭环 **且** 本级数据达标，
    缺一个都不闭环（缺一个 `passed` 就不是 True）。
    """
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
            return _apply_children(db, t, {'passed': None, 'measured': {},
                                          'message': '人工核销规则（%s）' % (kind or 'manual')})
    except Exception as exc:  # noqa: BLE001 - 数据不可得不算通过
        logger.warning('核销规则执行失败 task=%s：%s', t.id, exc)
        return _apply_children(db, t, {'passed': None, 'measured': {},
                                       'message': '数据不可得：%s' % str(exc)[:200]})

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
    return _apply_children(db, t, got)


def _apply_children(db: Session, t: DispatchTask, got: dict) -> dict:
    """把"子任务全闭环"这一条件并进核销结果（`measured` 里写清缺哪个）。"""
    kids = _children_closed(db, t)
    if not kids['has_children']:
        return got
    got['has_children'] = True
    got['measured'] = dict(got.get('measured') or {},
                           children_closed=kids['closed'],
                           data_ok=got.get('passed'), waiting=kids['waiting'])
    base = got.get('passed')
    got['passed'] = bool(kids['closed']) and base is True
    if not kids['closed']:
        got['message'] = '%s；还有 %d 条子任务没闭环（%s）' % (
            got.get('message') or '', len(kids['waiting']),
            '、'.join(kids['waiting'][:3]))
    elif base is not True:
        got['message'] = '%s；子任务都闭环了，但本级数据没达标' % (got.get('message') or '')
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
            measured = res.get('measured') or {}
            if res.get('has_children'):
                # 上级：缺条件就是 pending（还在等下級），不是 failed
                t.verify_state = 'passed' if res.get('passed') else 'pending'
                if measured.get('children_closed') and not measured.get('data_ok'):
                    _notify_kids_done(db, t, res, now)
            else:
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
    # 草稿超 7 天没人确认 → 自动取消（不占着"待确认"状态）
    acts['drafts_cancelled'] = 0
    try:
        olds = db.execute(select(DispatchTask).where(
            DispatchTask.status == 'draft', DispatchTask.deleted_flag == 0,
            DispatchTask.create_time < now - timedelta(days=DR.DRAFT_DAYS))).scalars().all()
        for t in olds:
            t.status = 'cancelled'
            _event(db, t, 'cancel', None, 'draft', 'cancelled',
                   '草稿超 %d 天未确认，自动取消' % DR.DRAFT_DAYS)
            acts['drafts_cancelled'] += 1
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception('清理过期任务草稿失败')
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


def _notify_kids_done(db: Session, t: DispatchTask, res: dict, now: datetime):
    """下级都办完了但本级数据没达标 —— 得告诉承办人，别让他以为完事了。"""
    for a in _assignees(db, t.id):
        if a.part != 'owner':
            continue
        emp = _emp(a.employee_id) or {'login_name': a.login_name, 'name': a.name}
        _notice(db, t, 'verify', emp, '【下级已办完，数据还没达标】%s' % t.title,
                '下级都办完了，但本级实测还没达标：%s' % (res.get('message') or ''),
                dedupe='task:%d:kids_done:%s:%s' % (t.id, a.login_name,
                                                    now.strftime('%Y%m%d')))


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
    """承办人停用后自动改派：**按他自己那个岗位**换人，不是一律找所长。

    原来这里写死 `_emp_by_role('sz', ...)`：县/市两级的单（承办人是 xgscwb / sgscwb）
    永远找不到"所长" → 静默不改派，单子就此卡死（2026-10-28 修）。
    """
    n = 0
    for a in _assignees(db, t.id):
        if a.part != 'owner':
            continue
        emp = _emp(a.employee_id)
        if emp and not emp['disabled']:
            continue
        role = a.role_code or _role_of(a.employee_id)
        alt = [e for e in _emp_by_role(role, a.dept_id) if e['employee_id'] != a.employee_id] \
            if role else []
        if not alt:
            continue
        a.part = 'collaborator'
        db.add(DispatchTaskAssignee(task_id=t.id, part='owner', employee_id=alt[0]['employee_id'],
                                    login_name=alt[0]['login_name'], name=alt[0]['name'],
                                    dept_id=alt[0]['dept_id'],
                                    dept_name=(_dept(alt[0]['dept_id']) or {}).get('name'),
                                    role_code=role, state='pending'))
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
        'case_id': t.case_id, 'case_key': t.case_key,
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
    # 草稿（待确认）默认不出现在"我的任务"里；要看草稿必须显式 status=draft
    if not status:
        q = q.where(DispatchTask.status != 'draft')
    elif status == 'draft':
        q = q.where(DispatchTask.creator_id == emp_id)
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


def _plan_offer(db: Session, t: DispatchTask, act: dict) -> dict:
    """要不要我替你起草方案？

    他本来就该交方案，却对着空白框发呆 —— 这时候系统主动问一句，
    比等他拖到逾期有用。起草复用已有的 ai/plan 接口，数字照样要过溯源。
    """
    if not int(t.require_plan or 0):
        return {'offer': False, 'reason': '这个任务不要求交方案'}
    mine = [a for a in _assignees(db, t.id) if a.employee_id == act.get('employee_id')]
    if not mine:
        return {'offer': False, 'reason': '不是我承办'}
    has = db.execute(select(DispatchTaskPlan.id).where(
        DispatchTaskPlan.task_id == t.id,
        DispatchTaskPlan.kind == 'owner_plan').limit(1)).first()
    if has:
        return {'offer': False, 'reason': '方案已交'}
    return {'offer': True,
            'message': '这个任务要求交方案。要不要我按本所/本级的数据先起草一份，'
                       '你改完再提交？',
            'endpoints': {'draft': '/api/tasks/%d/ai/plan' % t.id,
                          'submit': '/api/tasks/%d/plan' % t.id},
            'deadline': _iso(t.reply_due_at)}


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
    out['plan_offer'] = _plan_offer(db, t, act)
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
                             DispatchTask.create_time, DispatchTask.closed_at)
                      .where(DispatchTask.status != 'draft')).all()
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


# ─────────────────────────────────────────────────────────── 对话建任务
# 流程：用户在对话里说"让 X 3 天内给方案" → 智能体拟稿（draft，不落正式任务）
#      → 往会话里发一条**确认消息** → 人回「确认」/点按钮 → 才变成 open 任务。
# 纪律：AI 不直接建任务；没有人的明确确认，一律停在 draft。

CONFIRM_WORDS = ('确认', '确定', '可以', '同意', '下发', '就这', '没问题', 'ok', 'OK', '行')
CANCEL_WORDS = ('取消', '算了', '不办', '先不发', '不用了')
# ⚠️ 这里**不做关键词意图判断**：实测四个象限都存在（有词不是任务 / 无词是任务 /
#    有词是任务 / 无词不是任务）。意图交给 `dispatch_ai.classify_task_intent`（语义），
#    模型不可用时**只认明确指令**（保守降级），且最终由人确认兜底。
INTENT_EXPLICIT = ('建任务', '创建任务', '发起任务', '下发', '派给', '督办')


def _fallback_intent(text: str) -> tuple[bool, str]:
    """模型不可用时的保守规则：只认"明确说要建任务/下发"的指令。"""
    t = (text or '').strip()
    if len(t) < 4:
        return False, '太短'
    if any(k in t for k in INTENT_EXPLICIT):
        return True, ''
    return False, '模型不可用，且没有明确指令（建任务/创建任务/发起任务/下发/派给/督办）'


def _parse_due(text: str, today: str):
    """从话里抠时限：3天内 / 一周内 / 月底 / 年底 / 本周。返回 (reply_due, due, 说明)。"""
    # 18:00 指北京时间。原来写 timezone.utc，界面会变成凌晨 2 点
    now = datetime.strptime(today, '%Y-%m-%d').replace(
        hour=18, tzinfo=timezone(timedelta(hours=8)))
    m = re.search(r'(\d+)\s*(天|日|周|个?月)\s*(内|以内|之内)?', text or '')
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        days = n if unit in ('天', '日') else (n * 7 if unit == '周' else n * 30)
        return now + timedelta(days=days), now + timedelta(days=max(days, 30)), '%d 天内' % days
    if '月底' in text or '本月' in text:
        d = now.replace(day=1) + timedelta(days=32)
        end = (d.replace(day=1) - timedelta(days=1)).replace(hour=18)
        return end, end, '本月底前'
    if '年底' in text or '年内' in text:
        end = now.replace(month=12, day=31, hour=18)
        return end, end, '年底前'
    if '本周' in text:
        days = 7 - now.weekday()
        end = now + timedelta(days=days)
        return end, now + timedelta(days=30), '本周内'
    return now + timedelta(days=3), now + timedelta(days=30), '默认 3 天回执 / 30 天完成'


def _match_scope(text: str, act: dict) -> dict:
    """从话里找责任范围（所/县/市）；找不到就用发起人自己的范围。"""
    t = text or ''
    stations = _q('SELECT id, name, department_id FROM t_power_company '
                  'WHERE deleted_flag=0 ORDER BY id', limit=200)
    for s in stations:
        name = str(s.get('name') or '')
        short = (name.replace('供电服务站', '').replace('供电所', '')
                 .replace('供电中心', '').replace('供电公司', ''))
        if (name and name in t) or (short and len(short) >= 2 and short in t):
            return {'scope_type': 'station', 'scope_key': str(s['id']),
                    'scope_name': name, 'station_ids': [int(s['id'])],
                    'dept_id': int(s['department_id']), 'role': DR.LEVEL_ROLE['station']}
    for key, dept_id, role, label in _county_depts():
        if key in t:
            return {'scope_type': 'county', 'scope_key': str(dept_id),
                    'scope_name': label,
                    'station_ids': [], 'dept_id': dept_id, 'role': role}
    if '全市' in t:
        return {'scope_type': 'city', 'scope_key': 'city', 'scope_name': '全市',
                'station_ids': [], 'dept_id': alert_scope._cfg()['city_dept_id'],
                'role': DR.LEVEL_ROLE['city']}
    sc = act['scope']
    return {'scope_type': sc.get('scope_type'),
            'scope_key': (str(sc['station_ids'][0]) if sc.get('scope_type') == 'station'
                          else (str(sc.get('dept_id')) if sc.get('scope_type') == 'county' else 'city')),
            'scope_name': sc.get('scope') or '',
            'station_ids': sc.get('station_ids') or [],
            'dept_id': sc.get('dept_id') or alert_scope._cfg()['city_dept_id'],
            'role': DR.LEVEL_ROLE.get(sc.get('scope_type'), DR.LEVEL_ROLE['station'])}


def _draft_candidates(scope: dict) -> list[dict]:
    dept = scope.get('dept_id') or alert_scope._cfg()['city_dept_id']
    cands = _emp_by_role(scope.get('role') or DR.LEVEL_ROLE['station'], dept)
    if cands:
        return cands[:5]
    for alt in (DR.LEVEL_ROLE['station'], DR.LEVEL_ROLE['county'], DR.LEVEL_ROLE['city']):
        cands = _emp_by_role(alt, dept) or _emp_by_role(alt, alert_scope._cfg()['city_dept_id'])
        if cands:
            return cands[:5]
    return []


def _draft_title(question: str, scope: dict) -> str:
    q = (question or '').strip().replace('\n', ' ')
    name = scope.get('scope_name') or ''
    if name and name in q:          # 话里已经带了范围，别再加前缀（否则"两水所：让两水所…"）
        return q[:200]
    if len(q) <= 36:
        return ('%s：%s' % (name, q))[:200]
    return ('%s：%s…' % (name, q[:32]))[:200]


def _draft_confirm_text(task: DispatchTask, assignee_names: list[str]) -> str:
    return (
        '我理解你要办这件事，已拟好任务草稿：\n'
        '- **事项**：%s\n'
        '- **找谁办**：%s（%s）\n'
        '- **完成时限**：%s\n'
        '%s'
        '- **怎么算完成**：%s\n\n'
        '回复「**确认**」我就下发；要改直接说（换人 / 改时间 / 改要求），或者说「取消」。'
        % (task.title,
           '、'.join(assignee_names) or '（还没找到人，请指定）',
           task.scope_name or '', _cn_dt(task.due_at),
           ('- **要回执**：%s 前交东西\n' % _cn_dt(task.reply_due_at)
            if int(task.require_reply or 0) else
            '- **不用回执**：我到点催、按数据判完成\n'),
           '数据达标自动闭环' if task.verifier_type == 'auto' else '由发起人确认闭环')
    )


def propose_from_chat(db: Session, account: str, session_id: str, question: str,
                      alert_id: int | None = None, today: str | None = None,
                      strict: bool = False, mode: str = 'explicit',
                      history: list[dict] | None = None) -> dict:
    """对话触发：**语义判断**意图 → 拟任务草稿（draft）→ 往会话里发确认消息。

    mode：
      · `auto`     —— `/api/ask` 自动挂：置信度 ≥0.7 才拟稿（不打扰）
      · `explicit` —— 用户主动调 `/api/tasks/draft`：置信度 ≥0.4 就拟稿
      · `force`    —— 用户点了"把这段对话变成任务"：不再判意图，直接拟稿
    模型不可用时退回保守规则（只认明确指令），并仍由人确认兜底。
    """
    from app.services import alert_service, dispatch_ai

    act = _actor(account)
    q = (question or '').strip()
    today = (today or time.strftime('%Y-%m-%d'))[:10]
    alert = db.get(AlertItem, int(alert_id)) if alert_id else None

    cls: dict = {'available': False, 'reason': 'force'}
    if mode == 'force':
        ok, why = True, '用户要求把这段对话变成任务'
    else:
        hist = history if history is not None else (
            alert_service.recent_messages(db, session_id, 8) if session_id else [])
        cls = dispatch_ai.classify_task_intent(q, hist)
        if cls.get('available'):
            conf = float(cls.get('confidence') or 0)
            thr = 0.7 if mode == 'auto' else 0.4
            ok = bool(cls.get('is_task')) and conf >= thr
            if ok:
                why = cls.get('reason') or ''
            elif cls.get('is_task'):
                why = '有办一件事的意思，但置信度 %.2f 低于 %.2f' % (conf, thr)
            else:
                why = cls.get('reason') or '不是要办一件事'
        else:
            ok, why = _fallback_intent(q)
            if not ok:
                why = (why + '（原因：' + str(cls.get('reason') or '') + '）')[:200]
    if not ok and not alert_id:
        return {'intent': False, 'reason': why, 'task_id': None, 'classifier': cls,
                'message': '这段话我没听出"要办一件事"。要建任务请说：让谁、办什么、几号前；'
                           '或者点"把这段对话变成任务"。'}
    if alert_id and not ok:
        ok = True   # 从预警对话里发起，上下文已经说明是要处置
        why = '预警上下文'

    existing = db.execute(select(DispatchTask).where(
        DispatchTask.creator_id == act['employee_id'],
        DispatchTask.creator_session_id == session_id,
        DispatchTask.status == 'draft', DispatchTask.deleted_flag == 0)
        .order_by(DispatchTask.id.desc()).limit(1)).scalar_one_or_none()
    if existing is not None:
        names = [a.name or a.login_name for a in _assignees(db, existing.id) if a.part == 'owner']
        return {'intent': True, 'task_id': existing.id, 'status': 'draft',
                'draft': serialize_task(existing),
                'message': _draft_confirm_text(existing, names),
                'session_id': session_id, 'reused': True}

    draft_case_key = (case_key_of_alert(alert) if alert is not None
                      else 'talk|%d|%s' % (int(act['employee_id']), session_id or ''))
    if alert is not None:
        root0 = case_root(db, draft_case_key)
        if root0 is not None:
            return {
                'intent': True, 'blocked': 'CASE_EXISTS',
                'task_id': root0.id, 'case_id': root0.case_id,
                'status': root0.status, 'draft': serialize_task(root0),
                'session_id': session_id, 'reused': True, 'classifier': cls,
                'message': (
                    '这件事已经有任务在办了：**%s**（%s 发起，当前 %s）。\n'
                    '同一件事只能有一张单，所以你不能再建一个。你可以：\n'
                    '- 说「补充」加要求\n- 说「改方案」提修改\n- 说「说明」只留个记录\n'
                    % (root0.task_no, root0.creator_name or root0.creator_login, root0.status)),
            }
    if alert is not None:
        st = _station(alert.scope_key) if alert.scope_type == 'station' else {}
        scope = {'scope_type': alert.scope_type, 'scope_key': alert.scope_key,
                 'scope_name': alert.scope_name,
                 'station_ids': [st.get('id')] if st else [],
                 'dept_id': st.get('dept_id') if st else (
                     _i(alert.scope_key) if alert.scope_type == 'county' else 1),
                 'role': alert.assignee_role or DR.LEVEL_ROLE.get(
                     alert.scope_type, DR.LEVEL_ROLE['station'])}
        title = (q or alert.title)[:200]
        requirement = q or ('处置预警：%s' % alert.title)
        verify_rule = alert.verify_hint or {}
        verifier_type = 'auto' if (verify_rule or {}).get('kind') else 'human'
        level = alert.level
        biz_type = alert.object_type
        biz_keys = [alert.object_key] if alert.object_key not in (None, '*') else []
    else:
        scope = _match_scope(q, act)
        title = _draft_title(q, scope)
        requirement = q
        verify_rule = {}
        verifier_type = 'human'
        level = 'mid'
        biz_type, biz_keys = 'org', []

    reply_due, due, due_note = _parse_due(q, today)
    cands = _draft_candidates(scope)
    require_plan = 1 if any(k in q for k in ('方案', '回执', '报告')) else 0

    task = DispatchTask(
        task_no=_next_task_no(db), task_type='alert' if alert else 'talk',
        title=title, requirement=requirement, status='draft', level=level,
        case_id=_next_case_id(db), case_key=draft_case_key,
        alert_id=alert.id if alert else None,
        scope_type=scope.get('scope_type'), scope_key=scope.get('scope_key'),
        scope_name=scope.get('scope_name'), biz_type=biz_type, biz_keys=biz_keys,
        creator_id=int(act['employee_id']), creator_name=act['name'],
        creator_login=act['login_name'], creator_dept_id=act.get('dept_id'),
        creator_session_id=session_id,
        reply_due_at=reply_due, due_at=due, require_plan=require_plan,
        verifier_type=verifier_type, verify_rule=verify_rule,
    )
    db.add(task)
    db.flush()
    task.root_task_id = task.id
    for c in cands:
        db.add(DispatchTaskAssignee(
            task_id=task.id, part='owner', employee_id=c['employee_id'],
            login_name=c['login_name'], name=c['name'], dept_id=c['dept_id'],
            dept_name=(_dept(c['dept_id']) or {}).get('name'),
            role_code=_role_of(c['employee_id']), state='pending'))
    _event(db, task, 'create', act, None, 'draft', q,
           {'source': 'chat', 'question': q, 'due_note': due_note,
            'alert_id': task.alert_id, 'mode': mode, 'classifier': cls,
            'intent_reason': why})
    db.commit()

    names = [c['name'] or c['login_name'] for c in cands]
    text = _draft_confirm_text(task, names)
    card = {'task_id': task.id, 'task_no': task.task_no, 'title': task.title,
            'scope_name': task.scope_name, 'role': scope.get('role'),
            'assignees': [{'employee_id': c['employee_id'], 'login_name': c['login_name'],
                           'name': c['name'], 'dept_name': (c.get('dept_name') or '')}
                          for c in cands],
            'reply_due_at': _iso(task.reply_due_at), 'due_at': _iso(task.due_at),
            'reply_due_text': _cn_dt(task.reply_due_at), 'due_text': _cn_dt(task.due_at),
            'require_reply': int(task.require_reply or 0),
            'verifier_type': task.verifier_type, 'verify_rule': task.verify_rule or {},
            'require_plan': task.require_plan, 'due_note': due_note}
    alert_service.append_chat_message(
        db, session_id, account, 2, text, msg_type=2,
        meta={'kind': 'task_draft', 'task_id': task.id, 'draft': card},
        title='【任务草稿】%s' % task.title)
    return {'intent': True, 'task_id': task.id, 'status': 'draft', 'draft': card,
            'message': text, 'session_id': session_id, 'reused': False,
            'classifier': cls, 'mode': mode}


def confirm_draft(db: Session, account: str, task_id: int | None = None,
                  session_id: str | None = None, message: str | None = None,
                  overrides: dict | None = None) -> dict:
    """人工确认草稿 → 正式任务（draft → open，发通知）。也支持用一句话确认。"""
    from app.services import alert_service

    act = _actor(account)
    t = None
    if task_id:
        t = _get_task(db, int(task_id))
    elif session_id:
        t = db.execute(select(DispatchTask).where(
            DispatchTask.creator_id == act['employee_id'],
            DispatchTask.creator_session_id == session_id,
            DispatchTask.status == 'draft', DispatchTask.deleted_flag == 0)
            .order_by(DispatchTask.id.desc()).limit(1)).scalar_one_or_none()
    if t is None:
        raise DispatchError('DRAFT_NOT_FOUND', '没有待确认的任务草稿', 404)
    if t.creator_id != act['employee_id'] and act.get('role_code') != 'admin':
        raise DispatchError('NOT_PARTICIPANT', '只有发起人能确认这个草稿', 403)
    if t.status != 'draft':
        raise DispatchError('INVALID_TRANSITION', '任务已不是草稿（%s）' % t.status, 409)

    msg = (message or '').strip()
    if msg:
        if any(w in msg for w in CANCEL_WORDS):
            fs = _move(t, 'cancelled')
            _event(db, t, 'cancel', act, fs, 'cancelled', msg)
            db.commit()
            return dict(task_detail(db, account, t.id), cancelled=True)
        if not any(w in msg for w in CONFIRM_WORDS):
            raise DispatchError('NEED_CONFIRM', '没有识别到确认；请回复「确认」或「取消」', 409,
                                {'draft': serialize_task(t)})

    ov = overrides or {}
    if ov.get('assignees'):
        for a in _assignees(db, t.id):
            db.delete(a)
        db.flush()
        for a in ov['assignees']:
            emp = _emp(a.get('employee_id'))
            if not emp or emp['disabled']:
                raise DispatchError('BAD_ASSIGNEE', '承办人不存在或已停用', 422)
            db.add(DispatchTaskAssignee(
                task_id=t.id, part=a.get('part') or 'owner', employee_id=emp['employee_id'],
                login_name=emp['login_name'], name=emp['name'], dept_id=emp['dept_id'],
                dept_name=(_dept(emp['dept_id']) or {}).get('name'),
                role_code=_role_of(emp['employee_id']), state='pending'))
        db.flush()
    if not [a for a in _assignees(db, t.id) if a.part == 'owner']:
        raise DispatchError('NO_OWNER', '草稿还没有承办人，请先指定（assignees）', 422)
    if ov.get('title'):
        t.title = str(ov['title'])[:200]
    if ov.get('requirement'):
        t.requirement = str(ov['requirement'])
    t.reply_due_at = _parse_dt(ov.get('reply_due_at')) or t.reply_due_at
    t.due_at = _parse_dt(ov.get('due_at')) or t.due_at
    if ov.get('require_plan') is not None:
        t.require_plan = int(ov['require_plan'])
    if ov.get('verifier_type'):
        t.verifier_type = ov['verifier_type']
    if ov.get('verify_rule'):
        t.verify_rule = ov['verify_rule']

    fs = _move(t, 'open')
    _event(db, t, 'confirm', act, fs, 'open', msg or '确认下发',
           {'source': 'chat', 'confirmed_by_message': bool(msg)})
    for a in _assignees(db, t.id):
        if a.part == 'cc':
            continue
        emp = _emp(a.employee_id) or {'login_name': a.login_name, 'name': a.name}
        _notice(db, t, 'assign', emp, '【新任务】%s' % t.title,
                '要求：%s\n回执时限：%s' % (t.requirement, _iso(t.reply_due_at)),
                dedupe='task:%d:assign:%s' % (t.id, a.login_name))
    if t.alert_id:
        a = db.get(AlertItem, int(t.alert_id))
        if a is not None:
            a.status = 'dispatched'
            a.task_id = t.id
    db.commit()
    if t.creator_session_id:
        alert_service.append_chat_message(
            db, t.creator_session_id, account, 2,
            '任务已下发：**%s**（%s）。承办人会收到通知，后续进展我会在这里同步。'
            % (t.title, t.task_no),
            meta={'kind': 'task_confirmed', 'task_id': t.id})
    return task_detail(db, account, t.id)


def pending_draft(db: Session, account: str, session_id: str) -> dict:
    act = _actor(account)
    t = db.execute(select(DispatchTask).where(
        DispatchTask.creator_id == act['employee_id'],
        DispatchTask.creator_session_id == session_id,
        DispatchTask.status == 'draft', DispatchTask.deleted_flag == 0)
        .order_by(DispatchTask.id.desc()).limit(1)).scalar_one_or_none()
    if t is None:
        return {'has_draft': False, 'task_id': None}
    names = [a.name or a.login_name for a in _assignees(db, t.id) if a.part == 'owner']
    return {'has_draft': True, 'task_id': t.id, 'draft': serialize_task(t),
            'message': _draft_confirm_text(t, names)}


def draft_from_conversation(db: Session, account: str, session_id: str,
                            alert_id: int | None = None, today: str | None = None,
                            instruction: str = '') -> dict:
    """把**整段对话**变成任务草稿（前端"把这段对话变成任务"按钮）。

    与 /draft 的区别：这里**不判意图**（用户已明确要求），直接用最近一轮用户原话/指令拟稿。
    """
    from app.services import alert_service

    hist = alert_service.recent_messages(db, session_id, 10)
    if not hist:
        raise DispatchError('EMPTY_CONVERSATION', '这段会话还没有内容，没法变成任务', 422)
    q = (instruction or '').strip()
    if not q:
        users = [h['content'] for h in hist if h.get('role') == 'user']
        q = users[-1] if users else hist[-1].get('content') or ''
    return propose_from_chat(db, account, session_id, q, alert_id=alert_id,
                             today=today, mode='force', history=hist)


# ─────────────────────────────────────────────────────────── 通知

# ─────────────────────────────────────────────────────────── 事项：补充 / 说明

def _case_member(db: Session, t: DispatchTask, act: dict) -> bool:
    """这个人算不算"看得到这件事"的人。

    两类：
      ① 任务树上的人（发起人 / 主责 / 协办 / 抄送 / 被升级到的上级）；
      ② 同一事项的预警也推给了他 —— 哪怕他没参与任务，只要他是这一级的
         责任人，就能看到处理方案、能补充、能说明，但**不能**改状态。
    """
    if _access(db, t, act) is not None:
        return True
    # 整棵树里任一任务参与过就算 —— 县财务常是第 3 层的发起人，不在根任务上
    if t.case_id:
        sibs = db.execute(select(DispatchTask).where(
            DispatchTask.case_id == t.case_id,
            DispatchTask.deleted_flag == 0)).scalars().all()
        for x in sibs:
            if _access(db, x, act) is not None:
                return True
    if not t.case_key:
        return False
    sc = act.get('scope') or {}
    rows = db.execute(select(AlertItem).where(
        AlertItem.case_key == t.case_key)).scalars().all()
    for a in rows:
        if a.assignee_role != act.get('role_code'):
            continue
        if (a.scope_type == sc.get('scope_type')
                and str(a.scope_key) == str(sc.get('scope_key'))):
            return True
        if a.scope_type == 'station' and act.get('dept_id'):
            st = _station(a.scope_key)
            if CH.is_ancestor(act['dept_id'], st.get('dept_id'), _dept_parent):
                return True
    return False


def _append_note(db: Session, account: str, task_id: int, content: str,
                 supplement: bool) -> dict:
    act = _actor(account)
    t = _get_task(db, task_id)
    body = (content or '').strip()
    if not body:
        raise DispatchError('EMPTY_CONTENT', '内容不能为空', 422)
    if not _case_member(db, t, act):
        raise DispatchError('NOT_PARTICIPANT', '这件事不在你的范围内，看不到也不该改', 403)
    who = '%s（%s）' % (act.get('name') or act.get('login_name'),
                       CH.name_of(act.get('role_code')))
    frm = t.status
    if supplement:
        t.requirement = ((t.requirement or '') +
                         '\n\n【补充 · %s】%s' % (who, body))
        action, ev_text = 'supplement', '补充要求：%s' % body
    else:
        action, ev_text = 'comment', body
    _event(db, t, action, act, frm, t.status, ev_text,
           {'role': act.get('role_code'), 'supplement': bool(supplement)})
    for a in _assignees(db, t.id):
        if a.employee_id == act.get('employee_id'):
            continue
        emp = _emp(a.employee_id) or {'login_name': a.login_name, 'name': a.name}
        _notice(db, t, 'comment', emp,
                '【%s】%s' % ('补充' if supplement else '说明', t.title),
                '%s：%s' % (who, body),
                dedupe='task:%d:note:%s' % (t.id, uuid.uuid4().hex))
    db.commit()
    return {'task_id': t.id, 'task_no': t.task_no, 'case_id': t.case_id,
            'status': t.status, 'added': 'supplement' if supplement else 'comment',
            'requirement': t.requirement}


def comment(db: Session, account: str, task_id: int, content: str) -> dict:
    """说明：不要求任何人办，只留个记录（同级/上级/相关人都能写）。"""
    return _append_note(db, account, task_id, content, supplement=False)


def supplement(db: Session, account: str, task_id: int, content: str) -> dict:
    """补充要求：追加到任务要求里，会上门通知承办人，不改状态。"""
    return _append_note(db, account, task_id, content, supplement=True)


def case_view(db: Session, account: str, case_id: str) -> dict:
    """一个事项的全景：整棵树 + 各层方案 + 补充/说明时间线。

    查法：先按事项号（C20261021-00001）查，查不到再按 case_key 查。
    """
    act = _actor(account)
    key = (case_id or '').strip()
    if not key:
        raise DispatchError('CASE_NOT_FOUND', '没给事项号', 422)
    tasks = db.execute(select(DispatchTask).where(
        DispatchTask.case_id == key, DispatchTask.deleted_flag == 0)
        .order_by(DispatchTask.id)).scalars().all()
    if not tasks:
        tasks = db.execute(select(DispatchTask).where(
            DispatchTask.case_key == key, DispatchTask.deleted_flag == 0)
            .order_by(DispatchTask.id)).scalars().all()
    if not tasks:
        raise DispatchError('CASE_NOT_FOUND', '没有这个事项', 404)
    root = next((x for x in tasks if x.parent_task_id is None), tasks[0])
    if not _case_member(db, root, act):
        raise DispatchError('NOT_PARTICIPANT', '这件事不在你的范围内', 403)

    ids = [x.id for x in tasks]
    evs = db.execute(select(DispatchTaskEvent).where(
        DispatchTaskEvent.task_id.in_(ids)).order_by(DispatchTaskEvent.id)).scalars().all()
    plans = db.execute(select(DispatchTaskPlan).where(
        DispatchTaskPlan.task_id.in_(ids)).order_by(DispatchTaskPlan.id)).scalars().all()

    def depth(t):
        d, cur, seen = 0, t, set()
        while cur.parent_task_id and cur.parent_task_id not in seen and d < 8:
            seen.add(cur.parent_task_id)
            cur = next((x for x in tasks if x.id == cur.parent_task_id), cur)
            d += 1
        return d

    tree = []
    for x in tasks:
        row = serialize_task(x)
        row['depth'] = depth(x)
        row['assignees'] = [{'name': a.name, 'login_name': a.login_name,
                             'part': a.part, 'state': a.state} for a in _assignees(db, x.id)]
        tree.append(row)

    mine = _access(db, root, act)
    return {
        'case_id': root.case_id, 'case_key': root.case_key,
        'title': root.title, 'status': root.status, 'level': root.level,
        'scope_name': root.scope_name,
        'creator': {'name': root.creator_name, 'login_name': root.creator_login},
        'my_role': mine or 'observer',
        'tree': tree,
        'plans': [{'task_id': p.task_id, 'kind': p.kind, 'version': p.version,
                   'title': p.title, 'content': p.content, 'items': p.items,
                   'author_name': p.author_name, 'accepted': p.accepted,
                   'create_time': _iso(p.create_time)} for p in plans],
        'timeline': [{'task_id': e.task_id, 'action': e.action, 'actor_name': e.actor_name,
                      'actor_role': e.actor_role, 'content': e.content,
                      'payload': e.payload, 'create_time': _iso(e.create_time)}
                     for e in evs],
        'can': {'comment': True, 'supplement': True,
                'revise_plan': mine in ('owner', 'collaborator'),
                'change_status': mine in ('creator', 'owner', 'superior', 'admin'),
                'create_task': False},
        'note': '同一件事只有一张单：可以补充 / 改方案 / 写说明，不能重复建任务。',
    }


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
           'add_attachment', 'list_attachments', 'attachment_file', 'report_docx', 'ask',
           'propose_from_chat', 'confirm_draft', 'pending_draft', 'draft_from_conversation']


# ── 站点可配：县公司清单（P3, 2026-10-21）──────────────────────────────
# 原来写死 ('曾都',10,'xgscwb'), ('随县',11,...), ('高新',12,...), ('广水',13,...)。
# 真源在 config/sites/<SITE>/scopes.yml 的 department.county_depts；换单位必须改这里。
def _county_depts() -> list:
    try:
        from app.secretary import project
        dep = project.department()
        rows = dep.get('county_depts') or []
        out = []
        for r in rows:
            key = str(r.get('key') or '').strip()
            if not key:
                continue
            label = str(r.get('label') or (key + '供电公司'))
            out.append((key, int(r.get('dept_id') or 0), str(r.get('role') or 'xgscwb'), label))
        if out:
            return out
    except Exception:                              # noqa: BLE001
        pass
    # 站点包也读不到就别猜：宁可这条判范围失败（回落到发言人自己的范围），
    # 也不要拿别的地市/别的县的部门 ID 往库里怼。
    logger.warning('站点包没有 county_depts，县范围识别关闭')
    return []
