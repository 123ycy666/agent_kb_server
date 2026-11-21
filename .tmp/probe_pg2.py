import psycopg

def probe(db):
    print('=' * 20, db)
    try:
        c = psycopg.connect(host='127.0.0.1', port=5433, dbname=db, user='kb',
                            password='kb_dev_pwd', connect_timeout=5, autocommit=True)
    except Exception as e:
        print('  connect ERR', e)
        return
    cur = c.cursor()
    cur.execute("""select table_schema||'.'||table_name from information_schema.tables
                   where table_schema not in ('pg_catalog','information_schema')
                   order by 1""")
    tabs = [r[0] for r in cur.fetchall()]
    print('  tables(%d):' % len(tabs))
    for t in tabs:
        print('   ', t)
    for t in tabs:
        if t.lower().endswith(('nc_typo', 'nc_catalog', 'nc_pinyin', 'nc_word', 'nc_wordlist', 'nc_term', 'nc_ambiguous')):
            try:
                cur.execute('select count(*) from %s' % t)
                print('   rows', t, cur.fetchone()[0])
            except Exception as e:
                print('   rows ERR', t, e)
    c.close()

for db in ('ad_yc', 'skeleton', 'ad_new'):
    probe(db)
