# -*- coding: utf-8 -*-
"""对话建任务验收（v2，语义判断版）。

核心：**不用关键词判意图**。四个象限都用打桩模型覆盖：
  ① 有"任务/完成"等词但不是任务（问进度）         → 不拟稿
  ② 没有这些词但是在让人办事（"得有人盯到月底"）   → 拟稿
  ③ 有这些词且是任务                               → 拟稿
  ④ 没有词也不是任务（查数）                       → 不拟稿
另测：置信度阈值、模型不可用的保守降级、强制"把对话变成任务"、人工确认/取消/权限/预警上下文。

跑法： py -X utf8 server/tests/agent/test_task_from_chat.py
"""
import json
import os
import sys
import time
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = Path(HERE).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'server'))
os.environ.setdefault('DISPATCH_AI_ON', '0')

from sqlalchemy import func, select, text  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.dispatch import AlertItem, DispatchNotice  # noqa: E402
from app.services import alert_service, dispatch_ai  # noqa: E402
from app.services import dispatch_service as S  # noqa: E402
from app.services.dispatch_service import DispatchError  # noqa: E402

TODAY = '2026-10-19'
OUT = ROOT / 'output' / 'test_results' / ('%s-预警任务流转' % time.strftime('%Y-%m-%d'))
OUT.mkdir(parents=True, exist_ok=True)
RESULTS = []
TB = ['alert_scan_run', 'alert_item', 'dispatch_task', 'dispatch_task_assignee',
      'dispatch_task_event', 'dispatch_task_plan', 'dispatch_check', 'dispatch_notice',
      'dispatch_attachment', 'alert_chat_link']
_ORIG = dispatch_ai.classify_task_intent


def check(no, name, ok, detail=''):
    RESULTS.append({'no': no, 'name': name, 'ok': bool(ok), 'detail': str(detail)[:800]})
    print('%s %2s %s%s' % ('PASS' if ok else 'FAIL', no, name,
                           ('  | ' + str(detail)[:180]) if detail else ''))


def stub(is_task, conf=0.9, reason='stub', scope='', deadline=''):
    dispatch_ai.classify_task_intent = lambda q, h=None, timeout=12: {
        'available': True, 'is_task': is_task, 'confidence': conf, 'reason': reason,
        'scope': scope, 'action': '', 'deadline': deadline}


