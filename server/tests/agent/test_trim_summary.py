# -*- coding: utf-8 -*-
"""取数结果的「自带合计」与「截断不破坏 JSON」的判据测试。

判据（动手前定死）：
  1. run_sql 的结果带 summary：数值列有 sum/mean/min/max/n（金额=元）
  2. summary 只对数值列生成，字符串列不生成
  3. 结果超过长度上限时：仍然是**合法 JSON**、rows 只留放得下的、row_count 保留真实行数、
     带 note 说明「只列了前 M 行，汇总取 summary」，summary 必须保留
  4. 放得下时行为不变：rows 完整、无 note
跑法： cd secretary && py -X utf8 test_trim_summary.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')

from app.secretary import agent
from app.secretary import tools_db

RESULTS = []


def check(name, ok, detail=''):
    RESULTS.append({'case': name, 'ok': bool(ok), 'detail': str(detail)[:300]})
    print(('  [OK]   ' if ok else '  [FAIL] ') + name + (('   ' + str(detail)[:180]) if detail else ''))
    return ok


# 1/2. 真库跑一条：47 行分配额，合计必须是 1910.00 万元（19100000 元）
try:
    res = tools_db.run_sql('SELECT power_station_name, budget_amount FROM t_power_company_budget_result '
                           'WHERE year=2026 AND deleted_flag=0')
    sm = res.get('summary') or {}
    check('1 summary 有金额合计', 'budget_amount' in sm and abs(float(sm['budget_amount']['sum']) - 19100000.0) < 1,
          json.dumps(sm.get('budget_amount'), ensure_ascii=False))
    check('2 字符串列不进 summary', 'power_station_name' not in sm, list(sm.keys()))
except Exception as e:
    check('1 summary 有金额合计', False, '%s: %s' % (type(e).__name__, e))
    check('2 字符串列不进 summary', False, 'skip')

# 3/4. _trim 的长度闸
big = {'columns': ['power_station_name', 'budget_amount'],
       'rows': [{'power_station_name': '第%d所' % i, 'budget_amount': str(400000 + i)} for i in range(300)],
       'row_count': 300,
       'summary': {'budget_amount': {'sum': 19100000.0, 'mean': 63666.67, 'min': 400000, 'max': 400299, 'n': 300}}}
t = agent._trim('run_sql', big)
try:
    parsed = json.loads(t)
    ok_json = True
except Exception as e:
    parsed, ok_json = None, False
    check('3a 截断后仍是合法 JSON', False, '%s' % e)
if ok_json:
    check('3a 截断后仍是合法 JSON', True, '%d 字符' % len(t))
    check('3b row_count 保留真实行数', parsed.get('row_count') == 300, parsed.get('row_count'))
    check('3c summary 保留', float((parsed.get('summary') or {}).get('budget_amount', {}).get('sum', 0)) == 19100000.0)
    check('3d 有截断说明', 'note' in parsed and 'summary' in str(parsed.get('note')), str(parsed.get('note'))[:120])
    check('3e rows 变少但非空', 0 < len(parsed.get('rows') or []) < 300, len(parsed.get('rows') or []))

# 5. 时间条件守卫：本轮口径有时间、SQL 不带时间条件 → 必须提醒这是历年累计
SCOPE = {'time': {'period_type': 'yearToDate', 'time_text': '2026年1-9月'}}
check('5a 漏时间条件要提醒',
      '历年累计' in agent._time_guard('SELECT SUM(budget_total) FROM t_power_work_order WHERE work_status IN (3,6,7)', SCOPE))
check('5b 带了时间条件不提醒',
      agent._time_guard('SELECT 1 FROM t_power_work_order WHERE YEAR(create_time)=2026', SCOPE) == '')
check('5c 别的表不提醒',
      agent._time_guard('SELECT 1 FROM t_power_company_budget_result WHERE year=2026', SCOPE) == '')

# 6. 范围守卫：本轮范围不是全市、SQL 却没带范围条件 → 必须提醒它查的是全市
Q = {'place': '随县供电公司'}
check('6a 漏范围条件要提醒',
      '没有真正带范围条件' in agent._scope_guard('SELECT COUNT(*) FROM t_power_work_order WHERE deleted_flag=0', Q))
check('6b 带了范围条件不提醒',
      agent._scope_guard('SELECT COUNT(*) FROM t_power_work_order WHERE power_company_id IN (SELECT id FROM t_power_company WHERE parent_id=2)', Q) == '')
check('6c 全市范围不提醒',
      agent._scope_guard('SELECT COUNT(*) FROM t_power_work_order', {'place': '全市'}) == '')


# 8. 范围守卫要认「看着像筛了、其实没筛」的写法（整表子查询）
DEG = 'SELECT COUNT(*) FROM t_power_work_order WHERE power_company_name IN (SELECT name FROM t_power_company WHERE deleted_flag=0)'
check('8a 整表子查询要提醒', '没有真正带范围条件' in agent._scope_guard(DEG, {'place': '随县供电公司'}))
check('8b 写 parent_id 不提醒', agent._scope_guard('SELECT 1 FROM t_power_work_order WHERE power_company_id IN (SELECT id FROM t_power_company WHERE parent_id=2)', {'place': '随县供电公司'}) == '')
check('8c 写 scope_key 不提醒', agent._scope_guard("SELECT 1 FROM t_power_ai_metric_snapshot WHERE scope_type='station' AND scope_key='abc'", {'place': '洪山供电所'}) == '')
check('8d 公司下辖名单能查出来', '个所' in agent._scope_detail('随县供电公司'))
check('8e 单位守卫只对指标表', ('千瓦时' in agent._unit_guard('SELECT metric_value FROM t_power_ai_metric_snapshot'))
      and agent._unit_guard('SELECT budget_amount FROM t_power_company_budget_result') == '')


# 9. 数字闸：金额必须能在本轮结果（含成对派生）里找到出处
TR = [{'kind': 'tool', 'tool': 'run_sql', 'result': {'rows': [{'budget_amount': '19100000.00'}],
       'summary': {'budget_amount': {'sum': 19100000.0, 'mean': 19100000.0, 'min': 19100000.0, 'max': 19100000.0, 'n': 1}}}},
      {'kind': 'tool', 'tool': 'run_sql', 'result': {'rows': [{'alloc': '449000.00', 'used': '256500.00'}]}}]
POOL = agent._num_pool(TR)
check('9a 有出处的金额放过', agent._untraceable_money('全市分配1910.00万元', POOL) == [])
check('9b 编出来的金额被抓', agent._untraceable_money('全市分配1950.00万元', POOL) == [1950.0])
check('9c 余额（派生差）放过', agent._untraceable_money('余额19.25万元', POOL) == [])
check('9d 10 万以下不看', agent._untraceable_money('小额5.50万元', POOL) == [])

# 10. 内部系数闸：不许把 0.6 系数换算成「XX% 基准线 / 进度要求」
check('10a 进度要求被抓', bool(agent._coefficient_talk('41个所预算执行率低于45%的进度要求')))
check('10b 基准线被抓', bool(agent._coefficient_talk('全市执行率低于 60% 的达标线')))
check('10c 正常比较不误报', agent._coefficient_talk('线损率2.61%，低于3%的正常线') == '')
check('10d 只给两个数不报', agent._coefficient_talk('已过时间比例75%，当前执行率5.87%') == '')

small = tools_db.run_sql('SELECT COUNT(*) AS c FROM t_power_company_budget_result WHERE year=2026 AND deleted_flag=0')
t2 = agent._trim('run_sql', small)
p2 = json.loads(t2)
check('4 放得下就原样', 'note' not in p2 and p2.get('row_count') == 1, t2[:120])

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'output', 'test_results', 'trim_summary_test.json')
os.makedirs(os.path.dirname(out), exist_ok=True)
json.dump(RESULTS, open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
bad = [r for r in RESULTS if not r['ok']]
print('\n%d 项，%d 失败  -> %s' % (len(RESULTS), len(bad), os.path.normpath(out)))
sys.exit(1 if bad else 0)
