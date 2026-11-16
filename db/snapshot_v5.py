# -*- coding: utf-8 -*-
"""v5 迁移前整库快照：pg_dump + 逐表行数 + 列清单。

产物：output/backup/v5_before_<ts>/
    dump.sql        —— pg_dump -Fc（可 pg_restore）
    counts.json     —— 28 张表行数
    columns.json    —— 全部列定义（迁移后比对结构没变）
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
URL = 'postgresql://kb:kb_dev_pwd@127.0.0.1:5433/ad_new'
PGBIN = Path(r'D:\pg16local\pgsql\bin')
sys.path.insert(0, str(ROOT / 'db'))
from migrate_v5 import MOVES, connect, relkind  # noqa: E402


def cols(cur, schema, table):
    cur.execute("""select column_name, data_type, udt_name, is_nullable, column_default
                     from information_schema.columns
                    where table_schema=%s and table_name=%s order by ordinal_position""",
                (schema, table))
    return [list(r) for r in cur.fetchall()]


def main() -> int:
    ts = time.strftime('%Y%m%d-%H%M%S')
    out = ROOT / 'output' / 'backup' / ('v5_before_%s' % ts)
    out.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        cur = conn.cursor()
        counts, columns, where = {}, {}, {}
        for t in MOVES:
            for sch in ('public',) + tuple(sorted(set(MOVES.values()))):
                if relkind(cur, sch, t) in ('r', 'p'):
                    cur.execute('select count(*) from "%s"."%s"' % (sch, t))
                    counts[t] = cur.fetchone()[0]
                    columns[t] = cols(cur, sch, t)
                    where[t] = sch
                    break
        (out / 'counts.json').write_text(json.dumps(counts, ensure_ascii=False, indent=1),
                                         encoding='utf-8')
        (out / 'columns.json').write_text(json.dumps(columns, ensure_ascii=False, indent=1),
                                          encoding='utf-8')
        (out / 'where.json').write_text(json.dumps(where, ensure_ascii=False, indent=1),
                                        encoding='utf-8')
        cur.execute("select datname, pg_size_pretty(pg_database_size(datname)) from pg_database "
                    "order by 1")
        dbs = cur.fetchall()
        print('库：%s' % dbs)
        print('表 %d 张，总行数 %d' % (len(counts), sum(counts.values())))
    dump = out / 'dump.sql'
    exe = PGBIN / 'pg_dump.exe'
    if exe.exists():
        env = dict(os.environ, PGPASSWORD='kb_dev_pwd')
        p = subprocess.run([str(exe), '-h', '127.0.0.1', '-p', '5433', '-U', 'kb', '-d', 'ad_new',
                            '-Fc', '-f', str(dump)], env=env, capture_output=True, text=True)
        print('pg_dump exit=%d 大小=%s 字节'
              % (p.returncode, dump.stat().st_size if dump.exists() else 0))
        if p.returncode != 0:
            print(p.stderr[-500:])
    else:
        print('没找到 pg_dump，只留 JSON 快照：%s' % exe)
    print('快照目录：%s' % out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
