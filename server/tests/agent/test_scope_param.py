# -*- coding: utf-8 -*-
"""上游强制数据范围（force_place）的判据测试。

判据（动手前定死）：
  1. 传了上级指定范围 → place 就是它，且 place_default=False
  2. 上级指定范围 **压过**「沿用上一轮地点」（多轮会话）
  3. scope_text 里要写明这是上级指定的强制范围
  4. 不传（None/空串）时行为与今天完全一致：认出地点用它、没认出用全市+default
跑法： cd secretary && py -X utf8 test_scope_param.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')

from app.secretary import time_scope

RESULTS = []


def check(name, ok, detail=''):
    RESULTS.append({'case': name, 'ok': bool(ok), 'detail': str(detail)[:300]})
    print(('  [OK]   ' if ok else '  [FAIL] ') + name + (('   ' + str(detail)[:160]) if detail else ''))
    return ok


Q = '这个月所里啥情况？'
PREV = {'period_type': 'month', 'period_key': '202608', 'time_text': '2026年8月', 'place': '环潭供电所'}

# 1. 上级指定范围生效
try:
    d = time_scope.describe(Q, force_place='洪山供电所')
    check('1 上级范围生效', d['place'] == '洪山供电所' and not d['place_default'], d['place'])
except TypeError as e:
    check('1 上级范围生效', False, 'TypeError: %s' % e)
    d = {}

# 2. 压过沿用上一轮
try:
    d2 = time_scope.describe(Q, prev=PREV, force_place='洪山供电所')
    check('2 压过沿用上一轮', d2['place'] == '洪山供电所' and not d2['inherited']['place'],
          '%s inherited=%s' % (d2['place'], d2['inherited']['place']))
except TypeError as e:
    check('2 压过沿用上一轮', False, 'TypeError: %s' % e)

# 3. scope_text 写明强制
try:
    txt = time_scope.describe(Q, force_place='洪山供电所')['scope_text']
    check('3 scope_text 写明强制', '洪山供电所' in txt and '强制' in txt, txt.replace('\n', ' / ')[:120])
except TypeError as e:
    check('3 scope_text 写明强制', False, 'TypeError: %s' % e)

# 4. 不传时行为不变
a = time_scope.describe('环潭这个月咋样？')
check('4a 认出地点仍用它', a['place'] == '环潭供电所' and not a['place_default'], a['place'])
b = time_scope.describe('这月咋样？')
check('4b 没认出仍默认全市', b['place'] == '全市' and b['place_default'], '%s default=%s' % (b['place'], b['place_default']))

# 5. 空串等于没传
c = time_scope.describe('这月咋样？', force_place='')
check('5 空串等于没传', c['place'] == '全市' and c['place_default'], c['place'])


# 7. 补全拼接不留歧义：「今年分了多少钱」不该变成「…1-9月份了多少钱」
_q = time_scope.describe('今年分了多少钱、花了多少？')['question']
check('7 拼接不产生「月份了」', '月份' not in _q.replace('2026年1-9月份', ''), _q)

# 7b. 用户本轮明说「全市」→ 上游默认范围让位
d7 = time_scope.describe('全市几个节点、哪个最慢？', prev=PREV, force_place='随县供电公司')
check('7b 问题明说全市则让位', d7['place'] == '全市', d7['place'])

# 6. 上级指定「全市」也是明说（不再回落到沿用）
e = time_scope.describe('这月咋样？', prev=PREV, force_place='全市')
check('6 上级指定全市优先于沿用', e['place'] == '全市' and not e['place_default'], '%s default=%s' % (e['place'], e['place_default']))

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'output', 'test_results', 'scope_param_test.json')
os.makedirs(os.path.dirname(out), exist_ok=True)
json.dump(RESULTS, open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
bad = [r for r in RESULTS if not r['ok']]
print('\n%d 项，%d 失败  -> %s' % (len(RESULTS), len(bad), os.path.normpath(out)))
sys.exit(1 if bad else 0)
