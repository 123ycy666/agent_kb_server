# -*- coding: utf-8 -*-
import io
import psycopg

out = io.StringIO()
c = psycopg.connect(host='127.0.0.1', port=5433, dbname='ad_yc', user='kb',
                    password='kb_dev_pwd', autocommit=True)
cur = c.cursor()
for t in ('t_nc_pinyin', 't_nc_catalog', 't_nc_wordlist', 't_nc_ambiguous', 't_nc_typo'):
    cur.execute("""select column_name, data_type from information_schema.columns
                   where table_schema='cfg' and table_name=%s order by ordinal_position""", (t,))
    out.write('=== cfg.%s\n  %s\n' % (t, ', '.join('%s(%s)' % (r[0], r[1]) for r in cur.fetchall())))

cur.execute('select * from cfg.t_nc_pinyin order by id limit 8')
cols = [d[0] for d in cur.description]
out.write('\npinyin cols: %s\n' % cols)
for r in cur.fetchall():
    out.write('  %s\n' % ' | '.join(str(x) for x in r))

cur.execute("select count(distinct initial), count(distinct final) from cfg.t_nc_pinyin")
out.write('\n拼音表 distinct initial/final: %s\n' % (cur.fetchone(),))

cur.execute("""select name, py_full, py_strict, initials, core_code from cfg.t_nc_catalog
               where kind='供电所' and deleted_flag=0 order by id limit 6""")
out.write('\n供电所样本:\n')
for r in cur.fetchall():
    out.write('  %s\n' % ' | '.join(str(x) for x in r))
c.close()
open(r'D:\秘书智能体\.tmp\schema_dump.txt', 'w', encoding='utf-8').write(out.getvalue())
print('ok')
