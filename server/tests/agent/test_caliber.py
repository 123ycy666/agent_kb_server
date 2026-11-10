# -*- coding: utf-8 -*-
"""指标契约三道闸（2026-11-10）。不需要模型、不需要业务库；契约用内存注入。

钉死的事：
1. 闸1：命中契约表的 SQL 缺必需过滤 → ERROR_CALIBER；带了就放行；没在契约里的表一律放行。
2. 闸2：expand 摊出的 SQL 必须带全 must；派生值由契约算（执行率 6.25%）。
3. 闸3：正文里证据没有的数 → 抓出来；证据里有的 → 放行。
4. 证据包：22 行结果不再截成 12 行，且由代码给出每列极值行。

运行：py -X utf8 server/tests/agent/test_caliber.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SERVER))

from app.secretary import caliber                       # noqa: E402

_checks: list[tuple[str, bool, str]] = []


def chk(label, cond, extra=''):
    _checks.append((label, bool(cond), extra))


CONTRACTS = [
    {'code': 'defect_work_order_count', 'label': '缺陷治理工单数量', 'unit': '件', 'digits': 0,
     'direction': '', 'expr': '', 'enabled': True, 'version_no': 1, 'notes': '',
     'updated_by': '', 'updated_at': None,
     'bases': [{'metric_code': 'defect_work_order_count', 'table_name': 't_power_ai_work_order',
                'column_name': 'id', 'agg': 'count', 'scope_col': 'power_station_id',
                'must': [{'col': 'year', 'op': '=', 'value': '$问题年份'},
                         {'col': 'work_status', 'op': 'in', 'value': ['2', '3']},
                         {'col': 'deleted_flag', 'op': '=', 'value': '0'}], 'sort_key': ''}]},
    {'code': 'defect_work_order_budget', 'label': '工单预算总额', 'unit': '元', 'digits': 2,
     'direction': '', 'expr': '', 'enabled': True, 'version_no': 1, 'notes': '',
     'updated_by': '', 'updated_at': None,
     'bases': [{'metric_code': 'defect_work_order_budget', 'table_name': 't_power_ai_work_order',
                'column_name': 'total_budget', 'agg': 'sum', 'scope_col': 'power_station_id',
                'must': [{'col': 'year', 'op': '=', 'value': '$问题年份'},
                         {'col': 'work_status', 'op': 'in', 'value': ['2', '3']},
                         {'col': 'deleted_flag', 'op': '=', 'value': '0'}], 'sort_key': ''}]},
    {'code': 'budget_allocated', 'label': '预算分配总额', 'unit': '元', 'digits': 2,
     'direction': '', 'expr': '', 'enabled': True, 'version_no': 1, 'notes': '',
     'updated_by': '', 'updated_at': None,
     'bases': [{'metric_code': 'budget_allocated',
                'table_name': 't_power_ai_company_budget_result',
                'column_name': 'budget_amount', 'agg': 'sum', 'scope_col': 'power_station_id',
                'must': [{'col': 'year', 'op': '=', 'value': '$问题年份'},
                         {'col': 'deleted_flag', 'op': '=', 'value': '0'}], 'sort_key': ''}]},
    {'code': 'budget_exec_rate', 'label': '预算执行率', 'unit': '%', 'digits': 2,
     'direction': 'bigger_better',
     'expr': 'defect_work_order_budget / budget_allocated * 100',
     'enabled': True, 'version_no': 1, 'notes': '工单预算总额除以预算分配总额，乘 100 是百分数',
     'updated_by': '', 'updated_at': None, 'bases': []},
]


def inject():
    caliber._CACHE['rows'] = CONTRACTS
    caliber._CACHE['t'] = time.time() + 3600


def t_gate1():
    inject()
    # 府河所日志里那句话原样：有工单、无 year
    bad = ("SELECT SUM(total_budget) AS budget_total FROM t_power_ai_work_order "
           "WHERE deleted_flag = 0 AND power_station_id = 40 AND work_status IN ('2', '3')")
    r = caliber.check_sql(bad)
    chk('闸1 缺 year 被拒', not r['ok'], r)
    chk('闸1 报 ERROR_CALIBER', r.get('code') == 'ERROR_CALIBER')
    chk('闸1 missing 里有 year', any(m['col'] == 'year' for m in r.get('missing') or []))

    good = bad.replace('WHERE ', "WHERE year = '2026' AND ")
    r2 = caliber.check_sql(good)
    chk('闸1 带了 year 放行', r2['ok'], r2)

    other = "SELECT stat_month FROM t_power_ai_station_summary WHERE power_station_id=40 LIMIT 5"
    chk('闸1 不碰契约外的表', caliber.check_sql(other)['ok'])


def t_gate2():
    inject()
    plan = caliber.expand('budget_exec_rate', year='2026', scope_id=40)
    chk('闸2 摊出两条 SQL', len(plan['sql']) == 2, plan.get('sql'))
    joined = ' '.join(plan['sql'])
    for col in ('year', 'work_status', 'deleted_flag', 'power_station_id'):
        chk('闸2 SQL 带 %s' % col, col in joined)
    chk('闸2 带具体年份', "'2026'" in joined)

    from app.secretary import tools_db
    orig = tools_db.run_sql
    vals = {'defect_work_order_budget': '32708.93', 'budget_allocated': '523486.84'}

    def fake(sql):
        for k, v in vals.items():
            if k == 'defect_work_order_budget' and 'total_budget' in sql:
                return {'rows': [{'v': v}], 'columns': ['v'], 'row_count': 1}
            if k == 'budget_allocated' and 'budget_amount' in sql:
                return {'rows': [{'v': v}], 'columns': ['v'], 'row_count': 1}
        return {'error': 'unexpected sql: ' + sql}
    tools_db.run_sql = fake
    try:
        r = caliber.query('budget_exec_rate', year='2026', scope_id=40)
    finally:
        tools_db.run_sql = orig
    chk('闸2 派生值由契约算 6.25', r.get('value') == '6.25', r)
    chk('闸2 返回单位与期间', r.get('unit') == '%' and r.get('year') == '2026', r)
    chk('闸2 带算式', (r.get('expr') or '').startswith('defect_work_order_budget / budget_allocated'),
        r.get('expr'))


def t_gate3():
    ev = ('【权威证据】\n  budget_amount = 523486.84元\n'
          '  work_order_count = 1件\n  执行率 = 6.25%')
    r = caliber.verify('2026年府河供电所缺陷治理工单数量为63件，执行率6.25%。', ev)
    chk('闸3 抓出证据里没有的 63 件', not r['ok'] and any('63' in x['text'] for x in r['missing']), r)
    r2 = caliber.verify('2026年府河供电所缺陷治理工单数量为1件，执行率6.25%。', ev)
    chk('闸3 证据里有的数放行', r2['ok'], r2)


def t_evidence():
    from app.secretary import evidence
    rows = [{'stat_month': '20260%d' % (i % 10), 'loss_rate': '%d.0' % (10 + i)} for i in range(22)]
    res = {'sql': 'SELECT stat_month, loss_rate FROM t_power_ai_station_summary', 'rows': rows,
           'columns': ['stat_month', 'loss_rate'], 'row_count': len(rows)}
    txt = evidence.text([{'kind': 'tool', 'tool': 'run_sql', 'result': res}])
    lines = [ln for ln in txt.splitlines() if '→' in ln]
    chk('证据包 22 行不再截成 12 行', len(lines) >= 22, 'lines=%d' % len(lines))
    chk('证据包给出极值行', '最大' in txt and '最小' in txt)
    chk('证据包不再写"其余 10 行略"', '行略' not in txt)
    chk('证据包不再禁止相除做派生', '相减/相除' not in txt)


def t_parse():
    inject()
    txt = ('否则从 预算分配总额、缺陷治理工单数量（缺陷治理工单状态为2和3）、'
           '工单预算总额（缺陷治理工单状态为2和3）、'
           '预算执行率（工单预算总额除以预算分配总额）、'
           '缺陷治理成效指标下降最多及上升最高的各说一个 说明即可 不要从台区表里查数据')
    r = caliber.parse_text(txt)
    got = {m['label']: m for m in r['metrics']}
    chk('解析认出四个指标', len(got) >= 4, list(got))
    cnt = got.get('缺陷治理工单数量') or {}
    chk('解析给工单数量加状态过滤',
        any(x['col'] == 'work_status' for x in (cnt.get('must') or [])), cnt.get('must'))
    chk('解析把过滤也写进 base',
        any(any(x['col'] == 'work_status' for x in (b.get('must') or []))
            for b in (cnt.get('bases') or [])), cnt.get('bases'))
    rate = got.get('预算执行率') or {}
    chk('解析出执行率算式',
        rate.get('expr') == 'defect_work_order_budget / budget_allocated * 100', rate.get('expr'))
    chk('解析把读不懂的句子列出来', any('下降最多' in u for u in r['unresolved']), r['unresolved'])


def main():
    for fn in (t_gate1, t_gate2, t_gate3, t_evidence, t_parse):
        try:
            fn()
        except Exception as exc:                        # noqa: BLE001
            chk('%s 抛异常' % fn.__name__, False, '%s: %s' % (type(exc).__name__, exc))
    bad = [x for x in _checks if not x[1]]
    for label, ok, extra in _checks:
        print(('  ok  ' if ok else ' FAIL ') + label + ('' if ok else '  ' + str(extra)[:200]))
    print('\n%d/%d 通过' % (len(_checks) - len(bad), len(_checks)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
