# -*- coding: utf-8 -*-
"""任务流转里"系统自己动"的两条口径（交接 §4 待办）：

  R 组 `require_reply=0`：不催回执、不因回执超期升级（只按 due_at 盯数据）
  D 组 承办人停用后自动改派：同岗位换人，角色不许写死成所长

跑法： py -X utf8 server/tests/agent/test_dispatch_controls.py
产出： output/test_results/<日期>-任务流转控制/raw-controls.json
"""
import json
import os
import sys
import time
from datetime import timedelta
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT / 'server'))
os.environ.setdefault('DISPATCH_AI_ON', '0')

from sqlalchemy import select, text  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.dispatch import (  # noqa: E402
    AlertItem, DispatchNotice, DispatchTask, DispatchTaskAssignee, DispatchTaskEvent,
)
from app.secretary import alert_scope  # noqa: E402
from app.services import alert_service, dispatch_service as S  # noqa: E402
from app.services.dispatch_service import DispatchError  # noqa: E402

OUT = ROOT / 'output' / 'test_results' / ('%s-任务流转控制' % time.strftime('%Y-%m-%d'))
OUT.mkdir(parents=True, exist_ok=True)
RESULTS = []
TB = ['alert_scan_run', 'alert_item', 'dispatch_task', 'dispatch_task_assignee',
      'dispatch_task_event', 'dispatch_task_plan', 'dispatch_check', 'dispatch_notice',
      'dispatch_attachment', 'alert_chat_link']


def check(no, name, ok, detail=''):
    RESULTS.append({'no': no, 'name': name, 'ok': bool(ok), 'detail': str(detail)[:900]})
    print('%s %-5s %s%s' % ('PASS' if ok else 'FAIL', no, name,
                            ('  | ' + str(detail)[:190]) if detail else ''))


def reset(db):
    assert '127.0.0.1' in settings.database_url and '5433' in settings.database_url, \
        '只允许在本地 5433 测试库跑'
    db.execute(text('TRUNCATE %s RESTART IDENTITY CASCADE' % ', '.join(TB)))
    db.execute(text("DELETE FROM t_chat_message WHERE chat_session_id LIKE 'alert-%%'"))
    db.execute(text("DELETE FROM t_chat_session WHERE session_id LIKE 'alert-%%'"))
    db.commit()


def emp(login):
    e = S._emp_by_login(login)
    assert e, '业务库里没有账号 %s' % login
    return e


def make_alert(db, *, scope, scope_key, scope_name, role, hint, title, dedupe,
               object_key='*'):
    alert_service._upsert(db, {
        'alert_type': 'budget', 'subtype': 'budget.underspend', 'level': 'high',
        'title': title, 'detail': '', 'data': {},
        'scope_type': scope, 'scope_key': scope_key, 'scope_name': scope_name,
        'object_type': 'station', 'object_key': object_key,
        'assignee_role': role, 'verify_hint': hint, 'dedupe_key': dedupe,
    }, S._now())
    db.flush()
    return db.execute(select(AlertItem).where(AlertItem.dedupe_key == dedupe)).scalar_one()


