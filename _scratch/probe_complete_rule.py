# -*- coding: utf-8 -*-
"""只读探针：看补全表实际内容 + 预处理两步对几个问法做了什么。

不调模型、不写库。
"""
import os
import sys

sys.path.insert(0, r'D:\秘书智能体\server')

import dotenv
dotenv.load_dotenv(r'D:\秘书智能体\.env')

import psycopg
from psycopg.rows import dict_row

dsn = os.environ['DATABASE_URL'].replace('postgresql+psycopg://', 'postgresql://')

print('=== complete_rule 表 ===')
conn = psycopg.connect(dsn, connect_timeout=8, row_factory=dict_row)
try:
    cur = conn.cursor()
    cur.execute('SELECT id, kind, match_text, fill_text, priority, enabled '
                'FROM complete_rule ORDER BY priority, id')
    for r in cur.fetchall():
        print(r)
finally:
    conn.close()

print()
print('=== prompt_text 覆盖值（含 time_* / keywords 等） ===')
conn = psycopg.connect(dsn, connect_timeout=8, row_factory=dict_row)
try:
    cur = conn.cursor()
    cur.execute('SELECT k, left(v, 120) v FROM prompt_text ORDER BY k')
    for r in cur.fetchall():
        print(r)
finally:
    conn.close()

from app.secretary import preprocess, time_scope   # noqa: E402

Qs = ['两水供电所情况', '今年情况', '2026年情况', '情况', '两水供电所今年情况',
      '两水供电所情况怎么样', '问情况']

print()
for q in Qs:
    g = preprocess.gate(q)
    comp = preprocess.complete(q)
    sc = time_scope.describe(comp['text'])
    print('原话  :', q)
    print('  闸      :', g)
    print('  补全后  :', comp['text'], '| changed=', comp['changed'], '| hits=', comp['hits'])
    print('  时间改写:', sc['time']['rewritten'], '| 时间=', sc['time']['time_text'],
          '| 地点=', sc['place'], '| 最终问题=', sc['question'])
    print()
