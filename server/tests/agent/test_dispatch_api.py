# -*- coding: utf-8 -*-
"""接口层冒烟：路由 / 字段 / 鉴权 / 错误码（TestClient，不走真实登录）。

跑法： py -X utf8 server/tests/agent/test_dispatch_api.py
产出： output/test_results/<日期>-预警任务流转/raw-api.json
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

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

import app.main as M  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.core.deps import CurrentActor, get_current_actor  # noqa: E402
from app.secretary import config as sec_config  # noqa: E402

TODAY = '2026-10-19'
TOKEN = {'X-Internal-Token': sec_config.KB_INTERNAL_TOKEN}
OUT = ROOT / 'output' / 'test_results' / ('%s-预警任务流转' % time.strftime('%Y-%m-%d'))
OUT.mkdir(parents=True, exist_ok=True)
RESULTS = []
TB = ['alert_scan_run', 'alert_item', 'dispatch_task', 'dispatch_task_assignee',
      'dispatch_task_event', 'dispatch_task_plan', 'dispatch_check', 'dispatch_notice',
      'dispatch_attachment', 'alert_chat_link']


def check(no, name, ok, detail=''):
    RESULTS.append({'no': no, 'name': name, 'ok': bool(ok), 'detail': str(detail)[:800]})
    print('%s %2s %s%s' % ('PASS' if ok else 'FAIL', no, name,
                           ('  | ' + str(detail)[:200]) if detail else ''))


def actor(login):
    return CurrentActor(type='user', user_id=1, username=login, display_name=login,
                        role_codes=['user'], permissions=set())


def reset():
    assert '127.0.0.1' in settings.database_url and '5433' in settings.database_url
    db = SessionLocal()
    try:
        db.execute(text('TRUNCATE %s RESTART IDENTITY CASCADE' % ', '.join(TB)))
        db.execute(text("DELETE FROM t_chat_message WHERE chat_session_id LIKE 'alert-%%'"))
        db.execute(text("DELETE FROM t_chat_session WHERE session_id LIKE 'alert-%%'"))
        db.commit()
    finally:
        db.close()


def main():
    reset()
    c = TestClient(M.app)
    M.app.dependency_overrides[get_current_actor] = lambda: actor('sngdssz')

    # 1 未带内部令牌 → 401
    r = c.post('/api/alerts/scan', json={'accounts': ['sngdssz'], 'today': TODAY, 'cadence': 'all'})
    check(1, 'scan 无内部令牌 → 401', r.status_code == 401 and r.json()['detail']['code'] == 'UNAUTHORIZED',
          r.status_code)

    # 2 带令牌扫描
    r = c.post('/api/alerts/scan', headers=TOKEN,
               json={'accounts': ['sngdssz', 'xgscwb', 'sgscwb'], 'today': TODAY, 'cadence': 'all'})
    j = r.json()
    check(2, 'scan 200 且返回计数', r.status_code == 200 and j.get('found', 0) > 0,
          {k: j.get(k) for k in ('found', 'new', 'resolved')})

    # 3 列表
    r = c.get('/api/alerts', params={'today': TODAY})
    j = r.json()
    check(3, 'GET /api/alerts 字段齐全',
          r.status_code == 200 and j['total'] >= 2 and
          {'category_name', 'subtype_name', 'days_open', 'data', 'verify_hint'} <= set(j['items'][0]),
          {'total': j.get('total'), 'keys': sorted((j.get('items') or [{}])[0].keys())[:8]})
    aid = j['items'][0]['id']

    # 4 详情 / 卡片
    r = c.get('/api/alerts/%d' % aid)
    check(4, 'GET /api/alerts/{id} 200', r.status_code == 200 and r.json()['id'] == aid, r.status_code)
    r = c.get('/api/alerts/%d/card' % aid)
    check(5, 'GET /api/alerts/{id}/card 200', r.status_code == 200 and 'card' in r.json(), r.status_code)

    # 6 点预警进对话
    r = c.post('/api/alerts/%d/chat' % aid, json={'today': TODAY})
    j = r.json()
    check(6, 'POST /api/alerts/{id}/chat → session + 2 条消息',
          r.status_code == 200 and j.get('session_id') and len(j.get('messages') or []) == 2,
          {'session': j.get('session_id'), 'msgs': len(j.get('messages') or [])})
    sid = j.get('session_id')

    # 7 拟办
    r = c.post('/api/alerts/%d/propose' % aid, json={'option': 1})
    check(7, 'POST /propose 返回 draft（不落库）',
          r.status_code == 200 and r.json().get('title') and r.json().get('verify_rule') is not None,
          r.json().get('title', '')[:40])

    # 8 下发确认页
    r = c.post('/api/alerts/%d/dispatch/preview' % aid)
    pv = r.json()
    cand = (pv.get('recommend') or {}).get('candidates') or []
    check(8, 'POST /dispatch/preview 有推荐承办人',
          r.status_code == 200 and pv.get('recommend', {}).get('role') == 'sz' and cand,
          {'role': pv.get('recommend', {}).get('role'), 'n': len(cand)})

    # 9 下发
    body = {'assignees': [{'employee_id': cand[0]['employee_id'], 'part': 'owner'}],
            'title': '接口冒烟任务', 'requirement': '3 天内回执',
            'verifier_type': 'human', 'idempotency_key': 'api-1'}
    r = c.post('/api/alerts/%d/dispatch' % aid, json=body)
    t = r.json()
    check(9, 'POST /dispatch 201/200 且返回 task_no',
          r.status_code == 200 and t.get('task_no'), {'code': r.status_code, 'task': t.get('task_no')})
    tid = t.get('id')
    r2 = c.post('/api/alerts/%d/dispatch' % aid, json=body)
    check(10, '幂等：重复下发同一 id', r2.status_code == 200 and r2.json().get('id') == tid,
          r2.json().get('id'))

    # 11 我的任务
    r = c.get('/api/tasks', params={'role': 'mine'})
    check(11, 'GET /api/tasks 200', r.status_code == 200 and r.json()['total'] >= 1, r.status_code)

    # 12 接单
    r = c.post('/api/tasks/%d/accept' % tid)
    check(12, 'POST accept → doing', r.status_code == 200 and r.json().get('status') == 'doing',
          r.json().get('status'))

    # 13 回执
    r = c.post('/api/tasks/%d/reply' % tid, json={'content': '已整改'})
    check(13, 'POST reply → verify', r.status_code == 200 and r.json().get('status') == 'verify',
          r.json().get('status'))

    # 14 核销
    r = c.post('/api/tasks/%d/close' % tid, json={'conclusion': '通过'})
    check(14, 'POST close → closed', r.status_code == 200 and r.json().get('status') == 'closed',
          r.json().get('status'))

    # 15 错误码：不存在
    r = c.get('/api/tasks/999999')
    check(15, '不存在任务 → 404', r.status_code == 404 and r.json()['detail']['code'] == 'TASK_NOT_FOUND',
          r.status_code)

    # 16 越权
    M.app.dependency_overrides[get_current_actor] = lambda: actor('xgscwb')
    r = c.get('/api/tasks/%d' % tid)
    check(16, '非参与人看任务 → 403', r.status_code == 403 and
          r.json()['detail']['code'] == 'NOT_PARTICIPANT', r.status_code)

    # 17 通知
    M.app.dependency_overrides[get_current_actor] = lambda: actor('sngdssz')
    r = c.get('/api/dispatch/notices', params={'unread': 1})
    check(17, 'GET /api/dispatch/notices 200', r.status_code == 200 and 'items' in r.json(),
          r.json().get('total'))

    # 18 tick（内部令牌）
    r = c.post('/api/dispatch/tick')
    check(18, 'tick 无令牌 → 401', r.status_code == 401, r.status_code)
    r = c.post('/api/dispatch/tick', headers=TOKEN)
    check(19, 'tick 带令牌 200', r.status_code == 200 and 'checked' in r.json(), r.json())

    # 20 健康 / 统计
    r = c.get('/api/dispatch/health')
    check(20, 'dispatch/health ok', r.status_code == 200 and r.json().get('ok') is True, r.status_code)
    r = c.get('/api/dispatch/stats')
    check(21, 'dispatch/stats 有闭环率', r.status_code == 200 and 'closed_rate' in r.json(), r.status_code)

    # 22 对话消息落库
    db = SessionLocal()
    try:
        n = db.execute(text('SELECT COUNT(*) FROM t_chat_message WHERE chat_session_id=:s'),
                       {'s': sid}).scalar_one()
    finally:
        db.close()
    check(22, '卡片与首段话已写入会话表', int(n) == 2, 'msgs=%s' % n)

    M.app.dependency_overrides.clear()
    (OUT / 'raw-api.json').write_text(json.dumps(RESULTS, ensure_ascii=False, indent=1),
                                      encoding='utf-8')
    bad = [x for x in RESULTS if not x['ok']]
    print('=' * 90)
    print('%d/%d PASS' % (len(RESULTS) - len(bad), len(RESULTS)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
