# -*- coding: utf-8 -*-
"""名称归一不许误改的判据测试（从真实事故来：全市立了 → 全10里台区了）。"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')
from app.secretary import name_fix

RESULTS = []

def check(name, ok, detail=''):
    RESULTS.append({'case': name, 'ok': bool(ok), 'detail': str(detail)[:300]})
    print(('  [OK]   ' if ok else '  [FAIL] ') + name + (('   ' + str(detail)[:160]) if detail else ''))
    return ok

# 1. 不许把「全市立了…」改成台区（同音两字）
f = name_fix.fix('全市立了多少个工单？')
check('1 同音两字不许改', '十里' not in f['text'] and '台区' not in f['text'], f['text'])

# 2. 真地名仍要归一（三字及以上、或字面重合）
g = name_fix.fix('公家棚3号台区今年投了多少')
check('2 真地名仍归一', g['used'] and ('龚家棚' in g['text'] or '公家棚' in g['text']), g['text'])

# 3. 精确所名不受影响
h = name_fix.fix('环潭线损今年多少？')
check('3 精确所名照旧', '环潭' in h['text'], h['text'])

# 4. 已存在的台区名照旧
k = name_fix.fix('白鹤台区线损多少')
check('4 台区名照旧', '白鹤' in k['text'], k['text'])


# 5. 历史事故：结合意见工单 不许被切成 解河
m = name_fix.fix('结合意见工单看看哪个所最多')
check('5 结合意见不许改', '解河' not in m['text'], m['text'])

# 6. 真错字仍要改：凉水 → 两水
n = name_fix.fix('凉水供电所上个月线损多少')
check('6 凉水→两水', '两水' in n['text'], n['text'])

# 7. 常用问法不受影响
o = name_fix.fix('今年一共分下来多少？')
check('7 普通问句不动', '分下来多少' in o['text'], o['text'])


# 8. 错法表不许跨词边界：今年分了多少 不许变成 今年份了多少
p = name_fix.fix('环潭今年分了多少还剩多少？')
check('8 今年分了多少 不被改', '份了多少' not in p['text'], p['text'])

# 9. 时间词边界：滑窗不许从「10月」中间起头切出「月两」→「月亮台区」
#    （2026-10-28 线上：问两水所，被改成「10月亮台区水所」，SQL 查成月亮台区）
for q in ('2026年1-10月两水所工单有多少单', '10月两水所工单',
          '2026年1-9月两水所工单', '5日两水所工单'):
    r = name_fix.fix(q)
    check('9 时间词不被当地名（%s）' % q[:10],
          '亮台区' not in r['text'] and '月亮' not in r['text'], r['text'])

r = name_fix.fix('2026年1-10月两水所工单有多少单')
check('9.1 时间词之后照旧归一', '两水' in r['text'], r['text'])

# 10. 真名里带「月」的不受影响（前面没数字就不算时间词）
r = name_fix.fix('月亮台区本月线损')
check('10 月亮台区不被误伤', '月亮台区' in r['text'], r['text'])

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'output', 'test_results', 'namefix_test.json')
os.makedirs(os.path.dirname(out), exist_ok=True)
json.dump(RESULTS, open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
bad = [r for r in RESULTS if not r['ok']]
print('\n%d 项，%d 失败  -> %s' % (len(RESULTS), len(bad), os.path.normpath(out)))
sys.exit(1 if bad else 0)