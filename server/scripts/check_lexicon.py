#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""纠错词典自检：全新部署后跑一遍，确认 t_nc_* 六张表已导入且真的生效。

用法（在 server/ 目录下，用项目自己的 python）：
    python scripts/check_lexicon.py

为什么需要它：
    纠错词典缺失时**不会报错**——name_fix.fix() 静默退化为原文，
    问答仍能跑，只是「环谭」不会被改成「环潭」，用户很难察觉。
    这个脚本做三件事：表在不在 / 数据够不够 / 纠错是否真的生效。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TABLES = {
    't_nc_typo': 100,        # 错别字（常见错写 → 正确写法）
    't_nc_term': 60,         # 术语/别名
    't_nc_ambiguous': 60,    # 同音歧义（禁入 + 处置动作）
    't_nc_wordlist': 150,    # 业务词表
    't_nc_catalog': 15000,   # 名称总表（机构/台区/线路…，最大的一张）
    't_nc_pinyin': 15000,    # 汉字拼音（名称归一的底座）
}

# 真实样例：左边是用户会打出来的错法，右边是期望结果包含的关键词
SAMPLES = [
    ('环谭供电所的线损', '环潭'),
    ('环谭供电所的现损', '线损'),
    ('环谭所上月台区线损', '环潭'),
]

ok = True


def line(tag, msg):
    global ok
    if tag == 'FAIL':
        ok = False
    print('  [%s] %s' % (tag, msg))


print('=' * 66)
print('纠错词典自检')
print('=' * 66)

# ---------- 1. 表与行数 ----------
print('\n[1/3] 词典表与数据量')
try:
    from app.secretary import pgdb
    conn = pgdb.connect()
    try:
        cur = conn.cursor()
        counts = {}
        for t, minimum in TABLES.items():
            try:
                cur.execute('SELECT count(*) AS n FROM %s' % t)
                row = cur.fetchone()
                n = row['n'] if isinstance(row, dict) else row[0]
                counts[t] = n
                if n >= minimum:
                    line(' OK ', '%-16s %6d 行' % (t, n))
                elif n > 0:
                    line('WARN', '%-16s %6d 行（少于预期 %d，可能只导入了部分数据）' % (t, n, minimum))
                else:
                    line('FAIL', '%-16s    0 行（空表 = 纠错会退化，请导入 db/lexicon.sql）' % t)
            except Exception as e:
                line('FAIL', '%-16s 不存在或不可读：%s' % (t, type(e).__name__))
    finally:
        try:
            conn.close()
        except Exception:
            pass
except Exception as e:
    line('FAIL', '连不上 PG：%s %s' % (type(e).__name__, str(e)[:120]))
    print('\n结论：数据库不可达，无法自检。先确认 DATABASE_URL / PG 容器状态。')
    raise SystemExit(1)

# ---------- 2. name_fix 是否就绪 ----------
print('\n[2/3] 纠错模块状态（/health 里的 name_fix 就是这个）')
try:
    from app.secretary import name_fix
    st = name_fix.status()
    if st.get('available'):
        line(' OK ', '已就绪   数据源=%s' % st.get('source'))
    else:
        line('FAIL', '未就绪   %s' % st.get('reason'))
except Exception as e:
    line('FAIL', '加载失败：%s %s' % (type(e).__name__, str(e)[:120]))

# ---------- 3. 真实纠错效果 ----------
print('\n[3/3] 纠错是否真的生效（这是最终标准）')
try:
    from app.secretary import name_fix
    fixed_any = False
    for raw, expect in SAMPLES:
        r = name_fix.fix(raw)
        got = r.get('text') or ''
        if not r.get('used'):
            line('WARN', '「%s」未被任何规则改写（词典可能未命中）' % raw)
        elif expect in got:
            line(' OK ', '「%s」→「%s」' % (raw, got))
            fixed_any = True
        else:
            line('WARN', '「%s」→「%s」（期望含「%s」，请人工复核）' % (raw, got, expect))
    if not fixed_any:
        line('FAIL', '所有样例都没生效：词典虽在但没起作用，检查是否 SECRETARY_NAMEFIX=0')
except Exception as e:
    line('FAIL', '纠错调用异常：%s %s' % (type(e).__name__, str(e)[:120]))

# ---------- 结论 ----------
print('\n' + '=' * 66)
if ok:
    print('结论：纠错词典已就绪 ✅')
    print('提示：/health 应显示 name_fix=true、name_fix_source=db:pg')
else:
    print('结论：纠错词典未就绪 ❌')
    print('修复：psql -U kb -d ad_new -f db/lexicon.sql')
    print('      然后重启后端，再跑一次本脚本')
print('=' * 66)
sys.exit(0 if ok else 1)
