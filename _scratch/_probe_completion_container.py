# -*- coding: utf-8 -*-
"""容器内只读探针：把「原话 → 纠错 → 补全 → 时间地点」逐步打出来。

不调模型、不写库（complete_rules 只 SELECT）。
"""
import json

from app.secretary import complete_rules, name_fix, preprocess, time_scope

print('complete_rule 行数:', len(complete_rules.rows()))
for r in complete_rules.rows():
    print('  ', r.get('kind'), '|', r.get('match_text'), '|', str(r.get('fill_text'))[:40])

Qs = ['情况', '问情况', '今年情况', '两水情况', '两水供电所情况',
      '情况怎么样', '今年情况怎么样', '全市情况']

for q in Qs:
    print('\n=== 原话:', q)
    print('  闸       :', preprocess.gate(q))
    fixed = name_fix.fix(q)
    print('  纠错后   :', fixed.get('text'), '| used=', fixed.get('used'))
    comp = preprocess.complete(q)
    print('  补全后   :', comp.get('text'), '| changed=', comp.get('changed'),
          '| hits=', json.dumps(comp.get('hits'), ensure_ascii=False))
    # agent 里的真实顺序：先纠错改 question，再对 question 补全
    q1 = fixed.get('text') if fixed.get('used') else q
    comp2 = preprocess.complete(q1)
    print('  纠错→补全:', comp2.get('text'), '| changed=', comp2.get('changed'))
    sc = time_scope.describe(comp2.get('text'), fixed)
    print('  时间改写 :', sc['time'].get('rewritten'))
    print('  最终问题 :', sc['question'])
    print('  时间     :', sc['time'].get('time_text'), sc['time'].get('period_type'),
          sc['time'].get('period_key'), '| 默认=', sc['time'].get('is_default'))
    print('  地点     :', sc['place'], '| confirm=', sc.get('place_confirm'))
