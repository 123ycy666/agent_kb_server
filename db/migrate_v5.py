# -*- coding: utf-8 -*-
"""v5 迁移：28 张表归入 kb/cfg/qa/base，public 留同名兼容视图（幂等）。

判据：
  · 28 张表全部不在 public（public 只剩视图）
  · public 每个原名都是视图，且 pg_relation_is_updatable() 为真（旧代码 INSERT/UPDATE 照跑）
  · 逐表行数与迁移前快照相等
  · ad_wf 库一行不动

    python db/migrate_v5.py            # 迁移
    python db/migrate_v5.py --dry      # 只看计划，不执行
    python db/migrate_v5.py --check    # 只跑断言
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))

URL = os.environ.get('DATABASE_URL_RAW') or 'postgresql://kb:kb_dev_pwd@127.0.0.1:5433/ad_new'

# 表 → 目标 schema（需求定稿 §四 数据归属）
MOVES: dict[str, str] = {}
for _t in ('knowledge_base', 'document', 'document_chunk', 'conflict_record', 'tag'):
    MOVES[_t] = 'kb'
# `howto_task` 不在归位清单里：阶段 2 已把它迁成 kb.spec，原表改名 kb.spec_legacy 归档，
# public 也不再留它的视图（V15：同类配置只留一份在用的真身）。
for _t in ('prompt_text', 'prompt_material', 'caliber_metric', 'caliber_metric_ver',
           'caliber_base', 'complete_rule', 'data_meta_override',
           't_nc_ambiguous', 't_nc_catalog', 't_nc_pinyin', 't_nc_term', 't_nc_typo',
           't_nc_wordlist'):
    MOVES[_t] = 'cfg'
for _t in ('t_chat_session', 't_chat_message', 't_chat_query_trace', 't_chat_trace_step'):
    MOVES[_t] = 'qa'
for _t in ('app_user', 't_user', 't_user_pref', 'async_task', 'audit_log'):
    MOVES[_t] = 'base'


def connect():
    import psycopg
    return psycopg.connect(URL, autocommit=True)


def relkind(cur, schema: str, table: str):
    cur.execute("""select c.relkind from pg_class c join pg_namespace n on n.oid=c.relnamespace
                    where n.nspname=%s and c.relname=%s""", (schema, table))
    row = cur.fetchone()
    return row[0] if row else None


def counts(cur, schema: str, table: str) -> int:
    cur.execute('select count(*) from "%s"."%s"' % (schema, table))
    return cur.fetchone()[0]


def snapshot_counts(cur) -> dict:
    out = {}
    for t, grp in MOVES.items():
        kind = relkind(cur, grp, t)
        if kind in ('r', 'p'):
            out[t] = counts(cur, grp, t)
        elif relkind(cur, 'public', t) == 'r':
            out[t] = counts(cur, 'public', t)
    return out


def plan(cur) -> list[tuple[str, str, str]]:
    """[(表, 动作, 说明)]：move / view / noop。"""
    out = []
    for t, grp in MOVES.items():
        pub = relkind(cur, 'public', t)
        dst = relkind(cur, grp, t)
        if pub == 'r' and dst is None:
            out.append((t, 'move+view', '%s → %s.%s，public 留同名视图' % (t, grp, t)))
        elif pub == 'r' and dst in ('r', 'p'):
            out.append((t, 'conflict', '两边都有实体表，人工确认'))
        elif pub == 'v' and dst in ('r', 'p'):
            out.append((t, 'noop', '已迁移'))
        elif pub is None and dst in ('r', 'p'):
            out.append((t, 'view', '表已在 %s，补建 public 视图' % grp))
        else:
            out.append((t, 'missing', '找不到表'))
    return out


def run(dry=False) -> int:
    with connect() as conn:
        cur = conn.cursor()
        before = snapshot_counts(cur)
        steps = plan(cur)
        print('=== 迁移计划（%d 张表）===' % len(steps))
        for t, act, note in steps:
            print('  %-8s %-24s %s' % (act, t, note))
        if dry:
            print('\n--dry：未执行')
            return 0
        bad = [s for s in steps if s[1] in ('conflict', 'missing')]
        if bad:
            print('\n有冲突/缺表，停止：%s' % bad)
            return 2
        from psycopg import sql as S
        schema_sql = (ROOT / 'db' / 'v5_schema.sql').read_text(encoding='utf-8')
        cur.execute(schema_sql)
        print('  SCHEMA kb/cfg/qa/base 就绪')
        for t, act, note in steps:
            grp = MOVES[t]
            if act.startswith('move'):
                cur.execute(S.SQL('ALTER TABLE public.{} SET SCHEMA {}').format(
                    S.Identifier(t), S.Identifier(grp)))
                print('  MOVE %s → %s' % (t, grp))
            if act in ('move+view', 'view'):
                cur.execute(S.SQL('DROP VIEW IF EXISTS public.{}').format(S.Identifier(t)))
                cur.execute(S.SQL('CREATE VIEW public.{} AS SELECT * FROM {}.{}').format(
                    S.Identifier(t), S.Identifier(grp), S.Identifier(t)))
                print('  VIEW public.%s → %s.%s' % (t, grp, t))
        after = snapshot_counts(cur)
        print('\n=== 行数对照 ===')
        diff = 0
        for t in sorted(before):
            same = before.get(t) == after.get(t)
            diff += 0 if same else 1
            print('  %-22s %8s → %8s %s' % (t, before.get(t), after.get(t),
                                            '' if same else '  ← 不一致'))
        print('\n行数不一致 %d 张' % diff)
        return 0 if diff == 0 else 1


def check() -> int:
    """四条断言，全 PASS 才算迁移成功。"""
    with connect() as conn:
        cur = conn.cursor()
        fails = []

        cur.execute("select nspname from pg_namespace where nspname in ('kb','cfg','qa','base')")
        have = {r[0] for r in cur.fetchall()}
        if have != {'kb', 'cfg', 'qa', 'base'}:
            fails.append('schema 不全：%s' % sorted(have))
        print('%s ① 四个 schema 存在 %s' % ('PASS' if not fails else 'FAIL', sorted(have)))

        pub_tables = [t for t, g in MOVES.items() if relkind(cur, 'public', t) == 'r']
        print('%s ② public 不再有实体表（剩 %d 张）%s'
              % ('PASS' if not pub_tables else 'FAIL', len(pub_tables), pub_tables or ''))
        if pub_tables:
            fails.append('public 仍有实体表 %s' % pub_tables)

        notview, notupd = [], []
        for t, g in MOVES.items():
            if relkind(cur, 'public', t) != 'v':
                notview.append(t)
                continue
            cur.execute("select pg_relation_is_updatable('public.%s'::regclass, true)" % t)
            if not (cur.fetchone()[0] & 8):          # 8 = INSERT
                notupd.append(t)
        print('%s ③ public 每个原名都是视图（缺 %d）%s'
              % ('PASS' if not notview else 'FAIL', len(notview), notview or ''))
        print('%s ④ 视图可 INSERT（不可写 %d 张）%s'
              % ('PASS' if not notupd else 'FAIL', len(notupd), notupd or ''))
        if notview:
            fails.append('缺视图 %s' % notview)
        if notupd:
            fails.append('视图不可写 %s' % notupd)

        # ⑤ 序列跟着走：public 视图插入能拿到 id
        try:
            cur.execute("select nextval(pg_get_serial_sequence('kb.tag','id'))")
            nxt = cur.fetchone()[0]
            print('PASS ⑤ 序列随表迁移（kb.tag 下一个 id=%s）' % nxt)
        except Exception as exc:                     # noqa: BLE001
            print('FAIL ⑤ 序列没跟着走：%s' % exc)
            fails.append('序列未迁移')

        print('\n结论：%s' % ('PASS' if not fails else 'FAIL ' + '; '.join(fails)))
        return 0 if not fails else 1


def fix_shadow(dry=False) -> int:
    """清理「影子表」（2026-11-16 实测踩到的坑）。

    根因：库账号叫 `kb`，默认 search_path 是 `"$user", public`。阶段 1 建出 `kb`
    schema 之后，未加前缀的表名（ORM 模型全是未加前缀的）优先落进 `kb`，
    SQLAlchemy 的 `create_all` 顺手在 `kb` 里建了一批空表，把 `public.<同名视图>`
    遮住 —— 表现为 `prompt_text` 读出 0 行、页面覆盖项全部失效。

    判定：某张表出现在**不属于它**的 schema 里、且没有数据 → 是影子表，删掉。
    真表一定有数据或本来就是空的（`conflict_record` 合法为空，但它的归属就是 kb）。
    """
    from psycopg import sql as S
    with connect() as conn:
        cur = conn.cursor()
        dropped, kept = [], []
        for t, owner in MOVES.items():
            for grp in sorted(set(MOVES.values())):
                if grp == owner or relkind(cur, grp, t) != 'r':
                    continue
                n = counts(cur, grp, t)
                if n:
                    kept.append('%s.%s(%d 行，非空不删)' % (grp, t, n))
                    continue
                print('  DROP 影子表 %s.%s（归属应为 %s，0 行）' % (grp, t, owner))
                if not dry:
                    cur.execute(S.SQL('DROP TABLE {}.{} CASCADE').format(
                        S.Identifier(grp), S.Identifier(t)))
                dropped.append('%s.%s' % (grp, t))
        print('\n删除影子表 %d 张%s' % (len(dropped), ('：%s' % dropped) if dropped else ''))
        if kept:
            print('保留（非空）：%s' % kept)
        return 0


def emit_sql(out: str = '') -> int:
    """把迁移动作打印成**与当前状态无关**的 SQL（线上用容器自带 psql 执行）。

    线上 PG 在容器里、没有对外端口，本机脚本连不上；服务器上也不一定有 python 依赖。
    所以这里不按"当前状态"分支，而是每张表都走同一个受保护的 DO 块：
    表还在 public 才搬，搬完视图统一重建。任何状态跑都安全、可重复跑。

    out：写成文件时用 Python 自己写 UTF-8（**不要用 PowerShell 重定向**，
    它默认 UTF-16，psql 会报 invalid byte sequence 0xff）。
    """
    import io
    buf = io.StringIO()
    buf.write((ROOT / 'db' / 'v5_schema.sql').read_text(encoding='utf-8'))
    for t, grp in MOVES.items():
        buf.write(f"""-- {t} → {grp}
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
              WHERE n.nspname = 'public' AND c.relname = '{t}' AND c.relkind = 'r') THEN
    ALTER TABLE public."{t}" SET SCHEMA {grp};
  END IF;
