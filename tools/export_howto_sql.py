# -*- coding: utf-8 -*-
"""把做法库导成一支可直接在线上执行的 SQL（线上镜像不带 tools/ 与 db/）。

用法：
    py -X utf8 tools/export_howto_sql.py [输出路径]     # 默认 dist/howto_seed.sql

导出内容：建表 howto_task → 公开做法库 → document → document_chunk → howto_task 行。
文档/切片不建向量索引（is_retrievable=false），线上按任务名精确取，不依赖 embedding。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))

from app.core.config import settings  # noqa: E402

KB_NAME = '公开做法库'


def q_arr(v) -> str:
    """PostgreSQL 数组字面量：ARRAY['a','b'] / '{}'::text[]。"""
    items = [str(x) for x in (v or [])]
    if not items:
        return "'{}'::text[]"
    return 'ARRAY[' + ', '.join(q(x) for x in items) + ']'


def q(v) -> str:
    """SQL 字面量：字符串转义单引号。"""
    if v is None:
        return 'NULL'
    if isinstance(v, bool):
        return 'TRUE' if v else 'FALSE'
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (dict, list)):
        return q(json.dumps(v, ensure_ascii=False))
    return "'" + str(v).replace("'", "''") + "'"


def main() -> int:
    import psycopg

    out = Path(sys.argv[1] if len(sys.argv) > 1 else (ROOT / 'dist' / 'howto_seed.sql'))
    out.parent.mkdir(parents=True, exist_ok=True)
    dsn = settings.database_url.replace('postgresql+psycopg://', 'postgresql://')
    c = psycopg.connect(dsn)
    cur = c.cursor()

    cur.execute("select id from knowledge_base where name=%s order by id limit 1", (KB_NAME,))
    row = cur.fetchone()
    if not row:
        raise SystemExit('本机没有「%s」，先跑 tools/import_howto.py' % KB_NAME)
    kb_id = row[0]
    cur.execute("""select doc_id, task, product, applies, fmt, outline, length, tone,
                          check_items, version_no
                     from howto_task where enabled order by id""")
    cols = [d[0] for d in cur.description]
    tasks = [dict(zip(cols, r)) for r in cur.fetchall()]

    L = ['-- 办公线做法库（由 tools/export_howto_sql.py 生成，可重复执行）',
         'CREATE TABLE IF NOT EXISTS howto_task (',
         '  id BIGSERIAL PRIMARY KEY,',
         '  doc_id BIGINT NOT NULL REFERENCES document(id) ON DELETE CASCADE,',
         '  task VARCHAR(64) NOT NULL,',
         "  product VARCHAR(16) NOT NULL DEFAULT 'text',",
         "  applies JSONB NOT NULL DEFAULT '[]'::jsonb,",
         "  fmt JSONB NOT NULL DEFAULT '{}'::jsonb,",
         "  outline JSONB NOT NULL DEFAULT '[]'::jsonb,",
         "  length JSONB NOT NULL DEFAULT '{}'::jsonb,",
         "  tone TEXT NOT NULL DEFAULT '',",
         "  check_items JSONB NOT NULL DEFAULT '[]'::jsonb,",
         '  enabled BOOLEAN NOT NULL DEFAULT TRUE,',
         '  version_no INTEGER NOT NULL DEFAULT 1,',
         '  updated_at TIMESTAMPTZ NOT NULL DEFAULT now());',
         'CREATE UNIQUE INDEX IF NOT EXISTS ux_howto_task_task ON howto_task (task);',
         '']

    for t in tasks:
        cur.execute("""select id, filename, file_ext, file_size, visibility, doc_type, tags
                         from document where id=%s""", (t['doc_id'],))
        dcols = [d[0] for d in cur.description]
        doc = dict(zip(dcols, cur.fetchone()))
        cur.execute("""select chunk_index, content, heading_path
                         from document_chunk where doc_id=%s order by chunk_index""",
                    (t['doc_id'],))
        chunks = cur.fetchall()

        L.append('DO $$')
        L.append('DECLARE v_kb bigint; v_doc bigint;')
        L.append('BEGIN')
        L.append("  SELECT id INTO v_kb FROM knowledge_base WHERE name = %s ORDER BY id LIMIT 1;"
                 % q(KB_NAME))
        L.append('  IF v_kb IS NULL THEN')
        L.append('    INSERT INTO knowledge_base (name, description, embedding_model, embedding_dim,'
                 ' chunk_size, chunk_overlap, status, qa_enabled, created_at, updated_at)')
        L.append("    VALUES (%s, '做法库：这类活怎么做（格式/口吻/篇幅/步骤/自检）。' || "
                 "'公开可读，不参与问数检索。', 'manual', 1024, 800, 64, 'active', false,"
                 ' now(), now()) RETURNING id INTO v_kb;' % q(KB_NAME))
        L.append('  END IF;')
        L.append('  DELETE FROM document_chunk WHERE doc_id IN '
                 '(SELECT id FROM document WHERE kb_id=v_kb AND filename=%s);' % q(doc['filename']))
        L.append('  DELETE FROM document WHERE kb_id=v_kb AND filename=%s;' % q(doc['filename']))
        L.append('  INSERT INTO document (kb_id, version_no, is_active, filename, file_ext,'
                 ' file_size, storage_path, content_hash, parse_status, has_conflict, chunk_count,'
                 ' visibility, doc_type, tags, uploaded_by_app, created_at, updated_at)')
        L.append('  VALUES (v_kb, 1, true, %s, %s, %s, %s, %s, %s, false, %s, %s, %s, %s, %s,'
                 ' now(), now()) RETURNING id INTO v_doc;'
                 % (q(doc['filename']), q(doc['file_ext']), q(doc['file_size']), q(''),
                    q('seed'), q('ready'), q(len(chunks)), q(doc['visibility']),
                    q(doc['doc_type']), q_arr(doc['tags'] or []), q('howto')))
        for i, (idx, content, head) in enumerate(chunks):
            L.append('  INSERT INTO document_chunk (doc_id, kb_id, chunk_index, content,'
                     ' heading_path, embedding_model, is_retrievable)'
                     ' VALUES (v_doc, v_kb, %d, %s, %s, %s, false);'
                     % (int(idx) if idx is not None else i, q(content), q(head), q('manual')))
        L.append('  INSERT INTO howto_task (doc_id, task, product, applies, fmt, outline, length,'
                 ' tone, check_items, version_no, updated_at)')
        L.append('  VALUES (v_doc, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s,'
                 ' %s::jsonb, %s, now())'
                 % (q(t['task']), q(t['product']), q(t['applies']), q(t['fmt']), q(t['outline']),
                    q(t['length']), q(t['tone']), q(t['check_items']), q(t['version_no'])))
        L.append('  ON CONFLICT (task) DO UPDATE SET doc_id=excluded.doc_id,'
                 ' product=excluded.product, applies=excluded.applies, fmt=excluded.fmt,'
                 ' outline=excluded.outline, length=excluded.length, tone=excluded.tone,'
                 ' check_items=excluded.check_items,'
                 ' version_no=howto_task.version_no+1, updated_at=now();')
        L.append('END $$;')
        L.append('')

    out.write_text('\n'.join(L), encoding='utf-8')
    print('导出 %d 篇做法 → %s（%.1f KB）' % (len(tasks), out, out.stat().st_size / 1024))
    c.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
