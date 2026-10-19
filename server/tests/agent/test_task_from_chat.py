# -*- coding: utf-8 -*-
"""对话建任务验收：意图识别 → 拟草稿 → 人工确认 → 建任务；确认前不得有任务。

跑法： py -X utf8 server/tests/agent/test_task_from_chat.py
产出： output/test_results/<日期>-预警任务流转/raw-from-chat.json
"""
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = Path(HERE).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'server'))
os.environ.setdefault('DISPATCH_AI_ON', '0')

from sqlalchemy import func, select, text  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.dispatch import AlertItem, DispatchNotice, DispatchTask  # noqa: E402
from app.services import alert_service  # noqa: E402
from app.services import dispatch_service as S  # noqa: E402
from app.services.dispatch_service import DispatchError  # noqa: E402

TODAY = '2026-10-19'
OUT = ROOT / 'output' / 'test_results' / ('%s-预警任务流转' % time.strftime('%Y-%m-%d'))
OUT.mkdir(parents=True, exist_ok=True)
RESULTS = []
TB = ['alert_scan_run', 'alert_item', 'dispatch_task', 'dispatch_task_assignee',
      'dispatch_task_event', 'dispatch_task_plan', 'dispatch_check', 'dispatch_notice',
      'dispatch_attachment', 'alert_chat_link']


def check(no, name, ok, detail=''):
    RESULTS.append({'no': no, 'name': name, 'ok': bool(ok), 'detail': str(detail)[:800]})
    print('%s %2s %s%s' % ('PASS' if ok else 'FAIL', no, name,
                           ('  | ' + str(detail)[:180]) if detail else ''))


def reset(db):
    assert '127.0.0.1' in settings.database_url and '5433' in settings.database_url
    db.execute(text('TRUNCATE %s RESTART IDENTITY CASCADE' % ', '.join(TB)))
    db.execute(text("DELETE FROM t_chat_message WHERE chat_session_id LIKE 'chat-%'"))
    db.execute(text("DELETE FROM t_chat_session WHERE session_id LIKE 'chat-%'"))
    db.commit()


