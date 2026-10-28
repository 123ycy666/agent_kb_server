# -*- coding: utf-8 -*-
"""批量裂变 fanout · 服务层验收（第 1 步：只测函数，不接接口）。

对应口径：docs/03-设计/任务裂变设计.md §10 的判据 1~8、12
（9~11 是"双条件闭环"，下一步再做，不在本文件里）。

数据全部自造：预警自己插、任务自己建、范围取自 alert_scope.resolve，
**不依赖业务库当天扫出来的结果** —— 那样换一天数据就红。

跑法： py -X utf8 server/tests/agent/test_dispatch_fanout.py
产出： output/test_results/<日期>-任务裂变/raw-fanout.json
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

from sqlalchemy import func, select, text  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.dispatch import (  # noqa: E402
    AlertItem, DispatchCheck, DispatchNotice, DispatchTask, DispatchTaskAssignee,
    DispatchTaskEvent,
)
from app.secretary import alert_scope  # noqa: E402
from app.services import alert_service, dispatch_service as S  # noqa: E402
from app.services.dispatch_service import DispatchError  # noqa: E402

OUT = ROOT / 'output' / 'test_results' / ('%s-任务裂变' % time.strftime('%Y-%m-%d'))
OUT.mkdir(parents=True, exist_ok=True)
RESULTS = []
TB = ['alert_scan_run', 'alert_item', 'dispatch_task', 'dispatch_task_assignee',
      'dispatch_task_event', 'dispatch_task_plan', 'dispatch_check', 'dispatch_notice',
      'dispatch_attachment', 'alert_chat_link']

# 县级：曾都(10) / 随县(11) / 高新(12) / 广水(13)
COUNTIES = [('xgscwb', 10), ('xgscwb001', 11), ('xgscwb002', 12), ('xgscwb003', 13)]


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


def scope_of(login):
    sc = alert_scope.resolve(login)
    assert sc.get('ok'), '%s 范围解析失败：%s' % (login, sc.get('error'))
    return sc


def make_alert(db, *, scope, scope_key, scope_name, role, hint, title, dedupe):
    """插一条自造预警（走真实 _upsert 路径，dedupe_key 保证唯一）。"""
    alert_service._upsert(db, {
        'alert_type': 'budget', 'subtype': 'budget.underspend', 'level': 'high',
        'title': title, 'detail': '', 'data': {},
        'scope_type': scope, 'scope_key': scope_key, 'scope_name': scope_name,
        'object_type': 'station', 'object_key': '*',
        'assignee_role': role, 'verify_hint': hint, 'dedupe_key': dedupe,
    }, S._now())
    db.flush()
    return db.execute(select(AlertItem).where(AlertItem.dedupe_key == dedupe)).scalar_one()


def expect_error(fn, code):
    try:
        fn()
    except DispatchError as exc:
        return exc.code == code, '%s：%s' % (exc.code, exc.message[:90])
    return False, '没有抛错'


def main():
    db = SessionLocal()
    try:
        reset(db)

        # ── 自造数据：1 条市级预警 + 4 条县级预警 + 1 条所级预警 ─────────
        city = scope_of('sgscwb')
        parent_alert = make_alert(
            db, scope='city', scope_key='city', scope_name='全市', role='sgscwb',
            hint={'kind': 'budget_rate', 'station_ids': city['station_ids'], 'target': 0.6},
            title='全市预算执行进度偏慢', dedupe='fanout|city|sgscwb')
        counties = {}
        for login, dept in COUNTIES:
            sc = scope_of(login)
            counties[login] = make_alert(
                db, scope='county', scope_key=str(dept), scope_name=sc['scope'],
                role='xgscwb',
                hint={'kind': 'budget_rate', 'station_ids': sc['station_ids'], 'target': 0.6},
                title='%s：预算执行进度偏慢' % sc['scope'],
                dedupe='fanout|county|%s' % dept)
        db.commit()

        parent = S.create(db, 'sgscwb', parent_alert.id, {
            'assignees': [{'employee_id': emp('sgscwb')['employee_id'], 'part': 'owner'}],
            'title': '全市预算执行率偏低：各县月底前把执行率提上去',
            'requirement': '月底前执行率不低于 60%',
            'idempotency_key': 'fanout-parent'})
        parent_row = db.get(DispatchTask, parent['id'])

        # ── F1 选 3 个县裂变 → 3 条，case_id 同父、parent_task_id 指向父 ──
        far = parent_row.due_at + timedelta(days=10)
        far_reply = parent_row.reply_due_at + timedelta(days=10)
        r1 = S.fanout(db, 'sgscwb', parent['id'], {
            'targets': [{'employee_id': emp(l)['employee_id']}
                        for l in ('xgscwb001', 'xgscwb002', 'xgscwb003')],
            'title_template': '{scope}：月底前把预算花完',
            'due_at': far.isoformat(), 'reply_due_at': far_reply.isoformat()})
        ids = [c['task_id'] for c in r1['created']]
        rows = [db.get(DispatchTask, i) for i in ids]
        check('F1', '选 3 个县裂变 → 生成 3 条，case_id 与父相同、parent 指向父',
              len(ids) == 3
              and all(t.case_id == parent_row.case_id for t in rows)
              and all(t.parent_task_id == parent_row.id for t in rows)
              and all(t.root_task_id == (parent_row.root_task_id or parent_row.id)
                      for t in rows),
              {'created': len(ids), 'case': parent_row.case_id,
               'task_no': [t.task_no for t in rows]})

        # ── F2 每条子任务的 station_ids = 该县自己的范围（不是父的全市）──
        got = {t.scope_name: (t.verify_rule or {}).get('station_ids') or [] for t in rows}
        want = {scope_of(l)['scope']: scope_of(l)['station_ids']
                for l in ('xgscwb001', 'xgscwb002', 'xgscwb003')}
        check('F2', '子任务核销范围 = 该县自己的所（不是父的全市 47 个）',
              got == want
              and all(sorted(v) != sorted(city['station_ids']) for v in got.values()),
              {'子任务': {k: len(v) for k, v in got.items()},
               '父': len(city['station_ids'])})

        # ── F3 子任务时限必须夹在父的窗口内 ────────────────────────
        check('F3', 'due_at/reply_due_at 都不得晚于父（下发时故意给更晚的日期）',
              all(t.due_at <= parent_row.due_at for t in rows)
              and all(t.reply_due_at is not None and t.reply_due_at <= parent_row.reply_due_at
                      for t in rows),
              {'子due': [str(t.due_at)[:16] for t in rows],
               '父due': str(parent_row.due_at)[:16]})

        # ── F4 子任务 creator = 下发人，assignee = 下级 ──────────────
        owners = [db.execute(select(DispatchTaskAssignee).where(
            DispatchTaskAssignee.task_id == t.id,
            DispatchTaskAssignee.part == 'owner')).scalars().first() for t in rows]
        check('F4', '子任务 creator 是下发人（市财务），主责是县财务',
              all(t.creator_login == 'sgscwb' for t in rows)
              and [o.login_name for o in owners] == ['xgscwb001', 'xgscwb002', 'xgscwb003'],
              {'creator': [t.creator_login for t in rows],
               'owner': [o.login_name for o in owners]})

        # ── F5 同一批再裂一次 → 不新增（reused 命中）────────────────
        n_before = db.execute(select(func.count()).select_from(DispatchTask)).scalar_one()
        r2 = S.fanout(db, 'sgscwb', parent['id'], {
            'targets': [{'employee_id': emp(l)['employee_id']}
                        for l in ('xgscwb001', 'xgscwb002', 'xgscwb003')]})
        n_after = db.execute(select(func.count()).select_from(DispatchTask)).scalar_one()
        check('F5', '重复裂变 → created=0、reused=3，任务总数不变',
              not r2['created'] and len(r2['reused']) == 3 and n_before == n_after,
              {'created': len(r2['created']), 'reused': len(r2['reused']),
               'before/after': (n_before, n_after)})

        # ── F6 市级直接派给所长（跨级）→ LEVEL_SKIP ────────────────
        ok6, d6 = expect_error(lambda: S.fanout(db, 'sgscwb', parent['id'], {
            'targets': [{'employee_id': emp('sngdssz')['employee_id']}]}), 'LEVEL_SKIP')
        check('F6', '市级裂给所长（跨级）→ LEVEL_SKIP', ok6, d6)

        # ── F7 某目标没有承办人 → 进 skipped，其余照常 ──────────────
        r7 = S.fanout(db, 'sgscwb', parent['id'], {
            'targets': [{'employee_id': 99999999},
                        {'employee_id': emp('xgscwb002')['employee_id']}]})
        check('F7', '坏目标进 skipped，好目标照常返回（不整批回滚）',
              len(r7['skipped']) == 1 and '99999999' in json.dumps(r7['skipped'])
              and len(r7['reused']) == 1,
              {'skipped': r7['skipped'], 'reused': len(r7['reused'])})

        # ── F8 目标已自行开单 → adopted，不新建、不重挂 ─────────────
        own = S.create(db, 'xgscwb', counties['xgscwb'].id, {
            'assignees': [{'employee_id': emp('sngdssz')['employee_id'], 'part': 'owner'}],
            'title': '曾都区自己开的单', 'idempotency_key': 'adopt-1'})
        own_row = db.get(DispatchTask, own['id'])
        r8 = S.fanout(db, 'sgscwb', parent['id'], {
            'targets': [{'employee_id': emp('xgscwb')['employee_id']}]})
        db.refresh(own_row)
        ev = db.execute(select(DispatchTaskEvent).where(
            DispatchTaskEvent.task_id == parent_row.id,
            DispatchTaskEvent.action == 'fanout')).scalars().all()
        check('F8', '该县已自行开单 → adopted，子单没被重挂到父单下',
              len(r8['adopted']) == 1 and not r8['created']
              and r8['adopted'][0]['task_id'] == own_row.id
              and own_row.parent_task_id is None
              and any(own_row.id in (e.payload.get('adopted') or []) for e in ev),
              {'adopted': r8['adopted'], 'parent_task_id': own_row.parent_task_id})

        # ── F9 所级任务没有下级 → NO_TARGET ────────────────────────
        sz_scope = scope_of('sngdssz')
        st_alert = make_alert(
            db, scope='station', scope_key='42', scope_name=sz_scope['scope'], role='sz',
            hint={'kind': 'budget_rate', 'station_ids': sz_scope['station_ids'], 'target': 0.6},
            title='两水所预算执行进度偏慢', dedupe='fanout|station|42')
        db.commit()
        st_task = S.create(db, 'sngdssz', st_alert.id, {
            'assignees': [{'employee_id': emp('sngdssz')['employee_id'], 'part': 'owner'}],
            'title': '两水所：月底前把预算花完', 'idempotency_key': 'st-1'})
        ok9, d9 = expect_error(lambda: S.fanout(db, 'sngdssz', st_task['id'], {}), 'NO_TARGET')
        check('F9', '所级（最下一级）再裂 → NO_TARGET', ok9, d9)

        # ── F10 / F11 参数与权限 ──────────────────────────────────
        ok10, d10 = expect_error(lambda: S.fanout(db, 'sgscwb', parent['id'],
                                                 {'mode': 'multi'}), 'INVALID_MODE')
        check('F10', '瞎填 mode → INVALID_MODE', ok10, d10)
        ok11, d11 = expect_error(lambda: S.fanout(db, 'sz002', parent['id'], {}),
                                 'NOT_PARTICIPANT')
        check('F11', '不相干的人裂变别人的单 → NOT_PARTICIPANT', ok11, d11)

        # ── F12 mode=joint：1 条任务、多个主责、核销范围=父的整范围 ──
        county11 = db.get(DispatchTask, ids[0])
        c11_scope = scope_of('xgscwb001')
        r12 = S.fanout(db, 'xgscwb001', county11.id, {'mode': 'joint'})
        j12 = db.get(DispatchTask, r12['created'][0]['task_id']) if r12['created'] else None
        j_owners = db.execute(select(DispatchTaskAssignee).where(
            DispatchTaskAssignee.task_id == (j12.id if j12 else 0),
            DispatchTaskAssignee.part == 'owner')).scalars().all()
        check('F12', 'joint → 只有 1 条任务、多个主责、verify_rule 为父的整个范围',
              bool(r12['created']) and len(r12['created']) == 1 and len(j_owners) > 1
              and (j12.verify_rule or {}).get('station_ids') == c11_scope['station_ids']
              and j12.scope_type == 'county',
              {'created': len(r12['created']), 'owners': len(j_owners),
               'rule': (j12.verify_rule or {}).get('kind') if j12 else None})

        # ── F13 留痕：父 fanout 事件 + 子 create 事件 + 双方通知 ─────
        ev1 = db.execute(select(DispatchTaskEvent).where(
            DispatchTaskEvent.task_id == parent_row.id,
            DispatchTaskEvent.action == 'fanout')).scalars().all()
        ev_child = db.execute(select(DispatchTaskEvent).where(
            DispatchTaskEvent.task_id == ids[0],
            DispatchTaskEvent.action == 'create')).scalars().all()
        nt = db.execute(select(DispatchNotice).where(
            DispatchNotice.task_id.in_(ids))).scalars().all()
        summary = db.execute(select(DispatchNotice).where(
            DispatchNotice.task_id == parent_row.id,
            DispatchNotice.notice_type == 'fanout')).scalars().all()
        check('F13', '父有 fanout 事件、子有 create 事件(source=fanout)、承办人与下发人都有通知',
              bool(ev1) and ev1[0].payload.get('created') is not None
              and bool(ev_child) and ev_child[0].payload.get('source') == 'fanout'
              and ev_child[0].payload.get('parent_task_id') == parent_row.id
              and any(n.notice_type == 'assign' for n in nt) and bool(summary),
              {'fanout_event': len(ev1), 'child_event': len(ev_child),
               'notice': len(nt), 'summary': len(summary)})

        # ── F18 adopted 进来的那棵树也算子任务（汇总要看它）──────────
        kids_city = S._children_closed(db, parent_row)
        check('F18', '被认领（adopted）的树也算父任务的子任务',
              kids_city['has_children'] and not kids_city['closed']
              and own_row.task_no in kids_city['waiting'],
              {'waiting': kids_city['waiting'], 'adopted': own_row.task_no})

        # ══════════ 第 3 组 双条件闭环（判据 9/10/11）══════════
        def mk_parent(login, alert_id, rule):
            """自造一个"本级"任务，核销规则自己定（用来分别卡两个条件）。"""
            t = S.create(db, login, alert_id, {
                'assignees': [{'employee_id': emp(login)['employee_id'], 'part': 'owner'}],
                'title': '%s 的本级单' % login, 'idempotency_key': 'p-%s' % login})
            row = db.get(DispatchTask, t['id'])
            row.verify_rule = rule
            row.verifier_type = 'auto'
            db.commit()
            return row

        def sz_of(login):
            sid = scope_of(login)['station_ids'][0]
            emps = S._emp_by_role('sz', S._station(sid)['dept_id'])
            assert emps, '该所没有所长账号'
            return emps[0]

        def close_child(child_id, owner, creator):
            S.accept(db, owner, child_id)
            S.reply(db, owner, child_id, '已办')
            S.close(db, creator, child_id, '办完')

        fail_rule = {'kind': 'budget_rate', 'station_ids': scope_of('xgscwb003')['station_ids'],
                     'op': '>', 'target': 2.0, 'close_when_met': True}
        ok_rule = {'kind': 'budget_rate', 'station_ids': scope_of('xgscwb002')['station_ids'],
                   'op': '>=', 'target': 0.0, 'close_when_met': True}
        p_fail = mk_parent('xgscwb003', counties['xgscwb003'].id, fail_rule)   # 广水：数据不达标
        p_ok = mk_parent('xgscwb002', counties['xgscwb002'].id, ok_rule)       # 高新：数据达标

        # ── F15 父数据达标、子任务没闭环 → 父不闭环（pending + 写清缺哪个）──
        sz12 = sz_of('xgscwb002')
        r15 = S.fanout(db, 'xgscwb002', p_ok.id, {'targets': [{'employee_id': sz12['employee_id']}]})
        S.tick(db)
        db.refresh(p_ok)
        chk = db.execute(select(DispatchCheck).where(
            DispatchCheck.task_id == p_ok.id).order_by(DispatchCheck.id.desc()).limit(1)).scalar_one_or_none()
        m = (chk.measured or {}) if chk else {}
        check('F15', '数据达标但子任务没闭环 → 父不闭环，verify_state=pending，measured 点明缺哪个',
              p_ok.status != 'closed' and p_ok.verify_state == 'pending'
              and m.get('data_ok') is True and m.get('children_closed') is False
              and len(m.get('waiting') or []) == 1,
              {'status': p_ok.status, 'verify_state': p_ok.verify_state, 'measured': m})

        # ── F14 子任务全闭环、父数据不达标 → 父不闭环 + 提醒承办人 ──────
        sz13 = sz_of('xgscwb003')
        r14 = S.fanout(db, 'xgscwb003', p_fail.id, {'targets': [{'employee_id': sz13['employee_id']}]})
        close_child(r14['created'][0]['task_id'], sz13['login_name'], 'xgscwb003')
        S.tick(db)
        db.refresh(p_fail)
        note = db.execute(select(DispatchNotice).where(
            DispatchNotice.task_id == p_fail.id,
            DispatchNotice.notice_type == 'verify')).scalars().all()
        check('F14', '子任务全闭环但本级数据没达标 → 父不闭环，且给承办人发了提醒',
              p_fail.status != 'closed' and bool(note)
              and any('数据还没达标' in (n.title or '') for n in note),
              {'status': p_fail.status, 'notice': [(n.title or '')[:24] for n in note]})
        S.tick(db)      # 再跑一次：同一天不重复吵人
        note2 = db.execute(select(DispatchNotice).where(
            DispatchNotice.task_id == p_fail.id,
            DispatchNotice.notice_type == 'verify')).scalars().all()
        check('F14.1', '同一天重复 tick 不重复发提醒', len(note2) == len(note),
              {'first': len(note), 'after': len(note2)})

        # ── F16 两个条件都满足 → 自动闭环 auto_verified ────────────────
        p_fail.verify_rule = dict(fail_rule, op='>=', target=0.0)
        db.commit()
        S.tick(db)
        db.refresh(p_fail)
        check('F16', '子任务全闭环 + 数据达标 → 父自动闭环，close_reason=auto_verified',
              p_fail.status == 'closed' and p_fail.close_reason == 'auto_verified',
              {'status': p_fail.status, 'reason': p_fail.close_reason})

        # ── F17 人工闭环也要过两关；只有管理员能强制并留痕 ─────────────
        S.accept(db, 'xgscwb002', p_ok.id)
        S.reply(db, 'xgscwb002', p_ok.id, '本级报完')
        blocked = [True, '']
        try:
            S.close(db, 'xgscwb002', p_ok.id, '想直接闭环')
            blocked = [False, '竟然闭环了']
        except DispatchError as exc:
            blocked = [exc.code == 'CHILDREN_OPEN', '%s：%s' % (exc.code, exc.message[:60])]
        real_actor = S._actor
        S._actor = lambda acc: dict(real_actor(acc), role_code='admin')
        try:
            S.close(db, 'xgscwb002', p_ok.id, '管理员强制闭环')
        finally:
            S._actor = real_actor
        db.refresh(p_ok)
        check('F17', '人工闭环被子任务拦住（CHILDREN_OPEN）；管理员可强制闭环并留痕为 admin_forced',
              blocked[0] and p_ok.status == 'closed' and p_ok.close_reason == 'admin_forced',
              {'blocked': blocked[1], 'reason': p_ok.close_reason})

        (OUT / 'raw-fanout.json').write_text(
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
