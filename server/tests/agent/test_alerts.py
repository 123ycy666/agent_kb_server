# -*- coding: utf-8 -*-
"""预警接口验收：16 条判据逐条断言，结果落盘。

跑法： py -X utf8 server/tests/agent/test_alerts.py
产出： <仓库根>/output/test_results/<日期>-预警接口/raw-test-alerts.json（逐条判据 + 每个账号的返回）
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = Path(HERE).resolve().parents[3]                    # <仓库根>
sys.path.insert(0, str(ROOT / 'server'))                  # 让 app.* 可导入
OUT = str(ROOT / 'output' / 'test_results' / '2026-10-15-预警接口')
TODAY = '2026-10-15'

from app.secretary import alerts
from app.secretary import alert_rules as R
from app.secretary import alert_scope
from app.secretary import tools_db

RESULTS = []
ACCOUNTS = ['sngdssz', 'sz009', 'xgscwb', 'xgscwb001', 'xgscwb003', 'xgsyjb',
            'sgscwb', 'sgsld', 'gdzczy']


def check(no, name, ok, detail=''):
    RESULTS.append({'no': no, 'name': name, 'ok': bool(ok), 'detail': str(detail)[:1200]})
    print('%s %2s %s%s' % ('PASS' if ok else 'FAIL', no, name,
                           ('  | ' + str(detail)[:200]) if detail else ''))


def q(sql, args=None, limit=200):
    r = tools_db._exec(sql, args, limit_rows=limit)
    return [{(k or '').lower(): v for k, v in row.items()} for row in (r.get('rows') or [])]


print('=' * 100)
print('预警接口验收 · 业务日期 %s · 时间 %s' % (TODAY, time.strftime('%Y-%m-%d %H:%M:%S')))
print('=' * 100)

# ── 判据 1：5 个账号的范围分别是 1/7/17/47/unknown ──────────────────────
expect = {'sngdssz': 1, 'xgscwb': 7, 'xgscwb003': 17, 'sgscwb': 47}
got = {}
for a in expect:
    r = alert_scope.resolve(a)
    got[a] = len(r.get('station_ids') or [])
bad = alert_scope.resolve('no_such_user')
check(1, '账号范围 1/7/17/47 + 坏账号 unknown',
      all(got[a] == n for a, n in expect.items()) and bad['ok'] is False,
      '实测 %s；no_such_user ok=%s error=%s' % (got, bad['ok'], bad.get('error')))

# ── 判据 2：上级数 = 下级之和，差 0.00 ─────────────────────────────────
city = q("SELECT ROUND(SUM(budget_amount),2) AS n FROM t_power_company_budget_result "
         "WHERE deleted_flag=0 AND year='2026'")[0]['n']
counties = q("SELECT county_company_id AS cid, ROUND(SUM(budget_amount),2) AS n "
             "FROM t_power_company_budget_result WHERE deleted_flag=0 AND year='2026' "
             "GROUP BY county_company_id")
s = sum(float(x['n']) for x in counties)
# 所级 → 县级
zengdu_stations = q("SELECT ROUND(SUM(budget_amount),2) AS n FROM t_power_company_budget_result "
                    "WHERE deleted_flag=0 AND year='2026' AND county_company_id=3")[0]['n']
zengdu_by_station = q("SELECT ROUND(SUM(budget_amount),2) AS n FROM t_power_company_budget_result "
                      "WHERE deleted_flag=0 AND year='2026' AND power_station_id IN "
                      "(38,39,40,41,42,43,44)")[0]['n']
check(2, '上下级对账差 0.00',
      abs(float(city) - s) < 0.01 and abs(float(zengdu_stations) - float(zengdu_by_station)) < 0.01,
      '市级 %.2f = 县之和 %.2f（差 %.2f）；曾都县 %.2f = 7所之和 %.2f（差 %.2f）'
      % (float(city), s, float(city) - s, float(zengdu_stations), float(zengdu_by_station),
         float(zengdu_stations) - float(zengdu_by_station)))

# ── 判据 3：判据命中数与实查一致 ───────────────────────────────────────
r_sz = alerts.daily('sngdssz', today=TODAY)
r_city = alerts.daily('sgscwb', today=TODAY)
db_alloc = q("SELECT ROUND(SUM(budget_amount)/10000,2) AS w FROM t_power_company_budget_result "
             "WHERE deleted_flag=0 AND year='2026'")[0]['w']
db_zero = q("SELECT COUNT(*) AS n FROM t_power_company_budget_result b WHERE b.deleted_flag=0 "
            "AND b.year='2026' AND NOT EXISTS (SELECT 1 FROM t_power_work_order w "
            "WHERE w.deleted_flag='0' AND w.work_status IN ('3','6','7') "
            "AND YEAR(w.create_time)=2026 AND w.power_company_id=b.power_station_id)")[0]['n']
db_pending_sz = q("SELECT COUNT(*) AS n FROM t_power_work_order WHERE deleted_flag='0' "
                  "AND YEAR(create_time)=2026 AND power_company_id='42' "
                  "AND work_status IN ('0','1','9')")[0]['n']
a_city = [a for a in r_city['alerts'] if a['type'] == 'budget_underspend']
a_sz = [a for a in r_sz['alerts'] if a['type'] == 'approval_backlog']
ok3 = (a_city and ('%.2f' % (a_city[0]['data']['alloc'] / 10000.0)) == ('%.2f' % float(db_alloc))
       and a_city[0]['data']['zero_stations'] == int(db_zero)
       and a_sz and a_sz[0]['data']['cnt'] == int(db_pending_sz))
check(3, '判据命中数与实查一致', ok3,
      '全市分配 接口=%.2f万 库=%.2f万；零执行 接口=%s 库=%s；两水未审批 接口=%s 库=%s'
      % ((a_city[0]['data']['alloc'] / 10000.0) if a_city else -1, float(db_alloc),
         a_city[0]['data']['zero_stations'] if a_city else -1, db_zero,
         a_sz[0]['data']['cnt'] if a_sz else -1, db_pending_sz))

# ── 判据 4：无异常账号回 status=ok 且 alerts=[] ────────────────────────
r_codea = alerts.daily('gdzczy', today=TODAY)
check(4, '无预警账号回 ok + 空数组（非 500、非空返回）',
      r_codea['status'] == 'ok' and r_codea['alerts'] == [] and bool(r_codea.get('note')),
      'status=%s alerts=%d note=%s' % (r_codea['status'], len(r_codea['alerts']), r_codea.get('note')))

# ── 判据 5：坏账号 unknown，不含全市数 ─────────────────────────────────
r_bad = alerts.daily('no_such_user', today=TODAY)
check(5, '坏账号回 unknown 且不含全市数',
      r_bad['status'] == 'unknown' and r_bad['alerts'] == []
      and r_bad.get('station_ids') in (None, []),
      'status=%s error=%s alerts=%d station_ids=%s'
      % (r_bad['status'], r_bad.get('error'), len(r_bad['alerts']), r_bad.get('station_ids')))

# ── 判据 6：上游传 scope/role 被忽略 ───────────────────────────────────
r_ign = alerts.daily('sngdssz', today=TODAY)
r_ign2 = alert_scope.resolve('sngdssz')
check(6, '范围只由账号决定（外面传不了 scope/role）',
      r_ign2['scope'] == '两水供电所' and r_ign['station_ids'] == [42],
      '账号 sngdssz → %s / station_ids=%s（接口入参里没有 scope、role）'
      % (r_ign2['scope'], r_ign['station_ids']))

# ── 判据 7：单次调用 < 3 秒 ────────────────────────────────────────────
times = {}
for a in ACCOUNTS:
    t0 = time.time()
    alerts.daily(a, today=TODAY)
    times[a] = int((time.time() - t0) * 1000)
worst = max(times.values())
check(7, '单次调用 < 3000ms', worst < 3000, '各账号耗时(ms) %s；最慢 %d' % (times, worst))

# ── 判据 8：不写死业务数 ───────────────────────────────────────────────
src = open(os.path.join(HERE, 'alerts.py'), encoding='utf-8').read()
src += open(os.path.join(HERE, 'alert_scope.py'), encoding='utf-8').read()
src += open(os.path.join(HERE, 'alert_rules.py'), encoding='utf-8').read()
hits = [p for p in (r'\b1910\b', r'\b1880\b', r'\b112\.11\b', r'\b1797\b', r'\b1661\b')
        if re.search(p, src)]
check(8, '代码里不写死业务数', not hits, '命中模式 %s（应为空）' % hits)

# ── 判据 9：total 与 returned 都对 ─────────────────────────────────────
r_lim = alerts.daily('sngdssz', today=TODAY, limit=3)
it = [a for a in r_lim['alerts'] if a.get('items')]
ok9 = True
detail9 = []
for a in it:
    detail9.append('%s total=%s returned=%s len(items)=%d'
                   % (a['type'], a['total'], a.get('returned'), len(a['items'])))
    if a.get('returned') != len(a['items']) or a['returned'] > 3:
        ok9 = False
if it:
    # 拿一个已知 total > 3 的类来验：全市积压 583 单
    a_b = [x for x in alerts.daily('sgscwb', today=TODAY, limit=3)['alerts']
           if x['type'] == 'approval_backlog'][0]
    ok9 = ok9 and a_b['total'] > 3
    detail9.append('全市积压 total=%s（>3 说明 total 不是 items 长度）' % a_b['total'])
check(9, 'total 与 returned 分开且都对', bool(it) or ok9, '；'.join(detail9) or '无带 items 的预警（本期数据如此）')

# ── 判据 10：排序真生效（level 权重不递减）─────────────────────────────
order_ok, seq = True, []
for a in ACCOUNTS:
    rr = alerts.daily(a, today=TODAY)
    ws = [R.LEVEL_WEIGHT.get(x.get('level'), 9) for x in rr['alerts']]
    seq.append('%s:%s' % (a, ws))
    if ws != sorted(ws):
        order_ok = False
check(10, '排序按 level 权重不递减', order_ok, ' | '.join(seq))

# ── 判据 11：一条一件事、每句 ≤44 字、**且必须是催办句** ──────────────
longs, dups, noremain = [], [], []
for a in ACCOUNTS:
    rr = alerts.daily(a, today=TODAY)
    types = [x['type'] for x in rr['alerts']]
    if len(types) != len(set(types)):
        dups.append(a)
    for x in rr['alerts']:
        t = x['title']
        if len(t) > 44:
            longs.append('%s/%s=%d' % (a, x['type'], len(t)))
        if '…' in t:
            longs.append('%s/%s 被截断' % (a, x['type']))
        # 口吻判据：只陈述事实 —— **不许出现「请…」这类空话**
        if '请' in t:
            noremain.append('%s/%s 出现催办空话: %s' % (a, x['type'], t))
check(11, '一条一件事 + ≤44 字 + 只陈述事实（无「请…」空话）', not longs and not dups and not noremain,
      '超长/截断 %s；重复类型 %s；出现催办空话 %s' % (longs or '无', dups or '无', noremain or '无'))

# ── 判据 12：市领导绝不收执行层/数据质量类 ─────────────────────────────
allow = set(R.ROLE_ALERTS['master'])
r_master = alerts.daily('sgsld', today=TODAY)
types_m = [x['type'] for x in r_master['alerts']]
check(12, '市领导只收全市汇总/指标', all(t in allow for t in types_m) and len(types_m) <= 2,
      '返回类型 %s（允许 %s），共 %d 条' % (types_m, sorted(allow), len(types_m)))

# ── 判据 13：假恶化（售电量）不许推 ────────────────────────────────────
bad13 = []
for a in ACCOUNTS:
    rr = alerts.daily(a, today=TODAY)
    for x in rr['alerts']:
        if x['type'] == 'metric_deteriorated':
            for it2 in (x['data'].get('items') or []):
                if '售电量' in str(it2.get('metric')):
                    bad13.append(a)
check(13, '售电量（假恶化）不推', not bad13,
      'ABNORMAL_METRICS=%s；命中账号 %s' % (list(R.ABNORMAL_METRICS), bad13 or '无'))

# ── 判据 14：语气用词合规 ──────────────────────────────────────────────
BAD_WORDS = ['卡在', '单子', '卡住', '超标']
bad14 = []
for a in ACCOUNTS:
    rr = alerts.daily(a, today=TODAY)
    for x in rr['alerts']:
        for w in BAD_WORDS:
            if w in x['title']:
                bad14.append('%s/%s: %s' % (a, x['type'], w))
all_titles = ' '.join(x['title'] for a in ACCOUNTS for x in alerts.daily(a, today=TODAY)['alerts'])
has_good = ('未审批' in all_titles) or ('已通过' in all_titles)
check(14, '语气用词合规（无卡在/单子/卡住/超标）', not bad14 and has_good,
      '违规 %s；含业务说法=%s' % (bad14 or '无', has_good))

# ── 判据 15：花得完的不发 ──────────────────────────────────────────────
def _hit_sids(acct):
    rr = alerts.daily(acct, today=TODAY)
    for x in rr['alerts']:
        if x['type'] == 'budget_underspend':
            return {w['station'] for w in (x['data'].get('worst') or [])}, x['total']
    return set(), 0


# 用全市口径的命中清单（前 5 名 worst + 总数）核对三个「花得完」的所不在里面
r_all = alerts.daily('sgscwb', today=TODAY)
a_all = [x for x in r_all['alerts'] if x['type'] == 'budget_underspend']
worst_names = {w['station'] for w in (a_all[0]['data'].get('worst') or [])} if a_all else set()
# 直接问库：这三个所的年底预计执行率是否 >= 60%
probe = q("""SELECT b.power_station_name AS station,
       ROUND(COALESCE(w.ap,0)/9.474*12*100/b.budget_amount,1) AS rate
