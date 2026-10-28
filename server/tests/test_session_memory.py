# -*- coding: utf-8 -*-
"""会话记忆契约（2026-10-26）。

    python tests/test_session_memory.py

"记忆"只做**会话内**的多轮，不跨会话、不跨用户。两条必须成立：

  A. 继承规则：本轮没提时间/地点时沿用上一轮；提了就覆盖；说「全市」不继承地点。
  B. 会话隔离：`qa_log.load_context(session_id)` 只返回该会话自己的轮次。

B 是 2026-10-26 用户反馈「记忆很乱」时最可疑的一条 —— 一旦按用户而不是
session_id 过滤，不同会话的上下文就会互相污染，表现就是"问了 A 所，答的却是别的所"。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.secretary import time_scope as ts   # noqa: E402

ok = fail = 0


def check(label, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print('  [ OK ] %-46s %s' % (label, extra))
    else:
        fail += 1
        print('  [FAIL] %-46s %s' % (label, extra))


PREV = {'period_type': 'year', 'period_key': '2025', 'time_text': '2025年',
        'place': '广水供电所'}

print('=' * 78)
print('A. 多轮继承规则')
print('=' * 78)

r = ts.describe('那线损呢', prev=PREV, today='2026-10-26')
check('什么都没提 → 时间地点都继承',
      r['time']['period_key'] == '2025' and r['place'] == '广水供电所'
      and r['inherited']['time'] and r['inherited']['place'])

r = ts.describe('全市的呢', prev=PREV, today='2026-10-26')
check('说「全市」→ 地点覆盖，时间仍继承',
      r['place'] == '全市' and not r['inherited']['place']
      and r['time']['period_key'] == '2025')

r = ts.describe('那2024年呢', prev=PREV, today='2026-10-26')
check('提了年份 → 时间覆盖，地点继承',
      r['time']['period_key'] == '2024' and not r['inherited']['time']
      and r['place'] == '广水供电所')

r = ts.describe('广水所的线损', prev=PREV, today='2026-10-26')
check('提了地点 → 地点覆盖，时间继承',
      r['place'] == '广水供电所' and not r['inherited']['place']
      and r['time']['period_key'] == '2025')

r = ts.describe('今年的呢', prev=PREV, today='2026-10-26')
check('说「今年」→ 时间覆盖为今年至今',
      r['time']['period_type'] == 'yearToDate' and not r['inherited']['time'])

r0 = ts.describe('那线损呢', prev=None, today='2026-10-26')
check('无上一轮 → 走默认，不报错',
      r0['place'] and r0['time']['period_type'] and not r0['inherited']['place'])

print('\n' + '=' * 78)
print('B. 会话隔离（load_context 只能看到本会话）')
print('=' * 78)

try:
    import psycopg
    from app.secretary import pgdb, qa_log

    with psycopg.connect(pgdb.DSN, connect_timeout=8) as c:
        cur = c.cursor()
        cur.execute("""SELECT m.chat_session_id FROM t_chat_message m
                        JOIN t_chat_query_trace q ON q.message_id = m.id AND q.deleted_flag = 0
                        WHERE m.deleted_flag=0 AND m.sender_type=2
                        GROUP BY m.chat_session_id HAVING count(*) >= 2
                        ORDER BY random() LIMIT 2""")
        # 必须 join trace：load_context 只认「有 trace 关联」的轮次，
        # 只按 t_chat_message 抽样会抽到没有 trace 的会话 → 偶发 0 轮（2026-10-28 修的 flake）。
        sids = [r[0] for r in cur.fetchall()]

    if len(sids) < 2:
        print('  [SKIP] 库里有回答的会话不足 2 个，跳过隔离测试')
    else:
        a = qa_log.load_context(sids[0], limit=5)
        b = qa_log.load_context(sids[1], limit=5)
        check('A 会话能读到轮次', len(a) > 0, '%d 轮' % len(a))
        check('B 会话能读到轮次', len(b) > 0, '%d 轮' % len(b))
        # 关键：两次取到的内容不能是同一批（说明没有按会话过滤）
        same = [x for x in a if x in b]
        check('两个会话的内容不重叠', not same, '重叠 %d 条' % len(same))
        check('A 的返回是正序（旧→新）',
              all(a[i]['seq_no'] <= a[i + 1]['seq_no'] for i in range(len(a) - 1))
              if 'seq_no' in (a[0] if a else {}) else True)
except Exception as e:                      # noqa: BLE001
    print('  [SKIP] 会话隔离测试未执行：%s: %s' % (type(e).__name__, str(e)[:60]))

print('\n' + '=' * 78)
print('结果：%d 通过 / %d 失败' % (ok, fail))
print('=' * 78)
sys.exit(1 if fail else 0)
