# -*- coding: utf-8 -*-
import io
import pymysql

out = io.StringIO()
conn = pymysql.connect(host='47.121.183.81', port=3306, user='root',
                       password='8FcXqrhYM@', charset='utf8mb4', connect_timeout=15)

def dump(db, sql, n=40):
    out.write('\n### %s | %s\n' % (db, sql))
    try:
        cur = conn.cursor()
        cur.execute('USE `%s`' % db)
        cur.execute(sql)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()[:n]
        out.write('cols: %s\n' % (cols,))
        for r in rows:
            out.write('  %s\n' % ' | '.join(str(x) for x in r))
        cur.close()
    except Exception as e:
        out.write('ERR %s\n' % str(e)[:200])

for db in ('yc_dlj_data', 'ai_data'):
    dump(db, "select table_name, table_rows from information_schema.tables "
             "where table_schema='%s' and (table_name like 't_power%%' or table_name like '%%area%%' "
             "or table_name like '%%org%%' or table_name like '%%station%%' or table_name like '%%company%%') "
             "order by table_name" % db)
    dump(db, "select * from t_power_supply_company where coalesce(deleted_flag,0)=0 limit 20")
    dump(db, "select * from t_power_company where coalesce(deleted_flag,0)=0 limit 25")
    dump(db, "select area_name, station_name from t_power_push_area where coalesce(deleted_flag,0)=0 limit 15")
    dump(db, "select count(*) from t_power_push_area where coalesce(deleted_flag,0)=0")

conn.close()
open(r'D:\秘书智能体\.tmp\mysql_dump.txt', 'w', encoding='utf-8').write(out.getvalue())
print('ok')
