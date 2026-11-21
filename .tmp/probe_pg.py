import psycopg

for host, port in [('127.0.0.1', 5433)]:
    try:
        c = psycopg.connect(host=host, port=port, dbname='postgres', user='kb',
                            password='kb_dev_pwd', connect_timeout=5)
        cur = c.cursor()
        cur.execute('select datname from pg_database order by 1')
        print('databases:', [r[0] for r in cur.fetchall()])
        c.close()
    except Exception as e:
        print('ERR', host, port, e)
