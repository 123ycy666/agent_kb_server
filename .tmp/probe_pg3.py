# -*- coding: utf-8 -*-
import io
import psycopg

out = io.StringIO()
c = psycopg.connect(host='127.0.0.1', port=5433, dbname='ad_new', user='kb',
                    password='kb_dev_pwd', autocommit=True)
cur = c.cursor()

for kw in ('两河', '西陵', '夷陵', '五峰', '当阳', '鸦鹊岭'):
    cur.execute("select name, kind from cfg.t_nc_catalog where name like %s and deleted_flag=0 limit 5",
                ('%' + kw + '%',))
    out.write('catalog %s -> %s\n' % (kw, cur.fetchall()))

for kw in ('量和', '两河', '负荷', '线损'):
    cur.execute("select word, domain from cfg.t_nc_wordlist where word like %s limit 5", ('%' + kw + '%',))
    out.write('wordlist %s -> %s\n' % (kw, cur.fetchall()))

cur.execute("select kind, word from cfg.t_nc_word where word in ('两河','量和','府河','负荷')")
out.write('t_nc_word -> %s\n' % cur.fetchall())
c.close()
open(r'D:\秘书智能体\.tmp\lex_dump.txt', 'w', encoding='utf-8').write(out.getvalue())
print('ok')