def stub_unavailable(reason='模型不可用'):
    dispatch_ai.classify_task_intent = lambda q, h=None, timeout=12: {
        'available': False, 'reason': reason}


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

        # ── 四象限（语义判断，不看关键词）────────────────────
        stub(False, 0.9, '是在问进度，不是派事')
        r = S.propose_from_chat(db, 'sngdssz', 'chat-q1',
                                '两水所的任务完成得怎么样了？', today=TODAY,
                                mode='explicit')
        check(1, '象限①：有"任务/完成"但只是问进度 → 不拟稿', r['intent'] is False,
              r.get('reason'))

        stub(True, 0.86, '隐含要求盯到月底')
        r = S.propose_from_chat(db, 'sngdssz', 'chat-q2',
                                '供电所这个执行率，得有人盯到月底', today=TODAY,
                                mode='explicit')
        check(2, '象限②：没有"任务/下发"词，但在让人办事 → 拟稿', r['intent'] is True,
              {'title': (r.get('draft') or {}).get('title'), 'conf': r['classifier'].get('confidence')})

        stub(True, 0.92, '明确要求3天内交方案')
        r = S.propose_from_chat(db, 'sngdssz', 'chat-q3',
                                '让两水供电所3天内提交整改方案并回执', today=TODAY,
                                mode='explicit')
        check(3, '象限③：有词且是任务 → 拟稿', r['intent'] is True,
              {'task_id': r.get('task_id'), 'title': (r.get('draft') or {}).get('title')})
        draft = r.get('draft') or {}
        check(4, '承办人按范围推荐（两水所→所长）',
              any(a['login_name'] == 'sngdssz' for a in draft.get('assignees') or []),
              [(a['login_name'], a['name']) for a in draft.get('assignees') or []])
        check(5, '时限解析（3 天内）', (draft.get('reply_due_at') or '')[:10] == '2026-10-22',
              draft.get('reply_due_at'))

        stub(False, 0.95, '查数')
        r = S.propose_from_chat(db, 'sngdssz', 'chat-q4',
                                '今年预算执行率是多少？', today=TODAY, mode='explicit')
        check(6, '象限④：没有词也不是任务 → 不拟稿', r['intent'] is False, r.get('reason'))

        # ── 置信度阈值：自动模式更严 ─────────────────────────
        stub(True, 0.5, '不太确定')
        auto = S.propose_from_chat(db, 'sngdssz', 'chat-th1',
                                   '要不让他们月底前把这事办了', today=TODAY, mode='auto')
        check(7, '自动模式：0.5 置信度不打扰', auto['intent'] is False, auto.get('reason'))
        exp = S.propose_from_chat(db, 'sngdssz', 'chat-th2',
                                  '要不让他们月底前把这事办了', today=TODAY, mode='explicit')
        check(8, '主动入口：0.5 置信度拟稿（用户本来就想建）', exp['intent'] is True,
              exp.get('task_id'))

        # ── 模型不可用：保守降级（只认明确指令）──────────────
        stub_unavailable()
        a = S.propose_from_chat(db, 'sngdssz', 'chat-f1', '这个执行率得盯一下', today=TODAY)
        b = S.propose_from_chat(db, 'sngdssz', 'chat-f2', '帮我建任务：盯执行率', today=TODAY)
        check(9, '模型不可用：模糊话不拟稿', a['intent'] is False, a.get('reason'))
        check(10, '模型不可用：明确指令仍可拟稿', b['intent'] is True, b.get('task_id'))

        # ── 强制"把这段对话变成任务"（不判意图）───────────────
        alert_service.append_chat_message(db, 'chat-force', 'sngdssz', 1,
                                          '今年这钱怕是花不完了', title='测试会话')
        stub(False, 0.99, '不是任务')
        f = S.draft_from_conversation(db, 'sngdssz', 'chat-force', today=TODAY,
                                      instruction='让两水所月底前把预算花掉，3天内给方案')
        check(11, '强制模式：不看模型结论，直接拟稿', f['intent'] is True,
              {'task_id': f.get('task_id'), 'title': (f.get('draft') or {}).get('title')})
        try:
            S.draft_from_conversation(db, 'sngdssz', 'chat-empty', today=TODAY)
            check(12, '空会话不能变任务', False)
        except DispatchError as exc:
            check(12, '空会话不能变任务', exc.code == 'EMPTY_CONVERSATION', exc.code)

        # ── 确认门槛（拟稿 ≠ 任务）───────────────────────────
        stub(True, 0.9)
        r = S.propose_from_chat(db, 'sngdssz', 'chat-c1',
                                '让两水所3天内交整改方案', today=TODAY)
        mine = S.list_tasks(db, 'sngdssz')
        check(13, '确认前不进"我的任务"', mine['total'] == 0, mine['total'])
        try:
            S.confirm_draft(db, 'sngdssz', session_id='chat-c1', message='我再想想')
            check(14, '非确认词被拒', False)
        except DispatchError as exc:
            check(14, '非确认词被拒（NEED_CONFIRM）', exc.code == 'NEED_CONFIRM', exc.code)
        t = S.confirm_draft(db, 'sngdssz', session_id='chat-c1', message='确认')
        check(15, '「确认」→ 正式任务', t['status'] == 'open' and t['task_no'],
              {'task_no': t['task_no'], 'status': t['status']})
        n = db.execute(select(func.count()).select_from(DispatchNotice).where(
            DispatchNotice.task_id == t['id'], DispatchNotice.notice_type == 'assign')).scalar_one()
        check(16, '承办人收到下发通知', int(n) >= 1, n)
        check(17, '事件链 create→confirm',
              [e['action'] for e in t['events']][:2] == ['create', 'confirm'],
              [e['action'] for e in t['events']])
        try:
            S.confirm_draft(db, 'sngdssz', task_id=t['id'], message='确认')
            check(18, '重复确认被拒', False)
        except DispatchError as exc:
            check(18, '重复确认被拒', exc.code == 'INVALID_TRANSITION', exc.code)

        # ── 取消 / 权限 / 预警上下文 ─────────────────────────
        r2 = S.propose_from_chat(db, 'sngdssz', 'chat-c2',
                                 '安排随县供电公司本周内交整改报告', today=TODAY)
        t2 = S.confirm_draft(db, 'sngdssz', session_id='chat-c2', message='取消')
        check(19, '「取消」→ 草稿作废', t2['status'] == 'cancelled' and t2.get('cancelled'),
              t2['status'])

        r3 = S.propose_from_chat(db, 'sgscwb', 'chat-c3',
                                 '让全市5天内提交预算整改报告', today=TODAY)
        try:
            S.confirm_draft(db, 'xgscwb', task_id=r3['task_id'], message='确认')
            check(20, '别人不能确认我的草稿', False)
        except DispatchError as exc:
            check(20, '别人不能确认我的草稿', exc.code == 'NOT_PARTICIPANT', exc.code)

        alert_service.scan(db, accounts=['sngdssz'], cadence='daily', today=TODAY)
        al = alert_service.list_alerts(db, 'sngdssz', today=TODAY)['items'][0]
        r4 = S.propose_from_chat(db, 'sngdssz', 'chat-c4',
                                 '按这条预警处置', alert_id=al['id'], today=TODAY)
        t4 = S.confirm_draft(db, 'sngdssz', session_id='chat-c4', message='确认')
        a_after = db.get(AlertItem, al['id'])
        check(21, '预警上下文：确认后预警置为已下发',
              r4['intent'] and a_after.status == 'dispatched' and a_after.task_id == t4['id'],
              (a_after.status, a_after.task_id))

        # ── /api/ask 自动挂（打桩模型 + 打桩 agent）───────────
        import app.api.v1.agent as agent_api  # noqa: E402
        import app.main as M  # noqa: E402
        from fastapi.testclient import TestClient  # noqa: E402
        from app.core.deps import CurrentActor, get_current_actor  # noqa: E402

        def fake_ask(question, **kw):
            return {'answer': '（打桩）', 'session_id': kw.get('session_id'),
                    'trace': [], 'elapsed_ms': 1, 'model': 'stub'}
        orig_ask = agent_api.agent_mod.ask
        agent_api.agent_mod.ask = fake_ask
        c = TestClient(M.app)
        M.app.dependency_overrides[get_current_actor] = lambda: CurrentActor(
            type='user', user_id=1, username='sngdssz', display_name='两水所',
            role_codes=['user'], permissions=set())
        try:
            stub(True, 0.9, '明确要求')
            j1 = c.post('/api/ask', json={'question': '让他们3天内把方案交上来',
                                          'session_id': 'chat-api-1', 'today': TODAY}).json()
            check(22, '/api/ask 自动返回 task_draft', (j1.get('task_draft') or {}).get('intent') is True,
                  (j1.get('task_draft') or {}).get('task_id'))
            stub(False, 0.9, '查数')
            r = c.post('/api/ask', json={'question': '今年预算执行率是多少？',
                                          'session_id': 'chat-api-2', 'today': TODAY}).json()
            check(23, '/api/ask 普通提问不挂草稿', not r.get('task_draft'), r.get('task_draft'))
            # 本地环境 qa_log 可能未落库，这里手动给会话放一条用户消息，再测"整段对话→任务"
            alert_service.append_chat_message(db, 'chat-api-3', 'sngdssz', 1,
                                              '这钱年底花不完，得有人管', title='测试会话')
            rf = c.post('/api/tasks/draft/from-conversation',
                        json={'session_id': 'chat-api-3',
                              'instruction': '让两水所月底前花完，3天内给方案'})
            check(24, 'HTTP：把对话变成任务', rf.status_code == 200
                  and (rf.json().get('intent') is True), {'code': rf.status_code,
                                                          'body': rf.text[:120]})
            r2c = c.post('/api/tasks/draft/confirm',
                         json={'session_id': 'chat-api-3', 'message': '确认'})
            check(25, 'HTTP：确认建任务', r2c.status_code == 200
                  and r2c.json().get('status') == 'open', r2c.status_code)
        finally:
            agent_api.agent_mod.ask = orig_ask
            M.app.dependency_overrides.clear()

        paths = set(M.app.openapi()['paths'])
        need = ['/api/tasks/draft', '/api/tasks/draft/confirm', '/api/tasks/draft/pending',
                '/api/tasks/draft/from-conversation']
        check(26, '4 个对话任务接口已注册', all(p in paths for p in need),
              [p for p in need if p not in paths])
    finally:
        dispatch_ai.classify_task_intent = _ORIG
        db.close()

    (OUT / 'raw-from-chat.json').write_text(json.dumps(RESULTS, ensure_ascii=False, indent=1),
                                            encoding='utf-8')
    bad = [r for r in RESULTS if not r['ok']]
    print('=' * 90)
    print('%d/%d PASS' % (len(RESULTS) - len(bad), len(RESULTS)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
