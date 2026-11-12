# -*- coding: utf-8 -*-
"""把「做法」文档导入知识库（2026-11-11 办公线）。

用法：
    py -X utf8 tools/import_howto.py            # 全量导入/更新
    py -X utf8 tools/import_howto.py --list     # 只列已有做法

做的事（幂等，可反复跑）：
    1. 建 howto_task 表（db/howto.sql）
    2. 找/建知识库「公开做法库」（qa_enabled=false，不参与问数检索）
    3. 每篇 md：解析 YAML 头 → document(doc_type='howto', visibility='public') → 切 chunk
    4. 结构化参数写进 howto_task
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))

from app.core.config import settings  # noqa: E402

DOCS = ROOT / 'server' / 'app' / 'secretary' / 'howto_docs'
KB_NAME = '公开做法库'
CHUNK_MAX = 1200


def _dsn() -> str:
    return settings.database_url.replace('postgresql+psycopg://', 'postgresql://')


def _split_chunks(body: str) -> list[tuple[str, str]]:
    """按 ## 小节切，超长再按段落并。返回 [(heading_path, content)]。"""
    parts = re.split(r'\n(?=## )', body.strip())
    out = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        m = re.match(r'##\s+(.+)', part)
        head = m.group(1).strip() if m else ''
        if len(part) <= CHUNK_MAX:
            out.append((head, part))
            continue
        buf = ''
        for seg in part.split('\n\n'):
            if len(buf) + len(seg) > CHUNK_MAX and buf:
                out.append((head, buf.strip()))
                buf = ''
            buf += seg + '\n\n'
        if buf.strip():
            out.append((head, buf.strip()))
    return out or [('', body)]


def _parse(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding='utf-8')
    m = re.match(r'^---\n(.*?)\n---\n(.*)$', text, re.S)
    if not m:
        return {}, text
    meta = yaml.safe_load(m.group(1)) or {}
    return meta, m.group(2).strip()


def main() -> int:
    import psycopg

    conn = psycopg.connect(_dsn())
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute((ROOT / 'db' / 'howto.sql').read_text(encoding='utf-8'))

    emb_model = ('online/%s' % settings.online_embedding_model
                 if settings.model_channel == 'online' else 'local/%s' % settings.local_embedding_model)
    cur.execute("select id from knowledge_base where name=%s and status='active' order by id limit 1",
                (KB_NAME,))
    row = cur.fetchone()
    if row:
        kb_id = row[0]
    else:
        cur.execute("""insert into knowledge_base
            (name, description, embedding_model, embedding_dim, chunk_size, chunk_overlap,
             status, qa_enabled, created_at, updated_at)
            values (%s,%s,%s,%s,800,64,'active',false,now(),now()) returning id""",
                    (KB_NAME, '做法库：这类活怎么做（格式/口吻/篇幅/步骤/自检）。公开可读，'
                              '不参与问数检索，由 howto 工具按任务名精确取。',
                     emb_model, settings.embedding_dim))
        kb_id = cur.fetchone()[0]
    print('知识库 id=%s（%s）' % (kb_id, KB_NAME))

    files = sorted(DOCS.glob('*.md'))
    if '--list' in sys.argv:
        for f in files:
            meta, _ = _parse(f)
            print('  %-24s %s' % (meta.get('task', f.stem), meta.get('product', '')))
        return 0

    done = 0
    for f in files:
        meta, body = _parse(f)
        task = str(meta.get('task') or f.stem).strip()
        data = f.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        cur.execute("""select id from document where kb_id=%s and filename=%s and doc_type='howto'
                        order by id desc limit 1""", (kb_id, f.name))
        row = cur.fetchone()
        chunks = _split_chunks(body)
        if row:
            doc_id = row[0]
            cur.execute("""update document set file_size=%s, content_hash=%s, parse_status='ready',
                             chunk_count=%s, version_no=version_no+1, updated_at=now(),
                             tags=%s where id=%s""",
                        (len(data), sha, len(chunks), [task], doc_id))
            cur.execute('delete from document_chunk where doc_id=%s', (doc_id,))
        else:
            cur.execute("""insert into document
                (kb_id, version_no, is_active, filename, file_ext, file_size, storage_path,
                 content_hash, parse_status, has_conflict, chunk_count, visibility, doc_type,
                 tags, uploaded_by_app, created_at, updated_at)
                values (%s,1,true,%s,'.md',%s,'',%s,'ready',false,%s,'public','howto',%s,'howto',
                        now(),now()) returning id""",
                        (kb_id, f.name, len(data), sha, len(chunks), [task]))
            doc_id = cur.fetchone()[0]
        for i, (head, content) in enumerate(chunks):
            cur.execute("""insert into document_chunk
                (doc_id, kb_id, chunk_index, content, heading_path, embedding_model,
                 is_retrievable)
                values (%s,%s,%s,%s,%s,%s,false)""",
                        (doc_id, kb_id, i, content, head[:500] or None, emb_model))
        cur.execute("""insert into howto_task
            (doc_id, task, product, applies, fmt, outline, length, tone, check_items, updated_at)
            values (%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s::jsonb,now())
            on conflict (task) do update set
                doc_id=excluded.doc_id, product=excluded.product, applies=excluded.applies,
                fmt=excluded.fmt, outline=excluded.outline, length=excluded.length,
                tone=excluded.tone, check_items=excluded.check_items,
                version_no=howto_task.version_no+1, updated_at=now()""",
                    (doc_id, task, str(meta.get('product') or 'text'),
                     _j(meta.get('applies')), _j(meta.get('fmt')), _j(meta.get('outline')),
                     _j(meta.get('length')), str(meta.get('tone') or ''), _j(meta.get('check'))))
        print('  ✓ %-14s %-6s chunks=%d doc_id=%s' % (task, meta.get('product'), len(chunks), doc_id))
        done += 1

    cur.execute('select count(*) from howto_task where enabled')
    print('完成：%d 篇，做法表共 %d 条' % (done, cur.fetchone()[0]))
    conn.close()
    return 0


def _j(v) -> str:
    import json
    return json.dumps(v if v is not None else ({} if isinstance(v, dict) else []),
                      ensure_ascii=False)


if __name__ == '__main__':
    raise SystemExit(main())
