# -*- coding: utf-8 -*-
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
import json
from app.secretary import tools_db
import sys
sys.stdout.reconfigure(encoding='utf-8')

TABLES = ['t_power_push_city_month_summary', 't_power_push_district_month_summary', 't_power_company_assessment',
          't_power_company', 't_power_supply_company', 't_power_line', 't_power_area',
          't_cost_engineering', 't_cost_material', 't_power_station_monthly_core_stat',
          't_power_company_budget_allocation', 't_power_work_order']
for t in TABLES:
    try:
        d = tools_db.describe_table(t)
        # describe_table 对「不在业务字典白名单」的表返回 {'error': ...} 而没有 'rows'
        # ——这是设计如此（白名单只认线上维护的 ai_data 表），不是脚本报错。
        if 'rows' not in d:
            print('%-38s SKIP %s' % (t, str(d.get('error', ''))[:90]))
            continue
        cols = [c['col_name'] for c in d['rows']]
        print('%-38s %2d cols :: %s' % (t, len(cols), ','.join(cols[:26])))
    except Exception as e:
        print('%-38s ERR %s' % (t, str(e)[:80]))
