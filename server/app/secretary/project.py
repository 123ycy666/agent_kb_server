# -*- coding: utf-8 -*-
# ⚠️ 本文件由 tools/gen_site_defaults.py 生成，内容来自原站点包（2026-10-30 拆解）。
"""项目事实（原站点包 site.yml / scopes.yml / jobs.yml，2026-10-30 内联）。

只放"部署时才定、改它要重启"的常量；业务规则不在这里。
"""
from __future__ import annotations

import os

SITE = (os.environ.get("SITE") or "suizhou").strip()
SITE_NAME = (os.environ.get("SECRETARY_SITE_NAME") or '随州').strip()
SITE_FULL = (os.environ.get("SECRETARY_SITE_FULL") or '国网随州供电公司').strip()
SITE_CODE = (os.environ.get("SECRETARY_SITE_CODE") or 'suizhou').strip()

FEATURES = {'general': True, 'data': True, 'doc': True, 'office': True}
TOOL_BUDGET = 12
DEFAULT_STATION = '环潭供电所'

DEPARTMENT = {'city_dept_id': 1,
 'city_label': '随州市供电所',
 'county_roles': ['cto', 'yxb', 'xgscwb'],
 'city_names': ['随州市', '全市'],
 'county_depts': [{'key': '曾都', 'dept_id': 10, 'role': 'xgscwb', 'label': '曾都区供电公司'},
                  {'key': '随县', 'dept_id': 11, 'role': 'xgscwb', 'label': '随县供电公司'},
                  {'key': '高新', 'dept_id': 12, 'role': 'xgscwb', 'label': '高新供电公司'},
                  {'key': '广水', 'dept_id': 13, 'role': 'xgscwb', 'label': '广水供电公司'}]}

JOBS = {'jobs': [{'id': 'selfcheck_daily',
           'fn': 'noop',
           'cron': '0 8 * * *',
           'enabled': True,
           'args': {'note': '部署自检'}},
          {'id': 'dispatch_tick',
           'fn': 'dispatch_tick',
           'cron': '*/10 * * * *',
           'enabled': True,
           'args': {}},
          {'id': 'dispatch_scan_daily',
           'fn': 'dispatch_scan',
           'cron': '0 8 * * *',
           'enabled': True,
           'args': {'cadence': 'daily'}},
          {'id': 'dispatch_scan_weekly',
           'fn': 'dispatch_scan',
           'cron': '10 8 * * 1',
           'enabled': True,
           'args': {'cadence': 'weekly'}},
          {'id': 'dispatch_scan_monthly',
           'fn': 'dispatch_scan',
           'cron': '20 8 5 * *',
           'enabled': True,
           'args': {'cadence': 'monthly'}},
          {'id': 'dispatch_scan_yearly',
           'fn': 'dispatch_scan',
           'cron': '30 8 1 1,7 *',
           'enabled': True,
           'args': {'cadence': 'yearly'}},
          {'id': 'alerts_daily',
           'fn': 'alerts_daily',
           'cron': '0 8 * * *',
           'enabled': False,
           'args': {}}]}


def department() -> dict:
    """部门锚点 + 县公司清单（原 scopes.yml 的 department 节）。"""
    return dict(DEPARTMENT)
