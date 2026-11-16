# -*- coding: utf-8 -*-
"""现状巡检：一次看清分组、行数、配置行、字典表清单。

    python db/pg_inspect.py            # 全部
    python db/pg_inspect.py tables     # 只看分组与行数
    python db/pg_inspect.py cfg        # 只看配置行（提示词/材料/口径/补全表/字典表）
    python db/pg_inspect.py kb         # 只看知识库与说明书
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import psycopg                                    # noqa: E402
from psycopg.rows import dict_row                 # noqa: E402

DSN = 'postgresql://kb:kb_dev_pwd@127.0.0.1:5433/ad_new'


def q(sql, params=None):
    with psycopg.connect(DSN, row_factory=dict_row) as c:
        return c.execute(sql, params or ()).fetchall()


def tables():
    print('== schema 分组 ==')
    for r in q("""select table_schema, count(*) as n from information_schema.tables
                  where table_schema not in ('pg_catalog','information_schema')
                  group by 1 order by 1"""):
        print('  %-10s %d' % (r['table_schema'], r['n']))
    print('== 表与行数 ==')
    for r in q("""select n.nspname as sch, c.relname as t, c.relkind as k
                    from pg_class c join pg_namespace n on n.oid=c.relnamespace
                   where c.relkind in ('r','v') and n.nspname not in
                         ('pg_catalog','information_schema') order by 1,2"""):
        if r['k'] != 'r':
            continue
        n = q('select count(*) as c from "%s"."%s"' % (r['sch'], r['t']))[0]['c']
        print('  %-6s %-24s %6d' % (r['sch'], r['t'], n))


def cfg():
    print('== prompt_text ==')
    for r in q("select k, length(body) as n, version_no, coalesce(updated_by,'') as by "
               "from prompt_text order by k"):
        print('  %-14s %6d 字 v%s %s' % (r['k'], r['n'], r['version_no'], r['by']))
    print('== prompt_material ==')
    for r in q("select id, slot, section, length(body) as n, sort_no, enabled, pinned "
               "from prompt_material order by slot, sort_no"):
        print('  #%-3d %-10s %-22s %6d 字 sort=%s 启用=%s pinned=%s'
              % (r['id'], r['slot'], (r['section'] or '')[:22], r['n'], r['sort_no'],
                 r['enabled'], r['pinned']))
    print('== caliber_metric ==')
    for r in q("select id, code, label, unit, enabled, version_no from caliber_metric order by id"):
        print('  %s' % r)
    print('== caliber_base ==')
    for r in q("select * from caliber_base order by id"):
        print('  %s' % r)
    print('== complete_rule ==')
    for r in q("select id, kind, match_text, fill_text, priority, enabled "
               "from complete_rule order by id"):
        print('  %s' % r)
    print('== data_meta_override: %s 行 ==' % q("select count(*) as c from data_meta_override")[0]['c'])
    print('== 词典表 ==')
    for t in ('t_nc_catalog', 't_nc_pinyin', 't_nc_term', 't_nc_typo', 't_nc_wordlist',
              't_nc_ambiguous'):
        print('  %-16s %6d' % (t, q('select count(*) as c from public.%s' % t)[0]['c']))


def kb():
    print('== knowledge_base ==')
    for r in q("select id, name, status, qa_enabled from knowledge_base order by id"):
        print('  %s' % r)
    print('== document ==')
    for r in q("select id, kb_id, filename, doc_type, is_active, chunk_count, visibility, "
               "owner_id from document order by id"):
        print('  %s' % r)
    print('== 旧做法库（已改名 kb.spec_legacy）==')
    try:
        for r in q("select id, doc_id, task, product, enabled from kb.spec_legacy order by id"):
            print('  %s' % r)
    except Exception as exc:                       # noqa: BLE001
        print('  没有归档表：%s' % str(exc).splitlines()[0])
    print('== app_user ==')
    for r in q("select id, username, display_name, role, status from app_user"):
        print('  %s' % r)
    print('== 说明书（新表）==')
    try:
        for r in q("select id, name, role_code, space, status, owner_id, version_no "
                   "from kb.spec order by id"):
            print('  %s' % r)
    except Exception as exc:                       # noqa: BLE001
        print('  还没建：%s' % str(exc).splitlines()[0])


if __name__ == '__main__':
    what = sys.argv[1] if len(sys.argv) > 1 else 'all'
    if what in ('all', 'tables'):
        tables()
    if what in ('all', 'cfg'):
        cfg()
    if what in ('all', 'kb'):
        kb()
