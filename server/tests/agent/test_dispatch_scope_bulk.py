# -*- coding: utf-8 -*-
"""批量解析对照：`resolve_many` 必须与单账号 `resolve` 逐字段一致（122 个账号）。

跑法： py -X utf8 server/tests/agent/test_dispatch_scope_bulk.py
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

from app.secretary import alert_scope  # noqa: E402
from app.services import alert_service  # noqa: E402

OUT = ROOT / 'output' / 'test_results' / ('%s-预警任务流转' % time.strftime('%Y-%m-%d'))
OUT.mkdir(parents=True, exist_ok=True)
RESULTS = []
KEYS = ('ok', 'role_code', 'scope', 'scope_type', 'dept_id', 'station_ids',
        'station_names', 'ledger_node', 'name', 'error')


def check(no, name, ok, detail=''):
    RESULTS.append({'no': no, 'name': name, 'ok': bool(ok), 'detail': str(detail)[:800]})
    print('%s %2s %s%s' % ('PASS' if ok else 'FAIL', no, name,
                           ('  | ' + str(detail)[:200]) if detail else ''))


def main():
    accts = alert_service.all_role_accounts()
    extra = ['no_such_user', '', 'xgscwb003', 'gdzczy', 'sgsld', 'sngdssz']
    all_accts = accts + [x for x in extra if x not in accts]

    t0 = time.time()
    one = {a: alert_scope.resolve(a) for a in all_accts}
    t_one = time.time() - t0
    t0 = time.time()
    many = alert_scope.resolve_many(all_accts)
    t_many = time.time() - t0

    check(1, '账号数量 >= 100', len(all_accts) >= 100, len(all_accts))
    check(2, '返回键集合一致（空账号不参与批量）',
          (set(one) - {''}) == set(many), len(many))
    diff = []
    for a in [x for x in all_accts if x]:
        for k in KEYS:
            if one[a].get(k) != many[a].get(k):
                diff.append({'account': a, 'key': k, 'one': one[a].get(k), 'many': many[a].get(k)})
    check(3, '逐字段一致（%d 个账号 × %d 字段）' % (len([x for x in all_accts if x]), len(KEYS)),
          not diff, diff[:3])
    check(4, '广水例外仍然正确（xgscwb003 看 17 个所）',
          many.get('xgscwb003', {}).get('scope_type') == 'county'
          and len(many.get('xgscwb003', {}).get('station_ids') or []) == 17,
          len(many.get('xgscwb003', {}).get('station_ids') or []))
    check(5, '坏账号仍然关闸', many.get('no_such_user', {}).get('ok') is False
          and many.get('no_such_user', {}).get('error') == 'no_such_account',
          many.get('no_such_user', {}).get('error'))
    check(6, '批量解析明显更快（单账号 %.1fs → 批量 %.2fs）' % (t_one, t_many),
          t_many < t_one / 5, '%.2fs vs %.2fs' % (t_many, t_one))

    (OUT / 'raw-scope-bulk.json').write_text(json.dumps(RESULTS, ensure_ascii=False, indent=1),
                                             encoding='utf-8')
    bad = [r for r in RESULTS if not r['ok']]
    print('=' * 80)
    print('%d/%d PASS' % (len(RESULTS) - len(bad), len(RESULTS)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
