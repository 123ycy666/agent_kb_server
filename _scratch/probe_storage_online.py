# -*- coding: utf-8 -*-
"""只读：分别探 MySQL（业务库+语义字典）和 PG（知识库/记录库），回答"是不是两个库"。

在容器内用应用自己的连接配置跑，避免我猜端口/库名。全部 SELECT / SHOW。
见 docs/04-计划/2026-10-29-个人知识库未生效与运行日志排查.md
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[1]
HOST = '47.121.183.81'
USER = 'root'

IN_CONTAINER = r'''# -*- coding: utf-8 -*-
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
'''


def env(name: str) -> str:
    txt = (ROOT / '.env').read_text(encoding='utf-8')
    m = re.search(r'^%s\s*=\s*(.+)$' % re.escape(name), txt, re.M)
    return (m.group(1).strip().strip('"').strip("'") if m else '')


def main() -> int:
    pwd = env('SERVER_SSH_PASSWORD')
    if not pwd:
        print('[X] 缺少 SERVER_SSH_PASSWORD')
        return 2
    local = ROOT / '_scratch/_probe_storage_container.py'
    local.write_text(IN_CONTAINER, encoding='utf-8')

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=pwd, timeout=20,
              banner_timeout=25, auth_timeout=25, look_for_keys=False, allow_agent=False)
    try:
        sftp = c.open_sftp()
        sftp.put(str(local), '/tmp/_probe_storage.py')
        sftp.close()
        chan = c.get_transport().open_session()
        chan.settimeout(120)
        chan.set_combine_stderr(True)
        chan.exec_command('docker cp /tmp/_probe_storage.py kb-merged:/tmp/_probe_storage.py && '
                          'docker exec -w /app -e PYTHONPATH=/app kb-merged python /tmp/_probe_storage.py')
        out = b''
        while True:
            b = chan.recv(65535)
            if not b:
                break
            out += b
        code = chan.recv_exit_status()
        print(out.decode('utf-8', 'replace'))
        print('[exit]', code)
        return 0 if code == 0 else 1
    finally:
        c.close()


if __name__ == '__main__':
    sys.exit(main())
