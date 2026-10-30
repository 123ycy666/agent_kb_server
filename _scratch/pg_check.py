# -*- coding: utf-8 -*-
"""本地 PG 自检：缺哪些表，并能按 db/*.sql 补齐（只用于开发/测试环境）。

用法：
    py -X utf8 _scratch/pg_check.py          # 只查
    py -X utf8 _scratch/pg_check.py --apply  # 查完把缺的脚本执行一遍（按脚本整体执行，幂等）
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))

from app.secretary import pgdb  # noqa: E402

# 脚本 → 它负责的表（脚本本身都是幂等的 CREATE TABLE IF NOT EXISTS）
SCRIPTS = {
    'db/chat_tables.sql': ['t_chat_session', 't_chat_message', 't_chat_query_trace', 't_chat_trace_step'],
    'db/user_pref.sql': ['t_user_pref'],
    'db/chat_persona.sql': ['t_chat_persona'],
    'db/dispatch_tables.sql': ['dispatch_task'],
}


def _exists(cur, name: str) -> bool:
    cur.execute('SELECT to_regclass(%s) AS t', ('public.' + name,))
    row = cur.fetchone()
    return bool(row and row.get('t'))


def main() -> int:
    apply = '--apply' in sys.argv
    conn = pgdb.connect(autocommit=True)
    missing: list[str] = []
    try:
        cur = conn.cursor()
        try:
            for script, tables in SCRIPTS.items():
                lack = [t for t in tables if not _exists(cur, t)]
                print('%-28s %s' % (script, '缺：' + ', '.join(lack) if lack else '（齐）'))
                if lack:
                    missing.append(script)
        finally:
            cur.close()
    finally:
        conn.close()
    if apply and missing:
        import psycopg
        for script in missing:
            path = ROOT / script
            if not path.is_file():
                print('  [跳过] 没有脚本', script)
                continue
            sql = path.read_text(encoding='utf-8')
            with psycopg.connect(pgdb.DSN, autocommit=True) as c2:
                with c2.cursor() as cur2:
                    cur2.execute(sql)
            print('  已执行', script)
    return 0


if __name__ == '__main__':
    sys.exit(main())