END $$;
DROP VIEW IF EXISTS public."{t}";
CREATE VIEW public."{t}" AS SELECT * FROM {grp}."{t}";

""")
    # 影子表清理：不属于本 schema 的空表删掉（search_path 踩过的坑，见 fix_shadow）
    for t, owner in MOVES.items():
        for grp in sorted(set(MOVES.values())):
            if grp == owner:
                continue
            buf.write(f"""DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
              WHERE n.nspname = '{grp}' AND c.relname = '{t}' AND c.relkind = 'r')
     AND NOT EXISTS (SELECT 1 FROM {grp}."{t}") THEN
    DROP TABLE {grp}."{t}" CASCADE;
  END IF;
END $$;

""")
    text = buf.getvalue()
    if out:
        Path(out).write_text(text, encoding='utf-8')
        print('已写 %s（%d 字节，UTF-8 无 BOM）' % (out, len(text.encode('utf-8'))))
    else:
        sys.stdout.write(text)
    return 0


if __name__ == '__main__':
    if '--check' in sys.argv:
        sys.exit(check())
    if '--fix-shadow' in sys.argv:
        sys.exit(fix_shadow(dry='--dry' in sys.argv))
    if '--emit-sql' in sys.argv:
        _i = sys.argv.index('--emit-sql')
        _out = sys.argv[_i + 1] if len(sys.argv) > _i + 1 else ''
        sys.exit(emit_sql(_out))
    sys.exit(run(dry='--dry' in sys.argv))
