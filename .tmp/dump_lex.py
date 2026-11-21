# -*- coding: utf-8 -*-
import io
import psycopg

out = io.StringIO()

def q(db, sql, n=30):
    c = psycopg.connect(host='127.0.0.1', port=5433, dbname=db, user='kb',
                        password='kb_dev_pwd', connect_timeout=5, autocommit=True)
    cur = c.cursor()
    cur.execute(sql)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()[:n]
    c.close()
    return cols, rows

def dump(db, sql, n=30):
    out.write('\n### %s | %s\n' % (db, sql))
    try:
        cols, rows = q(db, sql, n)
        out.write('cols: %s\n' % cols)
        for r in rows:
            out.write('  %s\n' % ' | '.join(str(x) for x in r))
    except Exception as e:
        out.write('ERR %s\n' % str(e)[:200])

for db in ('ad_yc', 'ad_new'):
    dump(db, "select k, v from cfg.site_info order by k")
    dump(db, "select k, v from cfg.runtime order by k")
    dump(db, "select count(*) from cfg.t_nc_catalog where deleted_flag=0")
    dump(db, "select name, kind, owner from cfg.t_nc_catalog where deleted_flag=0 order by id limit 40")
    dump(db, "select count(*) from cfg.t_nc_typo where deleted_flag=0")
    dump(db, "select wrong, right_word, org, freq_level, reason, sample from cfg.t_nc_typo where deleted_flag=0 order by id limit 25")
    dump(db, "select kind, count(*) from cfg.t_nc_word group by 1 order by 1")
    dump(db, "select * from cfg.t_nc_word order by kind limit 40")
    dump(db, "select word, domain, py_full, initials, src from cfg.t_nc_wordlist order by id limit 20")
    dump(db, "select py_code, name_a, kind_a, name_b, kind_b, action from cfg.t_nc_ambiguous order by id limit 15")
    dump(db, "select term, alias, org from cfg.t_nc_term order by id limit 12")
    dump(db, "select hanzi, py, py_strict, initial from cfg.t_nc_pinyin limit 12")

open(r'D:\秘书智能体\.tmp\lex_dump.txt', 'w', encoding='utf-8').write(out.getvalue())
print('written', len(out.getvalue()))
