# -*- coding: utf-8 -*-
"""四类预警 + 任务流转 · 端到端验收（服务层）。

覆盖 37 条验收判据里可自动验的部分：
  四类枚举 / 条数下降 / 节点是属性 / 扫描幂等 / 天不重复吵人 /
  预警↔对话复用 / 推荐承办人 / 幂等下发 / 多人主责 / 权限 /
  状态机非法迁移 / 方案与回执 / 自动核销 / 催办与升级幂等 / 统计与健康。

跑法： py -X utf8 server/tests/agent/test_dispatch_flow.py
产出： output/test_results/<日期>-预警任务流转/raw-flow.json
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = Path(HERE).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'server'))
os.environ.setdefault('DISPATCH_AI_ON', '0')

from sqlalchemy import func, select, text  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.dispatch import (  # noqa: E402
    AlertChatLink, AlertItem, DispatchCheck, DispatchNotice, DispatchTask,
    DispatchTaskAssignee, DispatchTaskEvent,
)
from app.secretary import tools_db  # noqa: E402
from app.services import alert_service, dispatch_service as S  # noqa: E402
from app.services.dispatch_service import DispatchError  # noqa: E402

TODAY = '2026-10-19'
OUT = ROOT / 'output' / 'test_results' / ('%s-预警任务流转' % time.strftime('%Y-%m-%d'))
OUT.mkdir(parents=True, exist_ok=True)
RESULTS = []
TB = ['alert_scan_run', 'alert_item', 'dispatch_task', 'dispatch_task_assignee',
      'dispatch_task_event', 'dispatch_task_plan', 'dispatch_check', 'dispatch_notice',
      'dispatch_attachment', 'alert_chat_link']


def check(no, name, ok, detail=''):
    RESULTS.append({'no': no, 'name': name, 'ok': bool(ok), 'detail': str(detail)[:1000]})
    print('%s %2s %s%s' % ('PASS' if ok else 'FAIL', no, name,
                           ('  | ' + str(detail)[:180]) if detail else ''))


def reset(db):
    assert '127.0.0.1' in settings.database_url and '5433' in settings.database_url, \
        '只允许在本地 5433 测试库跑'
    db.execute(text('TRUNCATE %s RESTART IDENTITY CASCADE' % ', '.join(TB)))
    db.execute(text("DELETE FROM t_chat_message WHERE chat_session_id LIKE 'alert-%%'"))
    db.execute(text("DELETE FROM t_chat_session WHERE session_id LIKE 'alert-%%'"))
    db.commit()


def q(sql, args=None, limit=200):
    r = tools_db._exec(sql, args, limit_rows=limit)
    return [{(k or '').lower(): v for k, v in row.items()} for row in (r.get('rows') or [])]


def main():
    db = SessionLocal()
    try:
        reset(db)

        # ── 1) 扫描：四类 + 条数下降 ─────────────────────────────
        r1 = alert_service.scan(db, accounts=['sngdssz', 'xgscwb', 'sgscwb', 'sgsld'],
                                cadence='all', today=TODAY)
        check(1, '扫描成功且无判据异常', r1['found'] > 0 and not r1['errors'],
              'found=%s new=%s resolved=%s errors=%s'
              % (r1['found'], r1['new'], r1['resolved'], r1['errors'][:2]))

        lst = alert_service.list_alerts(db, 'sngdssz', today=TODAY)
        items = lst['items']
        check(2, '两水所长：旧口径 5 条 → 新口径 2 条', lst['total'] == 2,
              [a['title'] for a in items])
        cats = {a['category'] for a in items}
        check(3, '只出现四类枚举', cats <= {'budget', 'project', 'review', 'metric'}, cats)
        subtypes = {a['subtype'] for a in items}
        check(4, '审批不再按节点拆类型（只有 approval_stay）',
              'project.approval_stay' in subtypes and
              not any(s.startswith('node') for s in subtypes), subtypes)
        stay = [a for a in items if a['subtype'] == 'project.approval_stay'][0]
        check(5, '节点作为属性：data.by_node 有分布',
              isinstance((stay['data'] or {}).get('by_node'), dict)
              and stay['data']['by_node'], list((stay['data'] or {}).get('by_node') or {})[:5])

        # ── 2) 权限：范围与角色 ────────────────────────────────
        other = alert_service.list_alerts(db, 'xgscwb', today=TODAY)
        cross = [a for a in other['items'] if a['scope_type'] == 'station' and a['scope_key'] == '42']
        check(6, '县财务看不到两水所的所级预警', not cross, 'cross=%s' % len(cross))
        city = alert_service.list_alerts(db, 'sgscwb', today=TODAY)
        check(7, '市财务能看到全市口径', city['total'] > 0, 'total=%s' % city['total'])
        bad = alert_service.list_alerts(db, 'no_such_user', today=TODAY)
        check(8, '坏账号关闭闸门（unknown + 空）',
              bad['status'] == 'unknown' and bad['total'] == 0, bad.get('error'))

        # ── 3) 扫描幂等 + 不重复吵人 ──────────────────────────
        aid = items[0]['id']
        first_seen = items[0]['first_seen']
        r2 = alert_service.scan(db, accounts=['sngdssz'], cadence='all', today=TODAY)
        again = alert_service.get_alert(db, 'sngdssz', aid)
        check(9, '重复扫描不新增行（幂等）', r2['new'] == 0 and again['id'] == aid,
              'new=%s' % r2['new'])
        check(10, '同一条预警不重复吵人（first_seen 不变、seen_times 递增）',
              again['first_seen'] == first_seen and again['seen_times'] >= 2,
              'seen=%s' % again['seen_times'])

        # ── 4) 预警 ↔ 对话 ────────────────────────────────────
        c1 = alert_service.open_chat(db, 'sngdssz', aid, TODAY)
        c2 = alert_service.open_chat(db, 'sngdssz', aid, TODAY)
        check(11, '点预警 → 建会话 + 卡片 + 首段话',
              c1['chat_store'] == 'ok' and len(c1['messages']) == 2
              and c1['messages'][0]['meta'] and c1['messages'][0]['meta'].get('kind') == 'alert_card',
              'session=%s msgs=%s' % (c1['session_id'], len(c1['messages'])))
        check(12, '重复点开复用同一个会话', c2['session_id'] == c1['session_id'], c2['session_id'])
        check(13, '首段话带 3 条建议且每条有依据',
              len(c1['advice']) == 3 and all(a.get('evidence') for a in c1['advice']),
              [a['action'][:24] for a in c1['advice']])

        # ── 5) 推荐承办人 ─────────────────────────────────────
        pv = S.preview(db, 'sngdssz', aid)
        cands = pv['recommend']['candidates']
        check(14, '下发确认页：推荐到本所所长',
              pv['recommend']['role'] == 'sz' and any(c['login_name'] == 'sngdssz' for c in cands),
              [(c['login_name'], c['name']) for c in cands])

        # ── 6) 下发：多人 + 幂等 ──────────────────────────────
        cto = q("SELECT e.employee_id, e.login_name, e.actual_name FROM t_employee e "
                "JOIN t_role_employee re ON re.employee_id=e.employee_id "
                "JOIN t_role r ON r.role_id=re.role_id "
                "WHERE r.role_code='cto' AND e.department_id=10 LIMIT 1")[0]
        body = {
            'assignees': [
                {'employee_id': cands[0]['employee_id'], 'part': 'owner'},
                {'employee_id': int(cto['employee_id']), 'part': 'collaborator'},
            ],
            'title': '两水供电所预算执行整改',
            'requirement': '11-19 前把执行率提到 80%；3 天内交方案',
            'reply_due_at': '2026-10-25T18:00:00+08:00',
            'due_at': '2026-11-19T18:00:00+08:00',
            'require_plan': 1, 'verifier_type': 'auto',
            'verify_rule': {'kind': 'budget_rate', 'station_ids': [42], 'year': 2026,
                            'op': '>=', 'target': 0.0, 'close_when_met': True},
            'idempotency_key': 'flow-1',
        }
        t1 = S.create(db, 'sngdssz', aid, body)
        t1b = S.create(db, 'sngdssz', aid, body)
        parts = {a['part'] for a in t1['assignees']}
        check(15, '幂等下发：同一 idempotency_key 返回同一任务',
              t1['id'] == t1b['id'] and t1['status'] == 'open', t1['task_no'])
        check(16, '多人：主责 + 协办都落库', parts == {'owner', 'collaborator'}, parts)
        n_assign = db.execute(select(func.count()).select_from(DispatchNotice).where(
            DispatchNotice.task_id == t1['id'], DispatchNotice.notice_type == 'assign')).scalar_one()
        check(17, '下发通知按人发（2 人 2 条）', int(n_assign) == 2, 'notices=%s' % n_assign)

        # ── 7) 权限 ───────────────────────────────────────────
        for who, code in (('xgscwb', 'NOT_PARTICIPANT'),):
            try:
                S.task_detail(db, who, t1['id'])
                check(18, '非参与人看不到任务', False, '竟然看到了')
            except DispatchError as exc:
                check(18, '非参与人看不到任务', exc.code == code, exc.code)
        try:
            S.reply(db, cto['login_name'], t1['id'], '协办插嘴')
            check(19, '协办不能代替主责回执', False)
        except DispatchError as exc:
            check(19, '协办不能代替主责回执', exc.code == 'NOT_PARTICIPANT', exc.code)

        # ── 8) 状态机 ─────────────────────────────────────────
        try:
            S.close(db, 'sngdssz', t1['id'], '提前核销')
            check(20, '非法迁移被拒（未回执就核销）', False)
        except DispatchError as exc:
            check(20, '非法迁移被拒（未回执就核销）', exc.code == 'INVALID_TRANSITION' and exc.status == 409,
                  exc.code)
        t1 = S.accept(db, 'sngdssz', t1['id'])
        try:
            S.accept(db, 'sngdssz', t1['id'])
            check(21, '重复接单被拒', False)
        except DispatchError as exc:
            check(21, '重复接单被拒', exc.code == 'INVALID_TRANSITION', exc.code)

        # ── 9) 方案 → 回执 → 核销 ────────────────────────────
        t1 = S.submit_plan(db, 'sngdssz', t1['id'], '1. 补储备 2. 走审批 3. 周跟踪')
        pid = t1['plans'][-1]['id']
        t1 = S.accept_plan(db, 'sngdssz', t1['id'], pid, True)
        t1 = S.reply(db, 'sngdssz', t1['id'], '已提交回执，执行率待观察')
        t1 = S.close(db, 'sngdssz', t1['id'], '核销通过')
        acts = [e['action'] for e in t1['events']]
        check(22, '主流程事件齐全：确认→接单→方案→回执→核销',
              all(a in acts for a in ('confirm', 'accept', 'plan_submit', 'plan_accept',
                                      'reply', 'close')), acts)
        check(23, '闭环状态与原因正确',
              t1['status'] == 'closed' and t1['close_reason'] == 'human_confirmed',
              '%s/%s' % (t1['status'], t1['close_reason']))

        # ── 10) 自动核销（可见指标） ──────────────────────────
        t2 = S.create(db, 'sngdssz', aid, {
            'assignees': [{'employee_id': cands[0]['employee_id'], 'part': 'owner'}],
            'title': '自动核销用例', 'requirement': '达标即闭环',
            'verifier_type': 'auto',
            'verify_rule': {'kind': 'budget_rate', 'station_ids': [42], 'year': 2026,
                            'op': '>=', 'target': 0.0, 'close_when_met': True},
            'idempotency_key': 'flow-auto'})
        tk = S.tick(db)
        t2d = S.task_detail(db, 'sngdssz', t2['id'])
        check(24, '自动核销：tick 后 closed/auto_verified',
              t2d['status'] == 'closed' and t2d['close_reason'] == 'auto_verified',
              '%s/%s' % (t2d['status'], t2d['close_reason']))
        check(25, '自动核销留下实测值 dispatch_check',
              bool(t2d['checks']) and (t2d['checks'][0]['measured'] or {}).get('rate') is not None,
              t2d['checks'][:1])

        # ── 11) 催办 + 升级（幂等） ───────────────────────────
        past = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        t3 = S.create(db, 'sngdssz', aid, {
            'assignees': [{'employee_id': cands[0]['employee_id'], 'part': 'owner'}],
            'title': '超时用例', 'requirement': '3 天回执',
            'reply_due_at': past, 'due_at': past,
            'verifier_type': 'human',
            'idempotency_key': 'flow-late'})
        S.tick(db)
        n1 = db.execute(select(func.count()).select_from(DispatchNotice)).scalar_one()
        t3d = S.task_detail(db, 'sngdssz', t3['id'])
        lvl1 = t3d['escalate_level']
        S.tick(db)
        n2 = db.execute(select(func.count()).select_from(DispatchNotice)).scalar_one()
        check(26, '超时催办 + 升级各触发一次', lvl1 >= 1, 'level=%s notices=%s' % (lvl1, n1))
        check(27, '重复 tick 不重复发通知', n1 == n2, '%s -> %s' % (n1, n2))

        # ── 12) 子任务链 ──────────────────────────────────────
        t4 = S.subtask(db, 'sngdssz', t1['id'], {
            'title': '子任务：本周补 2 个储备项目', 'requirement': '周五前',
            'assignees': [{'employee_id': int(cto['employee_id']), 'part': 'owner'}],
            'verifier_type': 'human'})
        check(28, '继续下发：父子任务链成立',
              t4['parent_task_id'] == t1['id'] and t4['root_task_id'] == t1['root_task_id'],
              '%s -> %s' % (t4['parent_task_id'], t4['root_task_id']))

        # ── 13) 统计 / 健康 ───────────────────────────────────
        st = S.stats(db)
        check(29, '统计有闭环率与平均处置时长',
              st['total'] >= 4 and st['closed'] >= 2 and st['closed_rate'] > 0,
              {k: st[k] for k in ('total', 'closed', 'closed_rate', 'avg_close_hours')})
        h = db.execute(text('SELECT COUNT(*) FROM alert_item')).scalar_one()
        check(30, '预警实例已落库', int(h) >= 2, 'alerts=%s' % h)

        # ── 14) 路由已注册 ────────────────────────────────────
        import app.main as M  # noqa: E402
        try:
            paths = set(M.app.openapi()['paths'])
        except Exception:  # noqa: BLE001 - 老版本 FastAPI 无 openapi 缓存
            paths = {getattr(r, 'path', '') for r in M.app.routes}
        need = ['/api/alerts', '/api/alerts/scan', '/api/alerts/{alert_id}',
                '/api/alerts/{alert_id}/chat', '/api/alerts/{alert_id}/dispatch',
                '/api/tasks', '/api/tasks/{task_id}/accept', '/api/dispatch/tick']
        miss = [p for p in need if p not in paths]
        check(31, '新接口全部注册（%d 条）' % len(need), not miss, '缺：%s' % miss)
    finally:
        db.close()

    (OUT / 'raw-flow.json').write_text(json.dumps(RESULTS, ensure_ascii=False, indent=1),
                                       encoding='utf-8')
    bad = [r for r in RESULTS if not r['ok']]
    print('=' * 90)
    print('%d/%d PASS' % (len(RESULTS) - len(bad), len(RESULTS)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
