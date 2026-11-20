# -*- coding: utf-8 -*-
"""表 → schema 归位（cfg / kb / qa / base）与 public 兼容视图。

为什么要有这一层：一个现场 = 一个 PG 库，四片按用途分开（DR-001）。
但 ORM 模型的表名不带 schema 前缀、连接又固定 `search_path=public`，
所以实体表搬到各片之后，public 必须留一张**同名可写视图**，旧代码一行不改就能跑。

唯一事实来源：`db/migrate_v5.py` 从这里取 MOVES，不再自己维护第二份映射。
入参统一是 **psycopg 连接**（autocommit），内部自己开游标。
"""
from __future__ import annotations

import re

from psycopg import sql as S

SCHEMAS = ("cfg", "kb", "qa", "base")

# 表名 → 目标 schema。新增表要归位时只改这里。
MOVES: dict[str, str] = {}
for _t in ("knowledge_base", "document", "document_chunk", "conflict_record", "tag"):
    MOVES[_t] = "kb"
for _t in ("prompt_text", "prompt_material", "caliber_metric", "caliber_metric_ver",
           "caliber_base", "complete_rule", "data_meta_override",
           "t_nc_ambiguous", "t_nc_catalog", "t_nc_pinyin", "t_nc_term", "t_nc_typo",
           "t_nc_wordlist"):
    MOVES[_t] = "cfg"
for _t in ("t_chat_session", "t_chat_message", "t_chat_query_trace", "t_chat_trace_step"):
    MOVES[_t] = "qa"
for _t in ("app_user", "t_user", "t_user_pref", "async_task", "audit_log"):
    MOVES[_t] = "base"


