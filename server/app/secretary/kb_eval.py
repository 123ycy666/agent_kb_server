# -*- coding: utf-8 -*-
"""知识库黄金问题集：一组「问这句话应该命中哪篇」的问题，改完目录/检索跑一遍看命中率。

为什么要有：目录、切片、检索参数每次调整都会改变召回结果，
没有一组固定问题，就只能靠感觉判断「比之前好了还是差了」。
"""
from __future__ import annotations

from psycopg.rows import dict_row

from app.secretary import pgdb

SEED = [
    ("公文格式有什么要求", "公文格式规范.md"),
    ("请示报告的写法要求", "请示报告写作要求.md"),
    ("会议纪要要写哪些内容", "会议管理实施细则.md"),
    ("突发事件多长时间内报告", "突发事件信息报送流程.md"),
    ("涉密文件能不能复制", "涉密文件管理办法.md"),
    ("隐患整改谁挂牌督办", "隐患排查治理办法.md"),
    ("年度检修计划怎么编", "年度检修计划编制要求.md"),
    ("设备缺陷多久消缺", "设备缺陷管理流程.md"),
    ("抢修多长时间到场", "供电服务规范(2025版).md"),
    ("电费回收考核指标", "电费回收考核办法.md"),
    ("预算调整要走什么程序", "预算管理办法.md"),
    ("差旅住宿标准多少钱", "差旅费报销标准.md"),
    ("值班交接要注意什么", "值班管理规定.md"),
    ("事故调查报告包含什么", "事故调查报告模板.md"),
]


def _q(sql, params=None):
    import psycopg
    with psycopg.connect(pgdb.DSN, autocommit=True, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchall() if cur.description else []


def ensure() -> None:
    _q("""create table if not exists kb.eval_question (
              id bigserial primary key,
              kb_id bigint not null default 0,
              question varchar(300) not null,
              expect_doc varchar(300) not null default '',
              note varchar(200) not null default '',
              enabled boolean not null default true,
              created_at timestamptz not null default now())""")


def seed(kb_id: int = 0) -> dict:
    ensure()
    n = 0
    for q, doc in SEED:
        if _q("select 1 from kb.eval_question where question=%s", (q,)):
            continue
        _q("insert into kb.eval_question (kb_id, question, expect_doc) values (%s,%s,%s)",
           (int(kb_id), q, doc))
        n += 1
    return {'added': n}


def rows(kb_id: int = 0) -> list[dict]:
    ensure()
    if kb_id:
        return [dict(r) for r in _q(
            "select * from kb.eval_question where kb_id=%s order by id", (int(kb_id),))]
    return [dict(r) for r in _q("select * from kb.eval_question order by id")]


def add(kb_id: int, question: str, expect_doc: str = '', note: str = '') -> dict:
    ensure()
    if not (question or '').strip():
        return {'error': '问题不能为空'}
    _q("insert into kb.eval_question (kb_id, question, expect_doc, note) values (%s,%s,%s,%s)",
       (int(kb_id or 0), question.strip(), expect_doc or '', note or ''))
    return {'ok': True}


def remove(qid: int) -> dict:
    ensure()
    _q("delete from kb.eval_question where id=%s", (int(qid),))
    return {'ok': True}


def run(db, actor, kb_ids: list[int], top_k: int = 5) -> dict:
    """逐题检索，看期望文档是否在 top_k 里。返回命中数与逐题明细。"""
    from app.services.retrieval.search import search as run_search

    ensure()
    qs = [q for q in rows() if not q['kb_id'] or q['kb_id'] in kb_ids]
    items = []
    hit = 0
    for q in qs:
        err = ''
        names: list[str] = []
        try:
            res = run_search(db, actor=actor, query=q['question'], kb_ids=kb_ids,
                             top_k=top_k, use_rerank=False)
            names = [r.doc_name for r in res.results]
        except Exception as exc:                   # noqa: BLE001 - 单题失败不中断整轮
            err = '%s: %s' % (type(exc).__name__, str(exc)[:120])
        ok = bool(q['expect_doc']) and q['expect_doc'] in names
        hit += 1 if ok else 0
        items.append({'id': q['id'], 'question': q['question'], 'expect': q['expect_doc'],
                      'got': names, 'hit': ok, 'error': err})
    return {'total': len(items), 'hit': hit,
            'rate': round(hit / len(items), 3) if items else 0.0, 'items': items}