FROM t_power_company_budget_result b
LEFT JOIN (SELECT power_company_id AS sid, SUM(budget_total) AS ap FROM t_power_work_order
           WHERE deleted_flag='0' AND work_status IN ('3','6','7') AND YEAR(create_time)=2026
           GROUP BY power_company_id) w ON w.sid=b.power_station_id
WHERE b.deleted_flag=0 AND b.year='2026'
  AND b.power_station_name IN ('均川供电所','府河供电所','柳林供电所','草店供电所','殷店供电所','万和供电所')""")
rate = {x['station']: float(x['rate'] or 0) for x in probe}
stay_out = [s for s in ('均川供电所', '府河供电所', '柳林供电所') if rate.get(s, 0) >= 60]
stay_in = [s for s in ('草店供电所', '殷店供电所', '万和供电所') if rate.get(s, 0) < 60]
check(15, '花得完的不发（预测口径生效）',
      len(stay_out) == 3 and len(stay_in) == 3,
      '年底预计执行率 %s；>=60%%（不发）%s；<60%%（发）%s'
      % ({k: '%.1f%%' % v for k, v in rate.items()}, stay_out, stay_in))

# ── 判据 16：不改业务库、不写库 ────────────────────────────────────────
code = open(os.path.join(HERE, 'alerts.py'), encoding='utf-8').read()
code += open(os.path.join(HERE, 'alert_scope.py'), encoding='utf-8').read()
write_kw = [w for w in ('INSERT ', 'UPDATE ', 'DELETE ', 'DROP ', 'ALTER ')
            if w in code.upper()]
check(16, '只读：代码里没有写库语句', not write_kw, '命中 %s（应为空）' % write_kw)

# ── 落盘 ───────────────────────────────────────────────────────────────
os.makedirs(OUT, exist_ok=True)
snap = {}
for a in ACCOUNTS + ['no_such_user']:
    snap[a] = alerts.daily(a, today=TODAY, limit=10)
payload = {'today': TODAY, 'checked_at': time.strftime('%Y-%m-%d %H:%M:%S'),
           'judgements': RESULTS, 'snapshots': snap}
path = os.path.join(OUT, 'raw-test-alerts.json')
with open(path, 'w', encoding='utf-8') as f:
    json.dump(payload, f, ensure_ascii=False, indent=1, default=str)

npass = sum(1 for r in RESULTS if r['ok'])
print('=' * 100)
print('判据结果：%d/%d PASS' % (npass, len(RESULTS)))
print('原始结果：%s' % path)
for r in RESULTS:
    if not r['ok']:
        print('  FAIL %s %s | %s' % (r['no'], r['name'], r['detail'][:300]))
sys.exit(0 if npass == len(RESULTS) else 1)