def relkind(conn, schema: str, table: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute("""select c.relkind from pg_class c
                       join pg_namespace n on n.oid = c.relnamespace
                       where n.nspname = %s and c.relname = %s""", (schema, table))
        row = cur.fetchone()
    return row[0] if row else None


def apply_extra(conn, ddl_list) -> list[str]:
    """在「已有库」上跑增量 DDL（EXTRA_DDL）。

    ★ 为什么需要：`db_init.ensure_extra_columns()` 用 public 的 search_path 跑未加前缀的
      `ALTER TABLE document …`，而迁移后 public 里是同名**视图** → 每次启动都报
      「cannot add column to view」，等于增量迁移在老库上彻底失效。
      这里把 search_path 设成 `cfg,kb,qa,base,public`：未加前缀的表名先落到真身 schema，
      找不到才落 public —— 迁移前/迁移后都能跑对。
    """
    done, failed = [], []
    with conn.cursor() as cur:
        cur.execute("select current_setting('search_path')")
        row = cur.fetchone()
        old = str(row[0]) if row else "public"
        cur.execute("set search_path to cfg, kb, qa, base, public")
        for label, sql in ddl_list:
            try:
                cur.execute(sql)
                done.append(label)
            except Exception as exc:                  # noqa: BLE001
                failed.append("%s: %s" % (label, str(exc)[:80]))
        cur.execute("set search_path to " + "".join(ch for ch in old if ch.isalnum() or ch in ",_ "))
    for f in failed:
        print("[schema] 增量 DDL 失败：%s" % f, flush=True)
    return done


def ensure_schemas(conn) -> None:
    with conn.cursor() as cur:
        for s in SCHEMAS:
            cur.execute(S.SQL("create schema if not exists {}").format(S.Identifier(s)))


# 不带 schema 前缀、也不在 ORM 里的表：建在 public（随后由 migrate 归位）。
# 内容一律取自仓库里的结构 SQL，不在代码里抄第二份 DDL。
_STRUCT_FILES = ("db/chat_tables.sql", "db/user_pref.sql")

_IMPORT_INSERT = re.compile(r"(?im)^\s*insert\s+into")


def _ddl_only(dump: str) -> str:
    """从 pg_dump 里只取建表结构：丢掉 DROP / SET / OWNER / 数据段。

    ⚠️ 必须按**语句**过滤，不能按行 —— `CREATE TABLE public.x (` 的列定义都在
       接下来的行里，按行筛会把表体砍掉（实测就是这么坏掉的）。
    """
    # 只剥掉 `COPY … FROM stdin;` 到结束符 `\.` 之间的数据段，
    # 保留后面的 post-data（PRIMARY KEY / CREATE INDEX）—— 否则新库的词典表没有主键与索引。
    kept, in_copy = [], False
    for line in dump.splitlines():
        if in_copy:
            if line.strip() == "\\.":
                in_copy = False
            continue
        if line.startswith("COPY "):
            in_copy = True
            continue
        kept.append(line)
    head = "\n".join(kept)
    stmts = []
    for raw in head.split(";\n"):
        lines = [ln for ln in raw.splitlines() if not ln.strip().startswith("--")]
        stmt = "\n".join(lines).strip()
        if not stmt:
            continue
        if " OWNER TO " in stmt:
            continue
        if not stmt.startswith(("CREATE ", "ALTER ", "COMMENT ON ")):
            continue
        stmts.append(stmt + ";")
    return "\n".join(stmts) + ("\n" if stmts else "")


def init_tables(conn) -> list[str]:
    """空库补齐「非 ORM 的表」：对话记录表、个人偏好、指标口径三张。

    `db/caliber.sql` 里 DDL 与初始口径混在一份文件，这里只取 INSERT 之前的 DDL
    —— 空现场不许有任何业务内容（AC-03）。
    """
    from pathlib import Path

    # 结构 SQL 的位置：源码树是 <项目根>/db；容器里 <项目根> 是 /（main.py 的 parents 算法），
    # 所以额外认 /db 与 /app/db 两个候选 —— 否则容器里的空现场建不出这几张表。
    here = Path(__file__).resolve()
    roots = [here.parents[3], Path("/"), Path("/app"), here.parents[2]]
    root = next((r for r in roots if (r / "db" / "chat_tables.sql").is_file()), roots[0])
    acts: list[str] = []
    for rel in _STRUCT_FILES:
        p = root / rel
        if p.is_file():
            conn.execute(p.read_text(encoding="utf-8"))
            acts.append(rel)
    # 纠错词典 6 张表（t_nc_*）：db/lexicon.sql 是「结构 + 词典数据」的 pg_dump，
    # 空现场只取 **COPY 之前的结构 DDL**，词典内容由现场自己导入（不跟着骨架走）。
    lexicon = root / "db" / "lexicon.sql"
    if lexicon.is_file():
        ddl = _ddl_only(lexicon.read_text(encoding="utf-8"))
        if ddl.strip():
            conn.execute(ddl)
            acts.append("db/lexicon.sql（仅 DDL）")
    caliber = root / "db" / "caliber.sql"
    if caliber.is_file():
        ddl = _IMPORT_INSERT.split(caliber.read_text(encoding="utf-8"))[0]
        conn.execute(ddl)
        acts.append("db/caliber.sql（仅 DDL）")
    return acts


def migrate(conn) -> list[str]:
    """public 里的实体表搬进各自 schema，并重建同名可写视图。幂等。

    只搬 `relkind='r'`（实体表）；已经是视图的不动，只刷新。
    """
    acts: list[str] = []
    ensure_schemas(conn)
    with conn.cursor() as cur:
        for table, grp in MOVES.items():
            cur.execute("""select c.relkind from pg_class c
                           join pg_namespace n on n.oid = c.relnamespace
                           where n.nspname = %s and c.relname = %s""", ("public", table))
            r = cur.fetchone()
            pub = r[0] if r else None
            cur.execute("""select c.relkind from pg_class c
                           join pg_namespace n on n.oid = c.relnamespace
                           where n.nspname = %s and c.relname = %s""", (grp, table))
            r = cur.fetchone()
            dst = r[0] if r else None
            if pub == "r" and dst is None:
                cur.execute(S.SQL("alter table public.{} set schema {}").format(
                    S.Identifier(table), S.Identifier(grp)))
                acts.append("move %s → %s" % (table, grp))
                pub, dst = None, "r"
            if pub != "r" and dst in ("r", "p"):
                cur.execute(S.SQL("create or replace view public.{} as select * from {}.{}").format(
                    S.Identifier(table), S.Identifier(grp), S.Identifier(table)))
                if pub is None or pub == "v":
                    acts.append("view public.%s" % table)
    return acts


def missing_schemas(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("select nspname from pg_namespace where nspname = any(%s)",
                    (list(SCHEMAS),))
        got = {r[0] for r in cur.fetchall()}
    return [s for s in SCHEMAS if s not in got]
