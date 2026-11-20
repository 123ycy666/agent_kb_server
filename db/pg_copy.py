# -*- coding: utf-8 -*-
"""把一个现场的 `cfg.*` / `kb.*` 复制到另一个库（SRS-01 DR-004 / AC-04）。

现场迁移只带两片：`cfg`（页面可改的配置）与 `kb`（知识库、做法库、词典）。
`qa`（会话历史）与 `base`（账号）不随现场走 —— 目标库保留自己的账号。

    python db/pg_copy.py --from ad_new --to ad_empty --dry     # 只报计划
    python db/pg_copy.py --from ad_new --to ad_empty           # 真复制
    python db/pg_copy.py --to ad_empty --check                 # 只比对行数

复制方式：`COPY (SELECT …) TO STDOUT` → 目标库 `COPY … FROM STDIN`，
关掉外键触发器（会话级），复制完把序列推到 max(id)。同库同账号，不依赖 dblink。
"""
from __future__ import annotations

import argparse
import io
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

SCHEMAS = ("cfg", "kb")

# 老库里被改名归档的旧表：新库不需要它，缺了也不算不一致（DR-004 只管在用的配置）。
SKIP_LEGACY = {"kb.spec_legacy"}

BASE = {
    "host": os.environ.get("PGHOST") or "127.0.0.1",
    "port": int(os.environ.get("PGPORT") or 5433),
    "user": os.environ.get("PGUSER") or "kb",
    "password": os.environ.get("PGPASSWORD") or "kb_dev_pwd",
}


def connect(dbname: str):
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(dbname=dbname, autocommit=True, row_factory=dict_row, **BASE)


def tables(conn, schema: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("""select c.relname from pg_class c
                       join pg_namespace n on n.oid = c.relnamespace
                       where n.nspname = %s and c.relkind = 'r'
                       order by c.relname""", (schema,))
        return [r["relname"] for r in cur.fetchall()]


def columns(conn, schema: str, table: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("""select column_name from information_schema.columns
                       where table_schema = %s and table_name = %s
                       order by ordinal_position""", (schema, table))
        return [r["column_name"] for r in cur.fetchall()]


def counts(conn, schema: str, table: str) -> int:
    with conn.cursor() as cur:
        cur.execute('select count(*) as n from "%s"."%s"' % (schema, table))
        return int(cur.fetchone()["n"])


def plan(src_db: str, dst_db: str) -> list[tuple[str, str, int, int]]:
    out = []
    with connect(src_db) as src, connect(dst_db) as dst:
        for schema in SCHEMAS:
            for t in tables(src, schema):
                if t not in tables(dst, schema):
                    if "%s.%s" % (schema, t) in SKIP_LEGACY:
                        continue
                    out.append((schema, t, counts(src, schema, t), -1))
                    continue
                out.append((schema, t, counts(src, schema, t), counts(dst, schema, t)))
    return out


def copy(src_db: str, dst_db: str) -> dict:
    moved = {}
    with connect(src_db) as src, connect(dst_db) as dst:
        with dst.cursor() as cur:
            cur.execute("set session_replication_role = replica")   # 关外键触发器
        for schema in SCHEMAS:
            for t in tables(src, schema):
                if t not in tables(dst, schema):
                    if "%s.%s" % (schema, t) in SKIP_LEGACY:
                        continue
                    print("  跳过 %s.%s：目标库没有这张表（先让服务建库）" % (schema, t))
                    continue
                cols = columns(src, schema, t)
                common = [c for c in cols if c in columns(dst, schema, t)]
                collist = ", ".join('"%s"' % c for c in common)
                with dst.cursor() as cur:
                    cur.execute('delete from "%s"."%s"' % (schema, t))
                with src.cursor() as scur:
                    buf = io.BytesIO()
                    with scur.copy("COPY (SELECT %s FROM \"%s\".\"%s\") TO STDOUT"
                                   % (collist, schema, t)) as cp:
                        for chunk in cp:
                            buf.write(chunk)
                    n = buf.getvalue().count(b"\n")
                    buf.seek(0)
                    with dst.cursor() as dcur:
                        with dcur.copy('COPY "%s"."%s" (%s) FROM STDIN'
                                       % (schema, t, collist)) as cp:
                            while True:
                                data = buf.read(1 << 20)
                                if not data:
                                    break
                                cp.write(data)
                moved["%s.%s" % (schema, t)] = n
        # 序列推到 max(id)，否则新库插入会撞唯一键
        with dst.cursor() as cur:
            for key in moved:
                schema, t = key.split(".")
                if "id" not in columns(dst, schema, t):
                    continue                      # 主键不是 id 的表（k / qid …）不用推序列
                try:
                    cur.execute("""select pg_get_serial_sequence(%s, 'id') as seq""",
                                ('%s.%s' % (schema, t),))
                    row = cur.fetchone()
                except Exception:                 # noqa: BLE001 - 没有序列就跳过
                    continue
                if not row or not row["seq"]:
                    continue
                cur.execute("select coalesce(max(id), 0) as m from \"%s\".\"%s\"" % (schema, t))
                m = int(cur.fetchone()["m"] or 0)
                cur.execute("select setval(%s, %s, %s)", (row["seq"], max(1, m), m > 0))
        with dst.cursor() as cur:
            cur.execute("set session_replication_role = origin")
    return moved


def check(src_db: str, dst_db: str) -> int:
    fails = 0
    rows = plan(src_db, dst_db)
    for schema, t, want, got in rows:
        if got == -1:
            print("FAIL %-28s 目标库没有这张表" % ("%s.%s" % (schema, t)))
            fails += 1
        elif got != want:
            print("FAIL %-28s 源 %d 行 / 目标 %d 行" % ("%s.%s" % (schema, t), want, got))
            fails += 1
    print("\n共 %d 张表；不一致 %d 张" % (len(rows), fails))
    return 0 if not fails else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", default="ad_new")
    ap.add_argument("--to", dest="dst", required=True)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.check:
        return check(a.src, a.dst)
    rows = plan(a.src, a.dst)
    print("=== %s → %s（cfg / kb 两片）===" % (a.src, a.dst))
    for schema, t, want, got in rows:
        print("  %-28s 源 %6d 行%s" % ("%s.%s" % (schema, t), want,
                                      "" if got < 0 else " / 目标 %d 行" % got))
    if a.dry:
        print("\n--dry：未执行")
        return 0
    moved = copy(a.src, a.dst)
    print("\n已复制 %d 张表" % len(moved))
    bad = [k for k, v in moved.items() if v < 0]
    return check(a.src, a.dst)


if __name__ == "__main__":
    sys.exit(main())
