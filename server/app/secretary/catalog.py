# -*- coding: utf-8 -*-
"""知识库的目录、文档元数据、受控词表（需求 H7 / H9）。

为什么要有这层：19 篇资料平铺在一个库里，人找不到、模型只能靠向量硬撞。
目录回答「属于哪、谁能看」，词表回答「这是什么文种、跨哪些主题」，状态回答「现在还算不算数」。
"""
from __future__ import annotations

from psycopg.rows import dict_row

from app.secretary import pgdb

KINDS = ('制度', '办法', '细则', '规定', '规范', '通知', '通报', '请示', '报告',
         '批复', '函', '纪要', '模板', '台账', '流程', '方案')
TOPICS = ('公文格式', '保密', '财务报销', '安全生产', '考核', '信访', '督查', '值班', '接待')
STATUSES = ('draft', 'active', 'superseded', 'archived')
SECRECY = ('public', 'internal', 'sensitive')

# 一级 = 业务条线；二级 = 事项域。目录即权限边界，新增要管理员。
# ★ 2026-11-19 移库：目录树存 `kb.catalog`（页面可增删改），代码里不再写死业务分类。
#   空现场 = 空目录，管理员照自己的条线建；不再回落任何现场的分类树。
TREE: dict = {}


def _q(sql, params=None):
    import psycopg
    with psycopg.connect(pgdb.DSN, autocommit=True, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchall() if cur.description else []


def ensure() -> None:
    _q("""create table if not exists kb.catalog (
              id bigserial primary key,
              parent_id bigint,
              name varchar(64) not null,
              path varchar(200) not null default '',
              owner_id bigint,
              sort_no int not null default 100,
              enabled boolean not null default true,
              note varchar(200) not null default '',
              unique (parent_id, name))""")
    _q("""create table if not exists kb.tag_term (
              id bigserial primary key,
              dimension varchar(16) not null,
              term varchar(64) not null,
              synonyms varchar(200) not null default '',
              enabled boolean not null default true,
              usage_count int not null default 0,
              unique (dimension, term))""")
    for col, ddl in (
        ('catalog_id', 'bigint'),
        ('doc_kind', "varchar(16) not null default ''"),
        ('issuer', "varchar(128) not null default ''"),
        ('doc_no', "varchar(64) not null default ''"),
        ('signed_on', 'date'),
        ('effective_from', 'date'),
        ('status', "varchar(16) not null default 'active'"),
        ('secrecy', "varchar(16) not null default 'internal'"),
    ):
        _q('alter table kb.document add column if not exists %s %s' % (col, ddl))
    _repair_compat_view()


def _repair_compat_view() -> None:
    """public.document 是兼容视图；视图列固定，新加的列不会自动出现。

    不修的话 SQLAlchemy 从 public 读会报 `column document.catalog_id does not exist`。
    """
    try:
        rows = _q("""select 1 from information_schema.columns
                      where table_schema='public' and table_name='document'
                        and column_name='catalog_id'""")
        if rows:
            return
        has_view = _q("""select 1 from information_schema.views
                          where table_schema='public' and table_name='document'""")
        if not has_view:
            return
        _q('drop view if exists public.document')
        _q('create view public.document as select * from kb.document')
    except Exception as exc:                       # noqa: BLE001 - 视图修不了不影响主流程
        print('[catalog] 兼容视图修复失败：%s: %s' % (type(exc).__name__, exc), flush=True)


def seed(tree: dict | None = None) -> dict:
    """灌默认目录与两份词表；已有则不覆盖。"""
    ensure()
    made = 0
    tree = tree if tree is not None else TREE
    if not tree:
        return {"ok": True, "created": 0, "note": "目录树为空（代码不再内置业务分类，由库里或调用方提供）"}
    for i, (top, subs) in enumerate(tree.items(), 1):
        rows = _q("select id from kb.catalog where parent_id is null and name=%s", (top,))
        if rows:
            pid = rows[0]['id']
        else:
            pid = _q("insert into kb.catalog (name, path, sort_no) values (%s,%s,%s) returning id",
                     (top, top, i * 10))[0]['id']
            made += 1
        for j, sub in enumerate(subs, 1):
            if not _q("select 1 from kb.catalog where parent_id=%s and name=%s", (pid, sub)):
                _q("insert into kb.catalog (parent_id, name, path, sort_no) values (%s,%s,%s,%s)",
                   (pid, sub, '%s/%s' % (top, sub), j * 10))
                made += 1
    for t in KINDS:
        _q("insert into kb.tag_term (dimension, term) values ('文种', %s) "
           "on conflict (dimension, term) do nothing", (t,))
    for t in TOPICS:
        _q("insert into kb.tag_term (dimension, term) values ('主题', %s) "
           "on conflict (dimension, term) do nothing", (t,))
    return {'catalogs': made}


def tree() -> list[dict]:
    ensure()
    rows = _q("""select c.*, (select count(*) from kb.document d
                              where d.catalog_id = c.id and d.is_active) as docs
                   from kb.catalog c order by c.path""")
    return [dict(r) for r in rows]


def add(name: str, parent_id=None, note: str = '') -> dict:
    ensure()
    name = (name or '').strip()
    if not name:
        return {'error': '目录名不能为空'}
    parent = None
    path = name
    if parent_id:
        rows = _q("select path from kb.catalog where id=%s", (int(parent_id),))
        if not rows:
            return {'error': '父目录不存在'}
        parent = int(parent_id)
        path = '%s/%s' % (rows[0]['path'], name)
    row = _q("insert into kb.catalog (parent_id, name, path, note) values (%s,%s,%s,%s) returning id",
             (parent, name, path, note))[0]
    return {'ok': True, 'id': row['id'], 'path': path}


def set_enabled(cid: int, enabled: bool) -> dict:
    ensure()
    _q("update kb.catalog set enabled=%s where id=%s", (bool(enabled), int(cid)))
    return {'ok': True}


def terms(dimension: str = '') -> list[dict]:
    """词表 + 动态使用次数（按文档标签实时算，不维护计数器）。"""
    ensure()
    usage = {}
    try:
        rows = _q("""select t as term, count(*) as n from (
                        select unnest(tags) as t from kb.document where is_active and status='active'
                     ) x group by 1""")
        usage = {r['term']: int(r['n']) for r in rows}
    except Exception:                              # noqa: BLE001 - 统计失败不影响词表
        pass
    sql = ("select * from kb.tag_term where dimension=%s order by term" if dimension
           else "select * from kb.tag_term order by dimension, term")
    rows = [dict(r) for r in _q(sql, (dimension,) if dimension else None)]
    for r in rows:
        r['usage_count'] = usage.get(r['term'], 0)
    return sorted(rows, key=lambda r: (-r['usage_count'], r['term']))


def add_term(dimension: str, term: str, synonyms: str = '', definition: str = '',
             owner: str = '') -> dict:
    ensure()
    term = (term or '').strip()
    if not term:
        return {'error': '词条不能为空'}
    if dimension not in ('文种', '主题'):
        return {'error': '维度只有 文种 / 主题'}
    _q("""insert into kb.tag_term (dimension, term, synonyms, definition, owner)
          values (%s,%s,%s,%s,%s)
          on conflict (dimension, term) do update set synonyms=excluded.synonyms,
                definition=excluded.definition, owner=excluded.owner""",
       (dimension, term, synonyms or '', definition or '', owner or ''))
    return {'ok': True}


def set_term_enabled(tid: int, enabled: bool) -> dict:
    ensure()
    _q("update kb.tag_term set enabled=%s where id=%s", (bool(enabled), int(tid)))
    return {'ok': True}


def doc_meta(doc_id: int) -> dict:
    ensure()
    rows = _q("""select d.id, d.filename, d.catalog_id, d.doc_kind, d.issuer, d.doc_no,
                        d.signed_on, d.effective_from, d.status, d.secrecy, d.tags,
                        c.path as catalog_path, c.name as catalog_name
                   from kb.document d left join kb.catalog c on c.id = d.catalog_id
                  where d.id=%s""", (int(doc_id),))
    return dict(rows[0]) if rows else {}


def save_doc_meta(doc_id: int, data: dict) -> dict:
    ensure()
    allowed = ('catalog_id', 'doc_kind', 'issuer', 'doc_no', 'signed_on',
               'effective_from', 'status', 'secrecy')
    sets, vals = [], []
    for k in allowed:
        if k in data:
            sets.append('%s=%%s' % k)
            vals.append(data[k] if data[k] not in ('', None) else None)
    if not sets:
        return {'error': '没有要改的字段'}
    vals.append(int(doc_id))
    _q('update kb.document set %s where id=%%s' % ', '.join(sets), tuple(vals))
    return {'ok': True, 'id': int(doc_id)}


def list_docs(kb_id: int, limit: int = 200) -> list[dict]:
    """资料库列表：文档 + 目录 / 文种 / 时效 / 密级，一次查完。"""
    ensure()
    rows = _q("""select d.id, d.filename, d.file_ext, d.version_no, d.parse_status, d.chunk_count,
                        d.visibility, d.tags, d.created_at,
                        d.doc_kind, d.issuer, d.doc_no, d.signed_on, d.effective_from,
                        d.status, d.secrecy, d.catalog_id,
                        c.path as catalog_path
                   from kb.document d left join kb.catalog c on c.id = d.catalog_id
                  where d.is_active and d.kb_id = %s
                  order by coalesce(c.path,'~'), d.filename limit %s""", (int(kb_id), int(limit)))
    return [dict(r) for r in rows]


def completeness() -> dict:
    ensure()
    rows = _q("""select count(*) as total,
                        count(*) filter (where catalog_id is null) as no_catalog,
                        count(*) filter (where doc_kind='' or doc_kind is null) as no_kind,
                        count(*) filter (where issuer='' or issuer is null) as no_issuer,
                        count(*) filter (where doc_no='' or doc_no is null) as no_no
                   from kb.document where is_active""")
    return dict(rows[0]) if rows else {}
