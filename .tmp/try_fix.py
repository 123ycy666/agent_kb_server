# -*- coding: utf-8 -*-
"""只读：用当前库（ad_new 随州词典）跑几条宜昌真实问句，看纠错实际改动。"""
import io
import os
import sys

SERVER = r'D:\秘书智能体\server'
sys.path.insert(0, SERVER)
os.chdir(r'D:\秘书智能体')

from dotenv import load_dotenv
load_dotenv(r'D:\秘书智能体\.env')

from app.secretary import name_fix

out = io.StringIO()
st = name_fix.status()
out.write('status: %s\n' % st)

qs = [
    '西陵供电所的线损是多少',
    '夷陵区供电公司上个月售电量',
    '两河供电所的售电量',
    '鸦鹊岭供电服务站的公变有多少',
    '五峰渔洋关供电所这个月工单',
    '环谭供电所的现损',
    '当阳玉阳供电所的线笋率',
    '秭归县供电公司预蒜执行情况',
    '全市立了多少个工单',
]
for q in qs:
    r = name_fix.fix(q)
    out.write('\n%s\n  -> %s\n  used=%s ms=%s\n' % (q, r['text'], r['used'], r['ms']))
    if r.get('word_fixes'):
        out.write('  word_fixes=%s\n' % r['word_fixes'])
    if r.get('name_fixes'):
        out.write('  name_fixes=%s\n' % r['name_fixes'])
    if r.get('names'):
        out.write('  names=%s\n' % [n for n in r['names']][:3])
    if r.get('need_clarify'):
        out.write('  clarify=%s\n' % r['need_clarify'])
    if r.get('hint'):
        out.write('  hint=%s\n' % r['hint'])

open(r'D:\秘书智能体\.tmp\fix_probe.txt', 'w', encoding='utf-8').write(out.getvalue())
print('ok', len(out.getvalue()))