def main():
    db = SessionLocal()
    try:
        reset(db)

        # 1) 普通提问不触发
        r = S.propose_from_chat(db, 'sngdssz', 'chat-t1', '今年预算执行率是多少？', today=TODAY)
        check(1, '提问不触发建任务', r['intent'] is False, r.get('reason'))

        # 2) 严格模式：有动作没时限不打扰
        r = S.propose_from_chat(db, 'sngdssz', 'chat-t2', '让两水所把预算花完',
                                today=TODAY, strict=True)
        check(2, '严格模式：没时限不拟稿', r['intent'] is False, r.get('reason'))

        # 3) 带时限 → 拟稿（两水所）
        r = S.propose_from_chat(db, 'sngdssz', 'chat-t3',
                                '让两水供电所3天内提交整改方案并回执，月底前把执行率提到80%',
                                today=TODAY)
        draft = r.get('draft') or {}
        check(3, '识别意图并拟草稿', r['intent'] is True and r.get('status') == 'draft',
              {'task_id': r.get('task_id'), 'title': draft.get('title')})
        check(4, '范围/承办人识别（两水所→所长）',
              draft.get('scope_name') == '两水供电所'
              and any(a['login_name'] == 'sngdssz' for a in draft.get('assignees') or []),
              [(a['login_name'], a['name']) for a in draft.get('assignees') or []])
        due = draft.get('reply_due_at') or ''
        check(5, '时限解析（3 天内）',
              '2026-10-21' <= due[:10] <= '2026-10-23' and draft.get('require_plan') == 1, due)
        check(6, '确认消息已写进会话（AI 发言等人确认）',
              bool((r.get('message') or '')) and '确认' in (r.get('message') or ''),
              (r.get('message') or '')[:60].replace('\n', ' '))

        # 4) 确认前：不是正式任务
        mine = S.list_tasks(db, 'sngdssz')
        check(7, '确认前不出现在我的任务', mine['total'] == 0, mine['total'])
        pend = S.pending_draft(db, 'sngdssz', 'chat-t3')
        check(8, '可按会话查待确认草稿', pend['has_draft'] is True and pend['task_id'] == r['task_id'],
              pend.get('task_id'))

        # 5) 非确认词 → 拒绝
        try:
            S.confirm_draft(db, 'sngdssz', session_id='chat-t3', message='我再想想')
            check(9, '非确认词被拒', False)
        except DispatchError as exc:
            check(9, '非确认词被拒（NEED_CONFIRM）', exc.code == 'NEED_CONFIRM', exc.code)

        # 6) 「确认」→ 正式任务 + 通知
        t = S.confirm_draft(db, 'sngdssz', session_id='chat-t3', message='确认')
        check(10, '回复「确认」→ 正式任务', t['status'] == 'open' and t['task_no'],
              {'task_no': t['task_no'], 'status': t['status']})
        n_assign = db.execute(select(func.count()).select_from(DispatchNotice).where(
            DispatchNotice.task_id == t['id'], DispatchNotice.notice_type == 'assign')).scalar_one()
        check(11, '承办人收到下发通知', int(n_assign) >= 1, n_assign)
        check(12, '事件链：create → confirm',
              [e['action'] for e in t['events']][:2] == ['create', 'confirm'],
              [e['action'] for e in t['events']])
        try:
            S.confirm_draft(db, 'sngdssz', task_id=t['id'], message='确认')
            check(13, '重复确认被拒', False)
        except DispatchError as exc:
            check(13, '重复确认被拒', exc.code == 'INVALID_TRANSITION', exc.code)

        # 7) 「取消」→ 草稿作废
        r2 = S.propose_from_chat(db, 'sngdssz', 'chat-t4',
                                 '安排随县供电公司本周内交一份整改报告', today=TODAY)
        t2 = S.confirm_draft(db, 'sngdssz', session_id='chat-t4', message='取消')
        check(14, '回复「取消」→ 草稿作废',
              t2['status'] == 'cancelled' and t2.get('cancelled') is True, t2['status'])

        # 8) 范围识别：全县 / 全市
        r3 = S.propose_from_chat(db, 'sgscwb', 'chat-t5',
                                 '让全市5天内提交预算整改报告', today=TODAY)
        check(15, '全市范围识别', (r3.get('draft') or {}).get('scope_name') == '全市',
              (r3.get('draft') or {}).get('scope_name'))

        # 9) 权限：别人不能确认我的草稿
        try:
            S.confirm_draft(db, 'xgscwb', task_id=r3['task_id'], message='确认')
            check(16, '别人不能确认我的草稿', False)
        except DispatchError as exc:
            check(16, '别人不能确认我的草稿', exc.code == 'NOT_PARTICIPANT', exc.code)

        # 10) 从预警对话发起：确认后预警变 dispatched
        alert_service.scan(db, accounts=['sngdssz'], cadence='daily', today=TODAY)
        al = alert_service.list_alerts(db, 'sngdssz', today=TODAY)['items'][0]
        r4 = S.propose_from_chat(db, 'sngdssz', 'chat-t6',
                                 '帮我发起：3天内交整改方案', alert_id=al['id'], today=TODAY)
        t4 = S.confirm_draft(db, 'sngdssz', session_id='chat-t6', message='确认')
        a_after = db.get(AlertItem, al['id'])
        check(17, '预警对话发起：确认后预警置为已下发',
              r4['intent'] and a_after.status == 'dispatched' and a_after.task_id == t4['id'],
              (a_after.status, a_after.task_id))

        # 11) 路由已注册
        import app.main as M  # noqa: E402
        paths = set(M.app.openapi()['paths'])
        need = ['/api/tasks/draft', '/api/tasks/draft/confirm', '/api/tasks/draft/pending']
        check(18, '3 个新接口已注册', all(p in paths for p in need),
              [p for p in need if p not in paths])

        # 12) /api/ask 自动挂草稿（不调模型：打桩）
        import app.api.v1.agent as agent_api  # noqa: E402
        from fastapi.testclient import TestClient  # noqa: E402
        from app.core.deps import CurrentActor, get_current_actor  # noqa: E402

        def fake_ask(question, **kw):
            return {'answer': '（打桩）已看完数据', 'session_id': kw.get('session_id'),
                    'trace': [], 'elapsed_ms': 1, 'model': 'stub'}
        orig = agent_api.agent_mod.ask
        agent_api.agent_mod.ask = fake_ask
        c = TestClient(M.app)
        M.app.dependency_overrides[get_current_actor] = lambda: CurrentActor(
            type='user', user_id=1, username='sngdssz', display_name='两水所',
            role_codes=['user'], permissions=set())
        try:
            r1 = c.post('/api/ask', json={'question': '让两水所3天内提交整改方案',
                                          'session_id': 'chat-api-1', 'today': TODAY})
            j1 = r1.json()
            check(19, '/api/ask 自动返回 task_draft（带时限）',
                  r1.status_code == 200 and (j1.get('task_draft') or {}).get('intent') is True,
                  (j1.get('task_draft') or {}).get('task_id'))
            r2 = c.post('/api/ask', json={'question': '今年预算执行率是多少？',
                                          'session_id': 'chat-api-2', 'today': TODAY})
            check(20, '/api/ask 普通提问不挂草稿',
                  r2.status_code == 200 and not (r2.json().get('task_draft')),
                  r2.json().get('task_draft'))
            r3 = c.post('/api/tasks/draft/confirm',
                        json={'session_id': 'chat-api-1', 'message': '确认'})
            check(21, 'HTTP 确认草稿 → open',
                  r3.status_code == 200 and r3.json().get('status') == 'open',
                  r3.status_code)
        finally:
            agent_api.agent_mod.ask = orig
            M.app.dependency_overrides.clear()
    finally:
        db.close()

    (OUT / 'raw-from-chat.json').write_text(json.dumps(RESULTS, ensure_ascii=False, indent=1),
                                            encoding='utf-8')
    bad = [r for r in RESULTS if not r['ok']]
    print('=' * 90)
    print('%d/%d PASS' % (len(RESULTS) - len(bad), len(RESULTS)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