def main():
    db = SessionLocal()
    try:
        reset(db)

        # ── 夹具：一条所级预警（两水所）+ 一条县级预警（曾都，用来测停用改派）──
        sz_scope = alert_scope.resolve('sngdssz')
        st_alert = make_alert(
            db, scope='station', scope_key='42', scope_name=sz_scope['scope'], role='sz',
            hint={'kind': 'budget_rate', 'station_ids': sz_scope['station_ids'],
                  'op': '>=', 'target': 0.0},
            title='两水所预算执行进度偏慢', dedupe='ctl|station|42')
        cty_scope = alert_scope.resolve('xgscwb')
        cty_alert = make_alert(
            db, scope='county', scope_key='10', scope_name=cty_scope['scope'], role='xgscwb',
            hint={'kind': 'budget_rate', 'station_ids': cty_scope['station_ids'],
                  'op': '>=', 'target': 0.0},
            title='曾都区预算执行进度偏慢', dedupe='ctl|county|10')
        # 停用改派要另开两张单（同一事项只能一张单，CASE_EXISTS 会拦）
        d_alert = make_alert(
            db, scope='county', scope_key='10', scope_name=cty_scope['scope'], role='xgscwb',
            hint={'kind': 'budget_rate', 'station_ids': cty_scope['station_ids'],
                  'op': '>=', 'target': 0.0},
            title='曾都区：另一个对象', dedupe='ctl|county|10|d', object_key='ctl-d')
        d_alert2 = make_alert(
            db, scope='station', scope_key='42', scope_name=sz_scope['scope'], role='sz',
            hint={'kind': 'budget_rate', 'station_ids': sz_scope['station_ids'],
                  'op': '>=', 'target': 0.0},
            title='两水所：另一个对象', dedupe='ctl|station|42|d', object_key='ctl-d2')
        db.commit()

        def mk_task(login, alert_id, title, **extra):
            body = {'assignees': [{'employee_id': emp(login)['employee_id'], 'part': 'owner'}],
                    'title': title, 'idempotency_key': 'ctl-%s' % title}
            body.update(extra)
            return S.create(db, login, alert_id, body)

        # ══════════ R 组：require_reply=0 不催办、不因回执超期升级 ══════════
        far_due = (S._now() + timedelta(days=60)).isoformat()
        t_off = mk_task('sngdssz', st_alert.id, '只盯数据不回执', require_reply=0,
                        verifier_type='human', due_at=far_due)
        row_off = db.get(DispatchTask, t_off['id'])
        check('R1', 'require_reply=0 → 不设回执时限（reply_due_at 为空）',
              row_off.reply_due_at is None and row_off.require_reply == 0,
              {'reply_due_at': str(row_off.reply_due_at),
               'require_reply': row_off.require_reply})

        t_on = mk_task('xgscwb', cty_alert.id, '要回执的对照单', require_reply=1,
                       verifier_type='human', due_at=far_due)
        row_on = db.get(DispatchTask, t_on['id'])
        S.tick(db, now=S._now() + timedelta(days=30))     # 时间推到很久以后跑一轮
        urges_off = db.execute(select(DispatchNotice).where(
            DispatchNotice.task_id == row_off.id,
            DispatchNotice.notice_type == 'urge')).scalars().all()
        urges_on = db.execute(select(DispatchNotice).where(
            DispatchNotice.task_id == row_on.id,
            DispatchNotice.notice_type == 'urge')).scalars().all()
        esc_off = db.execute(select(DispatchTaskEvent).where(
            DispatchTaskEvent.task_id == row_off.id,
            DispatchTaskEvent.action == 'escalate')).scalars().all()
        check('R2', 'require_reply=0 的单 overdue 也不催办（对照组会催）',
              not urges_off and bool(urges_on),
              {'off': len(urges_off), 'on': len(urges_on)})
        check('R3', 'require_reply=0 不因"回执超期"升级',
              not any('回执超期' in (e.content or '') for e in esc_off),
              [(e.content or '')[:24] for e in esc_off])

        # ══════════ D 组：停用后自动改派，角色不许写死 ══════════
        t_d = mk_task('xgscwb', d_alert.id, '停用改派用例')
        row_d = db.get(DispatchTask, t_d['id'])
        gone_id = emp('xgscwb')['employee_id']
        real_emp = S._emp

        def fake_emp(eid):
            e = real_emp(eid)
            if e and int(eid or 0) == gone_id:
                e = dict(e, disabled=True)
            return e

        S._emp = fake_emp
        try:
            n = S._reassign_disabled(db, row_d)
        finally:
            S._emp = real_emp
        db.commit()
        owners = [a for a in db.execute(select(DispatchTaskAssignee).where(
            DispatchTaskAssignee.task_id == row_d.id)).scalars().all() if a.part == 'owner']
        check('D1', '县级承办人停用 → 按同岗位（县财务）换人，不是写死找所长',
              n == 1 and len(owners) == 1 and owners[0].role_code == 'xgscwb'
              and owners[0].employee_id != gone_id,
              {'n': n, 'new_owner': [(o.login_name, o.role_code) for o in owners]})

        # 没有同岗位替补时：不改派、不崩（留给升级/人工处理）
        t_d2 = mk_task('sngdssz', d_alert2.id, '停用改派无替补')
        row_d2 = db.get(DispatchTask, t_d2['id'])
        gone2 = emp('sngdssz')['employee_id']

        def fake_emp2(eid):
            e = real_emp(eid)
            if e and int(eid or 0) == gone2:
                e = dict(e, disabled=True)
            return e

        S._emp = fake_emp2
        try:
            n2 = S._reassign_disabled(db, row_d2)
        finally:
            S._emp = real_emp
        db.commit()
        check('D2', '该部门没有同岗位替补 → 不改派、不抛错', n2 == 0, {'n': n2})

        # ══════════ E 组：发起人改时限 / 承办人申请延期 / 回执带原因 ══════════
        row_on = db.get(DispatchTask, t_on['id'])
        child = S.subtask(db, 'xgscwb', row_on.id, {
            'title': '曾都区：三天内交调剂方案',
            'assignees': [{'employee_id': emp('sngdssz')['employee_id'], 'part': 'owner'}],
            'verifier_type': 'human'})
        child_row = db.get(DispatchTask, child['id'])

        want45 = S._now() + timedelta(days=45)
        S.adjust(db, 'xgscwb', row_on.id, {'due_at': want45.isoformat(),
                                           'requirement': '月底前执行率不低于 60%'})
        db.refresh(row_on)
        n_adj = db.execute(select(DispatchNotice).where(
            DispatchNotice.task_id == row_on.id,
            DispatchNotice.notice_type == 'adjust')).scalars().all()
        check('E1', '发起人改完成时限和要求的 → 生效，并通知承办人',
              abs((row_on.due_at - want45).total_seconds()) < 60
              and row_on.requirement.startswith('月底前') and bool(n_adj),
              {'due': str(row_on.due_at)[:16], 'notice': len(n_adj)})

        S.adjust(db, 'xgscwb', child_row.id, {'due_at': (S._now() + timedelta(days=90)).isoformat()})
        db.refresh(child_row)
        db.refresh(row_on)
        check('E2', '子任务改期限被父任务窗口夹住（给 90 天，实际落到父的 45 天）',
              child_row.due_at == row_on.due_at,
              {'child': str(child_row.due_at)[:16], 'parent': str(row_on.due_at)[:16]})

        blocked = [True, '']
        try:
            S.adjust(db, 'sngdssz', child_row.id, {'due_at': (S._now() + timedelta(days=99)).isoformat()})
            blocked = [False, '承办人竟然改成了']
        except DispatchError as exc:
            blocked = [exc.code == 'NOT_PARTICIPANT', exc.code]
        check('E3', '承办人不能自己改时限（改权限归发起人）→ NOT_PARTICIPANT',
              blocked[0], blocked[1])

        before_due = child_row.due_at
        S.request_extend(db, 'sngdssz', child_row.id, {
            'due_at': (S._now() + timedelta(days=70)).isoformat(),
            'reason': '预算指标还没下达到所'})
        db.refresh(child_row)
        ev_ext = db.execute(select(DispatchTaskEvent).where(
            DispatchTaskEvent.task_id == child_row.id,
            DispatchTaskEvent.action == 'extend_request')).scalars().all()
        nt_ext = db.execute(select(DispatchNotice).where(
            DispatchNotice.task_id == child_row.id,
            DispatchNotice.receiver_login == 'xgscwb',
            DispatchNotice.notice_type == 'extend')).scalars().all()
        check('E4', '承办人申请延期 → 留事件 + 通知发起人（带原因），时限没被自己改掉',
              bool(ev_ext) and bool(nt_ext)
              and any('预算指标还没下达到所' in (n.content or '') for n in nt_ext)
              and child_row.due_at == before_due,
              {'events': len(ev_ext), 'notices': len(nt_ext),
               'due_unchanged': child_row.due_at == before_due})

        reason = '交不出：预算指标还没下达到所，等县里批'
        S.accept(db, 'sngdssz', child_row.id)      # 先接单才能交回执
        S.reply(db, 'sngdssz', child_row.id, reason)
        nt_reply = db.execute(select(DispatchNotice).where(
            DispatchNotice.task_id == child_row.id,
            DispatchNotice.receiver_login == 'xgscwb',
            DispatchNotice.notice_type == 'reply')).scalars().all()
        check('E5', '交不出时写的实际原因会推到发起人（回执通知里带原因）',
              bool(nt_reply) and any('预算指标还没下达' in (n.content or '') for n in nt_reply),
              [(n.content or '')[:40] for n in nt_reply])

        (OUT / 'raw-controls.json').write_text(
            json.dumps(RESULTS, ensure_ascii=False, indent=1), encoding='utf-8')
    finally:
        db.rollback()
        db.close()

    bad = [r for r in RESULTS if not r['ok']]
    print('=' * 90)
    print('%d/%d PASS' % (len(RESULTS) - len(bad), len(RESULTS)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
