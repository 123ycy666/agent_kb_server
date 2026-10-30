# -*- coding: utf-8 -*-
import sys, json, os
sys.path.insert(0, "/app")
from app.core.config import settings
from app.secretary import tools_db, pgdb

print("=== 连接配置（脱敏） ===")
print("MySQL  :", os.environ.get("MYSQL_HOST") or os.environ.get("DB_HOST"),
      ":", os.environ.get("MYSQL_PORT") or os.environ.get("DB_PORT"))
u = os.environ.get("DATABASE_URL") or ""
print("PG DSN :", u.split("@")[-1] if "@" in u else "(未设置)")

print("\n=== MySQL ===")
try:
    r = tools_db._exec("SELECT VERSION() AS v, @@hostname AS h", limit_rows=1)["rows"]
    print("版本/主机:", r)
    r = tools_db._exec(
        "SELECT table_schema AS db, COUNT(*) AS tables, "
        "ROUND(SUM(data_length+index_length)/1048576,1) AS mb "
        "FROM information_schema.tables GROUP BY table_schema ORDER BY mb DESC", limit_rows=50)["rows"]
    for x in r:
        print("  ", x)
except Exception as e:
    print("[MySQL 失败]", type(e).__name__, str(e)[:200])

print("\n=== PostgreSQL ===")
try:
    c = pgdb.connect(autocommit=True)
    cur = c.cursor()
    cur.execute("SELECT version() AS v, current_database() AS db, inet_server_port() AS port")
    print("版本/库/端口:", cur.fetchone())
    cur.execute("SELECT datname FROM pg_database ORDER BY datname")
    print("所有库:", [x["datname"] if isinstance(x, dict) else x[0] for x in cur.fetchall()])
    cur.execute("SELECT schemaname, COUNT(*) AS n, "
                "ROUND(SUM(pg_total_relation_size(relid))/1048576.0,1) AS mb "
                "FROM pg_catalog.pg_statio_user_tables GROUP BY schemaname ORDER BY mb DESC")
    for x in cur.fetchall():
        print("  ", x)
    cur.close(); c.close()
except Exception as e:
    print("[PG 失败]", type(e).__name__, str(e)[:200])
