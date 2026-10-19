# -*- coding: utf-8 -*-
"""线上验收：对生产端点逐账号实测 /api/alerts/daily，结果落盘 JSON。

只发 HTTP 请求，不写任何库。跑法： py -X utf8 tools/verify_alerts_online.py
"""
import json
import os
import sys
import time
import urllib.request

BASE = os.environ.get('ALERT_BASE', 'http://47.121.183.81:8200')
TODAY = os.environ.get('ALERT_TODAY', time.strftime('%Y-%m-%d'))
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   'output', 'test_results', '2026-10-15-预警接口')
ACCOUNTS = [
    ('sngdssz', '所长(两水所)'), ('sz009', '所长(均川所)'),
    ('xgscwb', '县财务(曾都)'), ('xgscwb003', '县财务(广水,挂在关庙所)'),
    ('xgscwb001', '县财务(随县)'), ('xgsyjb', '县运检(曾都)'),
    ('sgscwb', '市财务'), ('sgsld', '市领导'), ('gdzczy', '录入人员'),
    ('no_such_user', '不存在的账号'),
]
BAD = ['卡在', '单子', '卡住', '超标']


def call(account, today=TODAY, extra=None):
    body = {'account': account, 'today': today, 'when': today + ' 08:00'}
    if extra:
        body.update(extra)
    data = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(BASE + '/api/alerts/daily', data=data,
                                 headers={'Content-Type': 'application/json; charset=utf-8'})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read().decode('utf-8', 'replace')
            return r.status, json.loads(raw), int((time.time() - t0) * 1000)
    except urllib.error.HTTPError as e:
        return e.code, {'raw': e.read().decode('utf-8', 'replace')[:300]}, int((time.time() - t0) * 1000)
    except Exception as e:                                  # noqa: BLE001
        return 0, {'error': '%s: %s' % (type(e).__name__, e)}, int((time.time() - t0) * 1000)


print('=' * 100)
print('线上验收 %s · 业务日期 %s · 时间 %s' % (BASE, TODAY, time.strftime('%Y-%m-%d %H:%M:%S')))
print('=' * 100)

out = {'base': BASE, 'today': TODAY, 'checked_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'calls': []}
bad_words, over40, no_urge = [], [], []

for acct, label in ACCOUNTS:
    status, r, ms = call(acct)
    rec = {'account': acct, 'label': label, 'http': status, 'elapsed_ms': ms,
           'status': r.get('status'), 'name': r.get('name'), 'role': r.get('role'),
           'scope': r.get('scope'), 'scope_type': r.get('scope_type'),
           'station_ids_n': len(r.get('station_ids') or []) if r.get('station_ids') else 0,
           'error': r.get('error'), 'suppressed': r.get('suppressed'),
           'alerts': [{'type': a.get('type'), 'level': a.get('level'),
                       'title': a.get('title'), 'total': a.get('total'),
                       'amount': a.get('amount')} for a in (r.get('alerts') or [])]}
    out['calls'].append(rec)
    print('%-14s %-22s HTTP=%s status=%-9s %-14s %-12s %2d条 %5dms'
          % (acct, label, status, rec['status'], rec['role'], rec['scope'],
             len(rec['alerts']), ms))
    for a in rec['alerts']:
        print('      [%-20s/%-5s %2d字] %s' % (a['type'], a['level'], len(a['title'] or ''), a['title']))
        for w in BAD:
            if w in (a['title'] or ''):
                bad_words.append('%s/%s: %s' % (acct, a['type'], w))
        if len(a['title'] or '') > 44:
            over40.append('%s/%s=%d' % (acct, a['type'], len(a['title'])))
        if '请' in (a['title'] or ''):
            no_urge.append('%s/%s 出现催办空话' % (acct, a['type']))
        if '…' in (a['title'] or ''):
            no_urge.append('%s/%s 截断' % (acct, a['type']))
    if rec['status'] == 'unknown':
        print('      (unknown) error=%s' % rec['error'])

# 判据：市领导只收全市汇总；scope 参数无效
print('-' * 100)
master = [c for c in out['calls'] if c['account'] == 'sgsld'][0]
master_types = [a['type'] for a in master['alerts']]
print('市领导收到的类型：%s（共 %d 条）' % (master_types, len(master_types)))

st, r_ign, ms = call('sngdssz', extra={'scope': '全市', 'role': '市公司财务部'})
print('传 scope=全市 + role=市公司财务部 给 sngdssz → scope=%s station_ids=%s（应为 两水供电所 / 1 个所）'
      % (r_ign.get('scope'), len(r_ign.get('station_ids') or []) if r_ign.get('station_ids') else 0))

checks = {
    '全部 HTTP 200': all(c['http'] == 200 for c in out['calls']),
    '范围正确(1/7/17/20/47)': ({c['account']: c['station_ids_n'] for c in out['calls']}).get('sngdssz') == 1
                              and ({c['account']: c['station_ids_n'] for c in out['calls']}).get('xgscwb') == 7
                              and ({c['account']: c['station_ids_n'] for c in out['calls']}).get('xgscwb003') == 17
                              and ({c['account']: c['station_ids_n'] for c in out['calls']}).get('sgscwb') == 47,
    '坏账号 unknown 且无数据': [c for c in out['calls'] if c['account'] == 'no_such_user'][0]['status'] == 'unknown',
    '录入人员 ok 无预警': [c for c in out['calls'] if c['account'] == 'gdzczy'][0]['status'] == 'ok',
    '市领导 ≤2 条且只全市口径': len(master_types) <= 2 and set(master_types) <= {'budget_underspend', 'metric_deteriorated'},
    'scope/role 传了无效': r_ign.get('scope') == '两水供电所',
    '每条 ≤44 字且不被截断': not over40,
    '每句只陈述事实（无「请…」空话）': not no_urge,
    '无禁用词(卡在/单子/卡住/超标)': not bad_words,
    '单次 <3 秒': max(c['elapsed_ms'] for c in out['calls']) < 3000,
}
print('-' * 100)
for k, v in checks.items():
    print('%s %s' % ('PASS' if v else 'FAIL', k))
print('判据：%d/%d PASS' % (sum(1 for v in checks.values() if v), len(checks)))
print('耗时：max %dms' % max(c['elapsed_ms'] for c in out['calls']))

out['checks'] = checks
os.makedirs(OUT, exist_ok=True)
p = os.path.join(OUT, 'raw-online-test.json')
with open(p, 'w', encoding='utf-8') as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print('落盘：%s' % p)
sys.exit(0 if all(checks.values()) else 1)
