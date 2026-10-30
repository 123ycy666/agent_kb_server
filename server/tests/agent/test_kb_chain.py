# -*- coding: utf-8 -*-
"""知识库配置链路验收（2026-10-30）：上传 → 类型判定 → 规则目录 → get_rule / 个人注入。

为什么要它
----------
`test_rule_stack` 是"直接往库里写行"验规则读写；本文件走**真实入库流程**
（`ingestion.pipeline.ingest_document`：落盘 → 解析 → 切片 → 向量化 → 落库），
确认真实上传的文档会被判成 `rule`，并且能被规则目录与 `get_rule` 取到。

钉死四条
--------
1. 文件名像"规则"的口径文档 → `document.doc_type='rule'`。
2. 文件名不像的（资料）→ `doc_type='doc'`。
3. 系统库里的规则文档出现在「规则目录」，`get_rule(主题)` 能取到正文。
4. 同一个人的个人库里放一份规则 → `personal_rules()` 注入；别人看不到。

⚠️ 会真的调一次 embedding 接口（每个文档 1~2 次），跑完自动清理。
运行：py -X utf8 server/tests/agent/test_kb_chain.py
"""
from __future__ import annotations

import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SERVER))

_checks: list[tuple[str, bool, str]] = []
TAG = 'zz-chain-20261030'
KB_NAME = 'zz链路测试库-20261030'
U = 904


def chk(label: str, cond: bool, extra: str = '') -> None:
    _checks.append((label, bool(cond), extra))


def _cleanup() -> None:
    from app.secretary import pgdb
    c = pgdb.connect(autocommit=True)
    try:
        cur = c.cursor()
        cur.execute("DELETE FROM document_chunk WHERE kb_id IN (SELECT id FROM knowledge_base "
                    "WHERE name=%s OR (name='我的资料库' AND created_by=%s))", (KB_NAME, U))
        cur.execute("DELETE FROM document WHERE content_hash LIKE %s", (TAG + '%',))
        cur.execute("DELETE FROM knowledge_base WHERE name=%s OR "
                    "(name='我的资料库' AND created_by=%s)", (KB_NAME, U))
        cur.close()
    finally:
        c.close()


def main() -> int:
    from app.core.database import SessionLocal
    from app.core.deps import CurrentActor
    from app.models import Document, KnowledgeBase
    from app.secretary import personal_kb, pgdb, rule_doc
    from app.services.ingestion import pipeline

    print('知识库配置链路验收（真上传）')
    _cleanup()

    # 建系统测试库
    c = pgdb.connect(autocommit=True)
    cur = c.cursor()
    cur.execute("INSERT INTO knowledge_base (name, description, embedding_model, embedding_dim, "
                "chunk_size, chunk_overlap, status, created_by, qa_enabled) "
                "VALUES (%s,'链路测试','x',1024,800,100,'active',%s,false)", (KB_NAME, U))
    cur.execute("SELECT id FROM knowledge_base WHERE name=%s LIMIT 1", (KB_NAME,))
    kb_id = int(cur.fetchone()['id'])
    cur.close()
    c.close()

    actor = CurrentActor(type='user', user_id=U, username='chain_tester',
                         display_name='链路测试', role_codes=['user'])
    db = SessionLocal()
    try:
        kb = db.get(KnowledgeBase, kb_id)
        body_rule = ('# 链路测试规则\n\n## 审批停留\n审批停留超过 90 天算积压（暗号 K9）。\n')
        body_doc = '# 普通资料\n\n## 概述\n这只是一份资料（暗号 D9）。\n'
        r1 = pipeline.ingest_document(
            db, kb=kb, filename='链路测试规则.md', content=body_rule.encode('utf-8'),
            actor=actor)
        r2 = pipeline.ingest_document(
            db, kb=kb, filename='链路测试资料.md', content=body_doc.encode('utf-8'),
            actor=actor)
        d1 = db.get(Document, r1.doc_id)
        d2 = db.get(Document, r2.doc_id)
        chk('规则文件名 → doc_type=rule', d1.doc_type == 'rule', str(d1.doc_type))
        chk('资料文件名 → doc_type=doc', d2.doc_type == 'doc', str(d2.doc_type))
        chk('规则文档解析出切片', (d1.chunk_count or 0) > 0, str(d1.chunk_count))

        rule_doc._CACHE['rows'] = None
        topics = rule_doc.topics(force=True)
        names = [t['topic'] for t in topics]
        chk('规则目录含上传的主题', any('审批停留' in n for n in names), str(names[:6]))
        got = rule_doc.get('审批停留', force=True)
        chk('get_rule 取到正文', 'K9' in (got.get('text') or ''), str(got)[:120])

        # 个人库链路
        kid = personal_kb.ensure(U)
        pkb = db.get(KnowledgeBase, kid)
        pipeline.ingest_document(db, kb=pkb, filename='我的口径规则.md',
                                 content=('# 我的口径\n\n## 线损口径\n'
                                          '本所线损率一律用台区线损（暗号 P9）。\n').encode('utf-8'),
                                 actor=actor)
        pr = personal_kb.personal_rules(U)
        chk('个人库规则被注入', 'P9' in (pr.get('text') or ''), str(pr)[:120])
        chk('个人注入标注 S7', 'S7 我的规则' in (pr.get('text') or ''))
        other = personal_kb.personal_rules(U + 1)
        chk('别人看不到我的规则', 'P9' not in (other.get('text') or ''), str(other)[:80])

        docs = personal_kb.list_docs(U)
        chk('个人库列表带类型', any(d.get('type') == 'rule' for d in docs), str(docs[:1]))
    finally:
        db.close()
        _cleanup()

    bad = [x for x in _checks if not x[1]]
    for label, ok, extra in _checks:
        print(('  [OK]   ' if ok else '  [FAIL] ') + label
              + (('   ← ' + extra) if (not ok and extra) else ''))
    print('\n%d 项检查，%d 项失败' % (len(_checks), len(bad)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
