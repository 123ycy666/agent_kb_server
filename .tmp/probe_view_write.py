# -*- coding: utf-8 -*-
import io
import psycopg

out = io.StringIO()
c = psycopg.connect(host='127.0.0.1', port=5433, dbname='ad_yc', user='kb',
                    password='kb_dev_pwd', autocommit=False,
                    options='-csearch_path=public')
cur = c.cursor()
for v in ('t_nc_typo', 't_nc_catalog'):
    cur.execute("select pg_get_viewdef('public.%s'::regclass) as d" % v)
    out.write('=== view %s\n%s\n' % (v, cur.fetchone()[0]))

cur.execute('select current_schema()')
out.write('\ncurrent_schema=%s\n' % (cur.fetchone(),))
try:
    cur.execute("""INSERT INTO t_nc_typo (wrong, right_word, org, freq_level, reason, sample,
                   deleted_flag, update_time) VALUES (%s,%s,%s,%s,%s,%s,0,now()) RETURNING id""",
                ('测试错字', '测试正字', '测试', '高频', '同音', '测试错字 -> 测试正字'))
    out.write('insert OK id=%s\n' % (cur.fetchone(),))
except Exception as e:
    out.write('insert ERR %s: %s\n' % (type(e).__name__, str(e)[:300]))
c.rollback()

try:
    cur.execute("UPDATE t_nc_typo SET sample = %s, update_time = now() WHERE wrong = %s AND deleted_flag = 0",
                ('x', '请况'))
    out.write('update OK rowcount=%s\n' % cur.rowcount)
except Exception as e:
    out.write('update ERR %s: %s\n' % (type(e).__name__, str(e)[:300]))
c.rollback()
c.close()
open(r'D:\秘书智能体\.tmp\view_write_probe.txt', 'w', encoding='utf-8').write(out.getvalue())
print('ok')
