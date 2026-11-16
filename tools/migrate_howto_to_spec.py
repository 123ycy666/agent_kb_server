# -*- coding: utf-8 -*-
"""把旧「做法库」（howto_task + document）迁成 kb.spec，旧表改名留档（V15：不留第二份真身）。

    python tools/migrate_howto_to_spec.py --dry
    python tools/migrate_howto_to_spec.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
os.chdir(ROOT / 'server')

import psycopg                                     # noqa: E402
from psycopg.rows import dict_row                  # noqa: E402

DSN = 'postgresql://kb:kb_dev_pwd@127.0.0.1:5433/ad_new'


def q(sql, params=None):
    with psycopg.connect(DSN, autocommit=True, row_factory=dict_row) as c:
        cur = c.execute(sql, params or ())
        return cur.fetchall() if cur.description else []


def main(dry=False) -> int:
    from app.secretary import spec as SP
    SP.ensure()
    rows = q("""select t.id, t.doc_id, t.task, t.product, t.applies, t.fmt, t.outline,
                       t.length, t.tone, t.check_items, t.enabled, t.version_no,
                       d.filename, d.doc_type, d.owner_id
                  from kb.howto_task t left join kb.document d on d.id = t.doc_id
                 order by t.id""")
    print('旧做法 %d 条' % len(rows))
    done = 0
    for r in rows:
        name = str(r['task'] or '').strip()
        if not name:
            continue
        body = ''
        chunks = q("select content from kb.document_chunk where doc_id=%s order by chunk_index",
                   (r['doc_id'],))
        body = '\n'.join(c['content'] for c in chunks)
        role = 'koujing' if (r['product'] or '') == '' else (
            'taizhang' if r['product'] == 'xlsx' else (
                'kejian' if r['product'] == 'pptx' else 'gongwen'))
        checks = r['check_items'] or []
        exists = q("select id from kb.spec where name=%s and space='spec' and enabled", (name,))
        if exists:
            print('  跳过（已有同名）：%s' % name)
            continue
        if dry:
            print('  将迁：%s → role=%s 产物=%s 检查项=%d 正文=%d 字'
                  % (name, role, r['product'], len(checks), len(body)))
            continue
        row = q("""insert into kb.spec (name, role_code, space, when_to_use, applies, product,
                                        owner_id, status, body, check_items, outline, fmt,
                                        doc_id, version_no, created_by, updated_by,
                                        published_at)
                   values (%s,%s,'spec',%s,%s::jsonb,%s,%s,'published',%s,%s::jsonb,
                           %s::jsonb,%s::jsonb,%s,%s,'migrate','migrate',now())
                   returning id""",
                (name, role, str(r['tone'] or ''), json.dumps(r['applies'] or [], ensure_ascii=False),
                 r['product'] or '', r['owner_id'], body,
                 json.dumps(checks, ensure_ascii=False),
                 json.dumps(r['outline'] or [], ensure_ascii=False),
                 json.dumps(r['fmt'] or {}, ensure_ascii=False),
                 r['doc_id'], int(r['version_no'] or 1)))
        sid = row[0]['id']
        q("insert into kb.spec_version (spec_id, version_no, name, body, meta, saved_by) "
          "values (%s,%s,%s,%s,%s::jsonb,'migrate') on conflict do nothing",
          (sid, int(r['version_no'] or 1), name, body,
           json.dumps({'from': 'howto_task', 'old_id': r['id']}, ensure_ascii=False)))
        # 说明书不进检索（V17）
        if r['doc_id']:
            q("update kb.document_chunk set is_retrievable=false where doc_id=%s", (r['doc_id'],))
        done += 1
        print('  迁：%s → #%s' % (name, sid))
    SP.invalidate()
    legacy = q("select count(*) as c from pg_class c join pg_namespace n on n.oid=c.relnamespace "
               "where n.nspname='kb' and c.relname='spec_legacy'")
    if not dry and legacy and legacy[0]['c'] == 0:
        q('ALTER TABLE kb.howto_task RENAME TO spec_legacy')
        q("CREATE VIEW public.howto_task_legacy AS SELECT * FROM kb.spec_legacy")
        q("DROP VIEW IF EXISTS public.howto_task")
        print('旧表 kb.howto_task 已改名 kb.spec_legacy（V15：不留第二份在用）')
    print('迁移 %d 条' % done)
    return 0


if __name__ == '__main__':
    sys.exit(main(dry='--dry' in sys.argv))
