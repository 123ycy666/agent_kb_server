# -*- coding: utf-8 -*-
"""说明书层（阶段 2）：配文档即加能力。

一篇文章 = 一类活怎么做（什么时候用 / 适用 / 产物 / 归属 / 步骤 / 检查项 / 版式）。
上一代做法库（`howto_task` INNER JOIN `document`）的两个病根在这里一并去掉：
  · 目录要参数行 + 文档行同时在才进得去 → 现在正文就存 `kb.spec.body`，读正文不 join
  · 参数只能由脚本从 YAML 头灌 → 现在上传后页面填四项，填完就能发布

判定优先级（需求 H4.4）：用户点名 > 说明书点名 > 本人 > 全局 > 管理员默认 > 最新版。
归属隔离（H8.4）：目录按身份过滤、取正文二次校验、检索按权限范围。
生效时延（H7.3 / H8.5）：发布即 invalidate，别的进程最多 60 秒（_TTL）。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from psycopg.rows import dict_row

from app.secretary import pgdb

_TTL = 60
_CACHE: dict = {'rows': None, 'at': 0.0}
_READY = False
DDL_PATH = Path(__file__).resolve().parents[3] / 'db' / 'spec_schema.sql'
_REF = re.compile(r'《([^》\n]{1,60})》')

FOUR = ('什么时候用', '适用', '产物', '归属')


def _q(sql, params=None, write=False):
    import psycopg
    with psycopg.connect(pgdb.DSN, autocommit=True, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchall() if cur.description else []


def ensure() -> None:
    """建表（幂等）。测试与首次启动都靠它，不用先手工跑 SQL。"""
    global _READY
    if _READY:
        return
    if not _q("select 1 from pg_class c join pg_namespace n on n.oid=c.relnamespace "
              "where n.nspname='kb' and c.relname='spec'"):
        _q(DDL_PATH.read_text(encoding='utf-8'))
    _READY = True


def invalidate() -> None:
    _CACHE['rows'] = None
    _CACHE['at'] = 0.0


# ── 目录 ──────────────────────────────────────────────────────────────
def _rows() -> list[dict]:
    now = time.time()
    hit = _CACHE['rows']
    if hit is not None and now - _CACHE['at'] < _TTL:
        return hit
    try:
        rows = _q("""select id, name, role_code, space, when_to_use, applies, product,
                            owner_id, status, body, check_items, outline, fmt, doc_id,
                            storage_path, version_no, conflict_with, enabled, updated_by,
                            published_at, updated_at
                       from kb.spec where enabled and status = 'published'
                      order by owner_id nulls last, updated_at desc""")
    except Exception as exc:                       # noqa: BLE001 - 读不到按无说明书
        print('[spec] 读说明书失败（按无说明书继续）：%s: %s'
              % (type(exc).__name__, str(exc)[:120]), flush=True)
        rows = []
    _CACHE['rows'] = rows
    _CACHE['at'] = now
    return rows


def _mine(row, user_id) -> bool:
    """归属：全局（owner_id 空）或本人。别人私有的一律不算可见。"""
    return row.get('owner_id') is None or str(row.get('owner_id')) == str(user_id)


def visible(user_id, space='spec') -> list[dict]:
    ensure()
    return [r for r in _rows() if r.get('space') == space and _mine(r, user_id)]


def _line(row) -> str:
    bits = [row['name'], ' '.join(str(row.get('when_to_use') or '').split())[:40],
            row.get('product') or '无产物']
    conf = row.get('conflict_with') or []
    if conf:
        bits.append('⚠ 与《%s》同时适用' % '》《'.join(str(c) for c in conf))
    return '- ' + ' · '.join(b for b in bits if b)


def menu_rows(user_id) -> list[dict]:
    out = []
    for r in visible(user_id, 'spec'):
        out.append({'id': r['id'], 'name': r['name'], 'line': _line(r),
                    'when_to_use': r.get('when_to_use') or '',
                    'product': r.get('product') or '', 'role_code': r.get('role_code') or '',
                    'version_no': r.get('version_no'), 'owner': '本人' if r.get('owner_id') else '全局',
                    'conflict': bool(r.get('conflict_with'))})
    return out


def menu_text(user_id, limit=30) -> str:
    """目录：一行一篇（名称 · 什么时候用 · 产物），超 limit 折叠成一行提示。"""
    rows = menu_rows(user_id)
    if not rows:
        return ''
    lines = [r['line'] for r in rows[:limit]]
    if len(rows) > limit:
        lines.append('- ……另有 %d 篇（说名字可取，如《%s》）'
                     % (len(rows) - limit, rows[limit]['name']))
    return '\n'.join(lines)


# ── 选取 ──────────────────────────────────────────────────────────────
def pick(user_id, name='', question='') -> dict:
    """按优先级挑一篇：点名 > 适用词命中 > 本人 > 全局 > 最新版。取不到返回 {}。

    applies 是**数据**（上传时页面填的词表），不是代码里的业务词规则。
    """
    items = visible(user_id, 'spec')
    name = (name or '').strip()
    if name:
        cand = [r for r in items if r['name'] == name]
        if not cand:
            cand = [r for r in items if name in r['name'] or r['name'] in name]
        if cand:
            cand.sort(key=lambda r: (r.get('owner_id') is None, -(r.get('version_no') or 1)))
            return cand[0]
    q = str(question or '')
    if q:
        best, hit = None, 0
        for r in items:
            n = sum(1 for w in (r.get('applies') or []) if w and str(w) in q)
            if n > hit or (n == hit and n and r.get('owner_id') and
                           best is not None and not best.get('owner_id')):
                best, hit = r, n
        if best:
            return best
    return {}


def load(user_id, name='', question='', spec_id=None) -> dict:
    """取一篇说明书：{item, body, refs, conflicts}。取不到返回 {}。

    取正文**二次校验归属**（H8.4）——目录过滤过一遍，这里再校验一遍，
    防止拿着 id 直取别人的私有说明书。
    """
    ensure()
    item = {}
    if spec_id:
        row = _q("select * from kb.spec where id=%s and enabled and status='published'",
                 (spec_id,))
        item = row[0] if row and _mine(row[0], user_id) else {}
    if not item:
        item = pick(user_id, name=name, question=question)
    if not item:
        return {}
    body = item.get('body') or ''
    refs = resolve_refs(user_id, item)
    same = [r for r in visible(user_id, 'spec')
            if r['id'] != item['id'] and r.get('role_code')
            and r.get('role_code') == item.get('role_code')
            and str(r.get('product') or '') == str(item.get('product') or '')]
    return {'item': item, 'body': body, 'refs': refs,
            'conflicts': [{'id': r['id'], 'name': r['name'], 'owner': '本人' if r.get('owner_id') else '全局'}
                          for r in same]}


# ── 引用 ──────────────────────────────────────────────────────────────
def resolve_refs(user_id, item) -> list[dict]:
    """正文里的《书名号》→ 可顺着读的说明书（V8）。解析不到标 missing。"""
    names = []
    for m in _REF.finditer(item.get('body') or ''):
        n = m.group(1).strip()
        if n and n not in names:
            names.append(n)
    out = []
    for n in names[:20]:
        hit = pick(user_id, name=n)
        out.append({'name': n, 'spec_id': hit.get('id') if hit.get('name') == n else None,
                    'resolved': bool(hit.get('name') == n)})
    return out


# ── 上传 / 分类 / 发布 ────────────────────────────────────────────────
def _ingest(filename, data, actor, *, retrievable: bool):
    """走真正的知识库入库通道（切片 + 向量）。

    · 资料（retrievable=True）：进 KB、可被 kb_search 检索到（V12）
    · 说明书（retrievable=False）：同样入库留档、原文件可下载，但切片标不可检索（V17）

    返回 (doc_id, storage_path, text)。
    """
    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.models import KnowledgeBase
    from app.services.ingestion import ingest_document
    from app.services.ingestion.parsers import ParsedDocument  # noqa: F401

    db = SessionLocal()
    try:
        kb = db.execute(select(KnowledgeBase).where(KnowledgeBase.status == 'active')
                        .order_by(KnowledgeBase.id)).scalars().first()
        if kb is None:
            return None, '', ''
        try:
            res = ingest_document(db, kb=kb, filename=filename, content=data, actor=actor,
                                  is_public=False)
        except Exception as exc:                   # noqa: BLE001
            # 同名同内容再上传：入库层会拒（SHA256 一致）。按已有文档复用 ——
            # 需求 H9.1 说同名上传=新版本，不该因为内容没变就传不上去。
            existing = getattr(exc, 'existing_doc_id', None)
            if not existing:
                raise
            doc_id = int(existing)
            rows = _q("select content from kb.document_chunk where doc_id=%s "
                      "order by chunk_index", (doc_id,))
            if not retrievable:
                _q("update kb.document_chunk set is_retrievable=false where doc_id=%s",
                   (doc_id,))
            return doc_id, '', '\n'.join(r['content'] for r in rows)
        doc_id = getattr(res, 'document_id', None) or getattr(res, 'doc_id', None)
        path = getattr(res, 'storage_path', '') or ''
        text = getattr(res, 'full_text', '') or ''
        if not text:
            rows = _q("select content from kb.document_chunk where doc_id=%s order by chunk_index",
                      (doc_id,))
            text = '\n'.join(r['content'] for r in rows)
        if doc_id and not retrievable:
            _q("update kb.document_chunk set is_retrievable=false where doc_id=%s", (doc_id,))
        return doc_id, str(path), text
    except Exception as exc:                       # noqa: BLE001 - 入库失败退到附件通道
        print('[spec] 入库失败（退到附件通道）：%s: %s'
              % (type(exc).__name__, str(exc)[:160]), flush=True)
        return None, '', ''
    finally:
        db.close()


def upload(user_id, filename, data, *, role_code='', space='', meta=None,
           user_code='', actor=None) -> dict:
    """上传即分类：

      · 选了角色 → 说明书库，进「待发布」，预览切片与目录行后确认发布
      · 没选角色 → 资料库，不进目录，只可检索（V12）
      · 同名同归属 → 新版本，旧版退役（H9.1）
    """
    ensure()
    role = (role_code or '').strip()
    is_spec = bool(role) and space != 'material'
    doc_id = storage = body = None
    if actor is not None:
        doc_id, storage, body = _ingest(filename, data, actor, retrievable=not is_spec)
    if body is None:
        from app.secretary import doc_files
        meta_file = doc_files.save(user_id, filename, data, user_code=user_code)
        storage = str(meta_file.get('path') or '')
        try:
            body = doc_files.plain_text(meta_file['file_id'], user_id) or ''
        except Exception as exc:                   # noqa: BLE001 - 解析失败按空正文
            print('[spec] 取正文失败：%s: %s' % (type(exc).__name__, exc), flush=True)
            body = ''
    body = body or ''
    m = dict(meta or {})
    name = str(m.get('name') or Path(filename).stem)[:120]
    owner = m.get('owner_id', user_id)
    ver = 1
    if is_spec:
        prev = _q("""select id, version_no from kb.spec
                      where name=%s and coalesce(owner_id,0)=coalesce(%s,0) and enabled
                      order by version_no desc limit 1""", (name, owner))
        if prev:
            ver = int(prev[0]['version_no']) + 1
            _q("update kb.spec set enabled=false, status='retired', updated_at=now() "
               "where id=%s", (prev[0]['id'],))
    row = _q("""insert into kb.spec
                (name, role_code, space, when_to_use, applies, product, owner_id, status,
                 body, check_items, outline, fmt, doc_id, storage_path, version_no,
                 created_by, updated_by)
                values (%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,
                        %s,%s,%s,%s,%s)
                returning id""",
             (name, role, 'spec' if is_spec else 'material',
              str(m.get('when_to_use') or ''),
              json.dumps(m.get('applies') or [], ensure_ascii=False),
              str(m.get('product') or ''), owner,
              'pending' if is_spec else 'material',
              body,
              json.dumps(m.get('check_items') or [], ensure_ascii=False),
              json.dumps(m.get('outline') or [], ensure_ascii=False),
              json.dumps(m.get('fmt') or {}, ensure_ascii=False),
              doc_id, storage, ver, str(user_code or ''), str(user_code or '')))
    spec_id = row[0]['id']
    _q("insert into kb.spec_version (spec_id, version_no, name, body, meta, saved_by) "
       "values (%s,%s,%s,%s,%s::jsonb,%s) on conflict (spec_id, version_no) do nothing",
       (spec_id, ver, name, body,
        json.dumps({'role_code': role, 'space': 'spec' if is_spec else 'material'},
                   ensure_ascii=False), str(user_code or '')))
    invalidate()
    return {'spec_id': spec_id, 'name': name, 'space': 'spec' if is_spec else 'material',
            'status': 'pending' if is_spec else 'material', 'version_no': ver,
            'doc_id': doc_id, 'chars': len(body),
            'retrievable': not is_spec, 'in_menu': False,
            'preview': preview(spec_id)}


def preview(spec_id) -> dict:
    """待发布预览：切出来的正文前几段 + 目录行长什么样。"""
    row = _q("select * from kb.spec where id=%s", (spec_id,))
    if not row:
        return {}
    r = row[0]
    body = r.get('body') or ''
    paras = [p.strip() for p in re.split(r'\n\s*\n', body) if p.strip()]
    return {'id': r['id'], 'name': r['name'], 'chars': len(body),
            'slices': [{'no': i, 'text': p[:200]} for i, p in enumerate(paras[:5], 1)],
            'menu_line': _line(dict(r, conflict_with=[])),
            'missing': [k for k, v in (
                ('什么时候用', r.get('when_to_use')), ('适用', r.get('applies')),
                ('产物', r.get('product')), ('归属', r.get('owner_id'))) if not v]}


def classify(user_id, spec_id, *, role_code='', when_to_use='', applies=None,
             product='', owner_scope='own', check_items=None, outline=None, fmt=None) -> dict:
    """填四项（什么时候用 / 适用 / 产物 / 归属）。四项不全不许发布。"""
    ensure()
    row = _q("select * from kb.spec where id=%s", (spec_id,))
    if not row:
        return {'error': '没有这篇说明书（id=%s）' % spec_id}
    r = row[0]
    role = (role_code or r.get('role_code') or '').strip()
    wtu = when_to_use if when_to_use is not None and when_to_use != '' else (r.get('when_to_use') or '')
    app = applies if applies else (r.get('applies') or [])
    prod = product if product else (r.get('product') or '')
    owner = None if owner_scope == 'global' else int(user_id)
    checks = check_items if check_items is not None else (r.get('check_items') or [])
    _q("""update kb.spec set role_code=%s, when_to_use=%s, applies=%s::jsonb, product=%s,
                 owner_id=%s, check_items=%s::jsonb, outline=%s::jsonb, fmt=%s::jsonb,
                 space='spec', updated_by=%s, updated_at=now()
            where id=%s""",
       (role, str(wtu or ''), json.dumps(app, ensure_ascii=False), str(prod or ''), owner,
        json.dumps(checks, ensure_ascii=False),
        json.dumps(outline if outline is not None else (r.get('outline') or []), ensure_ascii=False),
        json.dumps(fmt if fmt is not None else (r.get('fmt') or {}), ensure_ascii=False),
        str(user_id), spec_id))
    invalidate()
    return {'id': int(spec_id), 'role_code': role, 'when_to_use': wtu, 'applies': app,
            'product': prod, 'owner_scope': 'global' if owner is None else 'own',
            'check_items': checks, 'ready': all([role, wtu, app, prod]),
            'missing': [k for k, v in (('角色', role), ('什么时候用', wtu), ('适用', app),
                                       ('产物', prod)) if not v]}


def _save_version(spec_id, name, body, meta, user_code) -> None:
    row = _q("select version_no from kb.spec where id=%s", (spec_id,))
    ver = int((row[0] or {}).get('version_no') or 1) if row else 1
    _q("""insert into kb.spec_version (spec_id, version_no, name, body, meta, saved_by)
                values (%s,%s,%s,%s,%s::jsonb,%s)
                on conflict (spec_id, version_no) do update
                   set name=excluded.name, body=excluded.body, saved_by=excluded.saved_by""",
       (spec_id, ver, name, body, json.dumps(meta, ensure_ascii=False), str(user_code or '')))


def create(user_id, *, name, body, role_code='', when_to_use='', applies=None,
           product='', check_items=None, owner_scope='own', user_code='') -> dict:
    """新建一篇技能：直接写正文，不用先有文件。落「待发布」，发布后才进目录。"""
    ensure()
    nm = str(name or '').strip()[:120]
    if not nm:
        return {'error': '技能名不能空'}
    owner = None if owner_scope == 'global' else int(user_id)
    row = _q("""insert into kb.spec
                (name, role_code, space, when_to_use, applies, product, owner_id, status,
                 body, check_items, version_no, created_by, updated_by)
                values (%s,%s,'spec',%s,%s::jsonb,%s,%s,'pending',%s,%s::jsonb,1,%s,%s)
                returning id""",
             (nm, str(role_code or ''), str(when_to_use or ''),
              json.dumps(applies or [], ensure_ascii=False), str(product or ''), owner,
              body or '', json.dumps(check_items or [], ensure_ascii=False),
              str(user_code or ''), str(user_code or '')))
    spec_id = int(row[0]['id'])
    _save_version(spec_id, nm, body or '',
                  {'role_code': role_code, 'space': 'spec'}, user_code)
    invalidate()
    return {'id': spec_id, 'name': nm, 'status': 'pending', 'chars': len(body or '')}


def save_draft(user_id, spec_id, *, name=None, body=None, role_code=None, when_to_use=None,
               applies=None, product=None, check_items=None, owner_scope=None,
               user_code='', admin=False) -> dict:
    """存草稿：正文与四项一起写，状态回到「待发布」（退役的重新启用，也要再发布）。"""
    ensure()
    row = _q("select * from kb.spec where id=%s", (spec_id,))
    if not row:
        return {'error': '没有这篇技能'}
    r = row[0]
    if r.get('owner_id') is not None and str(r['owner_id']) != str(user_id) and not admin:
        return {'error': '别人私有的技能不能改'}
    nm = str(name if name is not None else (r.get('name') or '')).strip()[:120] or r['name']
    new_body = body if body is not None else (r.get('body') or '')
    owner = r.get('owner_id')
    if owner_scope == 'global':
        owner = None
    elif owner_scope == 'own':
        owner = int(user_id)
    _q("""update kb.spec set name=%s, role_code=%s, when_to_use=%s, applies=%s::jsonb,
                 product=%s, check_items=%s::jsonb, owner_id=%s, body=%s,
                 status='pending', enabled=true, updated_by=%s, updated_at=now()
            where id=%s""",
       (nm,
        str(role_code if role_code is not None else (r.get('role_code') or '')),
        str(when_to_use if when_to_use is not None else (r.get('when_to_use') or '')),
        json.dumps(applies if applies is not None else (r.get('applies') or []),
                   ensure_ascii=False),
        str(product if product is not None else (r.get('product') or '')),
        json.dumps(check_items if check_items is not None else (r.get('check_items') or []),
                   ensure_ascii=False),
        owner, new_body, str(user_code or ''), spec_id))
    _save_version(spec_id, nm, new_body,
                  {'role_code': r.get('role_code') or '', 'space': r.get('space') or 'spec'},
                  user_code)
    invalidate()
    return {'id': int(spec_id), 'name': nm, 'status': 'pending', 'chars': len(new_body)}


def edit_view(user_id, spec_id, admin=False) -> dict:
    """编辑用：任何状态的原文与四项（不看 published，只看归属）。"""
    ensure()
    row = _q("select * from kb.spec where id=%s", (spec_id,))
    if not row or (not admin and not _mine(row[0], user_id)):
        return {}
    r = row[0]
    return {'id': r['id'], 'name': r['name'], 'role_code': r.get('role_code') or '',
            'space': r.get('space') or 'spec', 'status': r.get('status') or '',
            'when_to_use': r.get('when_to_use') or '', 'applies': r.get('applies') or [],
            'product': r.get('product') or '', 'check_items': r.get('check_items') or [],
            'body': r.get('body') or '', 'owner': '本人' if r.get('owner_id') else '全局',
            'version_no': r.get('version_no'), 'enabled': bool(r.get('enabled')),
            'updated_at': str(r.get('updated_at') or '')}


def enforce_not_retrievable() -> dict:
    """V17：说明书正文一律不进检索（H2.4）。

    只把 `spec.doc_id` 那一行的切片标掉不够 —— 同一个文件在库里可能有多份文档行：
      · 同一文件名的历史版本；
      · 旧做法库时代的副本（命名习惯是「<说明书名>做法」）。
    所以按三条口径一起收：`doc_type='howto'`、`spec.doc_id`、以及文件名里含说明书名的。
    规则全是数据驱动（文件名/文档类型/说明书名），代码里不写业务词。
    """
    ensure()
    ids = set()
    for r in _q("select id from kb.document where doc_type='howto'"):
        ids.add(r['id'])
    for s in _q("select name, doc_id from kb.spec where doc_id is not null"):
        ids.add(s['doc_id'])
        for d in _q("select id from kb.document where filename like %s",
                    ('%' + str(s['name']) + '%',)):
            ids.add(d['id'])
    for i in ids:
        _q("update kb.document_chunk set is_retrievable=false where doc_id=%s", (i,))
    return {'docs': len(ids)}


def publish(spec_id, user_id, admin=False) -> dict:
    """发布：待发布 → 发布（进目录，60 秒内生效）。归属之外的人不许发布。"""
    ensure()
    row = _q("select * from kb.spec where id=%s", (spec_id,))
    if not row:
        return {'error': '没有这篇说明书'}
    r = row[0]
    if r.get('owner_id') is not None and str(r['owner_id']) != str(user_id) and not admin:
        return {'error': '别人私有的说明书不能发布'}
    miss = [k for k, v in (('角色', r.get('role_code')), ('什么时候用', r.get('when_to_use')),
                           ('适用', r.get('applies')), ('产物', r.get('product'))) if not v]
    if miss:
        return {'error': '四项没填全，不能发布：%s' % '、'.join(miss), 'missing': miss}
    _q("""update kb.spec set status='published', published_at=now(), updated_at=now(),
                 updated_by=%s where id=%s""", (str(user_id), spec_id))
    _mark_conflicts()
    invalidate()
    if r.get('doc_id'):
        _q("update kb.document set is_active=true where id=%s", (r['doc_id'],))
    # V17：说明书不进检索 —— 按文件名把全部同版本文档的切片标不可检索
    enforce_not_retrievable()
    return {'id': int(spec_id), 'name': r['name'], 'status': 'published',
            'in_menu': True, 'effective_within_sec': _TTL}


def retire(spec_id, user_id, admin=False) -> dict:
    """退役不删（H9.2）：还留着、可回滚，只是不进目录。"""
    row = _q("select * from kb.spec where id=%s", (spec_id,))
    if not row:
        return {'error': '没有这篇说明书'}
    r = row[0]
    if r.get('owner_id') is not None and str(r['owner_id']) != str(user_id) and not admin:
        return {'error': '别人私有的说明书不能退役'}
    _q("update kb.spec set status='retired', enabled=false, updated_at=now() where id=%s",
       (spec_id,))
    invalidate()
    return {'id': int(spec_id), 'status': 'retired', 'deleted': False}


def enable(spec_id, user_id, admin=False) -> dict:
    """把退役/停用的说明书重新启用（H9.1：旧版退役可回滚）。

    根因：`retire()` 只把 enabled 置 false，没有反向出口，页面上退役后回不来。
    启用后仍保持 `status='published'`，只是 enabled 复位；下一轮目录就有它。
    """
    ensure()
    row = _q("select * from kb.spec where id=%s", (spec_id,))
    if not row:
        return {'error': '没有这篇说明书'}
    r = row[0]
    if r.get('owner_id') is not None and str(r['owner_id']) != str(user_id) and not admin:
        return {'error': '别人私有的说明书不能启用'}
    _q("""update kb.spec set enabled=true,
                  status=case when status='retired' then 'published' else status end,
                  updated_at=now(), updated_by=%s where id=%s""",
       (str(user_id), spec_id))
    _mark_conflicts()
    invalidate()
    enforce_not_retrievable()
    return {'id': int(spec_id), 'name': r['name'], 'enabled': True, 'in_menu': True}


def _mark_conflicts() -> None:
    """并存冲突（V10）：同角色 + 同产物 + 适用词有交集的已发布说明书互相标注。"""
    rows = _q("""select id, name, role_code, product, applies, owner_id from kb.spec
                  where enabled and status='published' and space='spec' and role_code <> ''""") or []
    marks = {r['id']: [] for r in rows}
    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            if a['role_code'] != b['role_code'] or (a['product'] or '') != (b['product'] or ''):
                continue
            wa = set(a.get('applies') or [])
            wb = set(b.get('applies') or [])
            if wa & wb:
                marks[a['id']].append(b['name'])
                marks[b['id']].append(a['name'])
    for sid, names in marks.items():
        _q("update kb.spec set conflict_with=%s::jsonb where id=%s",
           (json.dumps(names, ensure_ascii=False), sid))


# ── 试跑 ──────────────────────────────────────────────────────────────
def try_run(user_id, name='', question='', spec_id=None) -> dict:
    """试跑：不入会话，只看这篇说明书会不会被选中、目录行长什么样、注入多长。"""
    from app.secretary import inject
    hit = load(user_id, name=name, question=question, spec_id=spec_id)
    if not hit:
        return {'hit': False, 'menu_line': '', 'body_chars': 0, 'injection': {}}
    item = hit['item']
    block = inject.spec_block(hit)
    inj = inject.build(user_id, None, question or '', spec=hit)
    return {'hit': True, 'id': item['id'], 'name': item['name'],
            'menu_line': _line(item), 'body_chars': len(hit['body']),
            'body_head': (hit['body'] or '')[:300],
            'check_items': item.get('check_items') or [],
            'refs': hit['refs'], 'conflicts': hit['conflicts'],
            'block_chars': len(block), 'injection': inj['parts']}
