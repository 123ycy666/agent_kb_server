# -*- coding: utf-8 -*-
"""评测接口（阶段 6）：题集 / 跑分 / 改造前后对比。

题集与跑分都落库（`qa.eval_suite` / `qa.eval_run` / `qa.eval_item`），
页面上能看每题的结果与判据命中，不是只看一个总分。
"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, Request
from psycopg.rows import dict_row

from app.core.deps import CurrentActor, get_current_actor
from app.secretary import pgdb

router = APIRouter(prefix='/eval', tags=['评测'])

CLASSES = ('闲聊', '写通知', '速写', '纠错', '汇报', '台账', '查数', '接力', '未命中', '归属')


def _q(sql, params=None):
    import psycopg
    with psycopg.connect(pgdb.DSN, autocommit=True, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchall() if cur.description else []


def _jr(data, status_code: int = 200):
    from fastapi.responses import JSONResponse
    return JSONResponse(content=json.loads(json.dumps(data, ensure_ascii=False, default=str)),
                        status_code=status_code)


def ensure() -> None:
    _q("""create table if not exists qa.eval_suite (
              id bigserial primary key, name varchar(80) not null, note text not null default '',
              questions jsonb not null default '[]', created_at timestamptz not null default now())""")
    _q("""create table if not exists qa.eval_run (
              id bigserial primary key, suite_id bigint, tag varchar(80) not null default '',
              model varchar(80) not null default '', total integer not null default 0,
              passed integer not null default 0, score numeric(5,2) not null default 0,
              by_class jsonb not null default '{}', created_at timestamptz not null default now())""")
    _q("""create table if not exists qa.eval_item (
              id bigserial primary key, run_id bigint not null, seq integer not null,
              cls varchar(20) not null default '', question text not null default '',
              answer text not null default '', passed boolean not null default false,
              verdict varchar(200) not null default '', secs numeric(8,1) not null default 0,
              extra jsonb not null default '{}')""")


@router.get('/classes', summary='题集分类')
def classes(actor: CurrentActor = Depends(get_current_actor)):
    return _jr({'items': list(CLASSES)})


@router.get('/suites', summary='题集列表')
def suites(actor: CurrentActor = Depends(get_current_actor)):
    ensure()
    return _jr({'items': _q('select id, name, note, jsonb_array_length(questions) as n, '
                            'created_at from qa.eval_suite order by id desc')})


@router.post('/suites', summary='导入题集（≥30 题，10 类）')
async def create_suite(request: Request, actor: CurrentActor = Depends(get_current_actor)):
    ensure()
    body = await request.json()
    qs = body.get('questions') or []
    row = _q("insert into qa.eval_suite (name, note, questions) values (%s,%s,%s::jsonb) "
             "returning id", (body.get('name') or '题集', body.get('note') or '',
                              json.dumps(qs, ensure_ascii=False)))
    return _jr({'id': row[0]['id'], 'n': len(qs)})


@router.post('/run', summary='跑分（同步跑，题多时用 limit 分批）')
async def run(request: Request, actor: CurrentActor = Depends(get_current_actor)):
    ensure()
    body = await request.json()
    suite_id = int(body.get('suite_id') or 0)
    limit = int(body.get('limit') or 0)
    tag = str(body.get('tag') or time.strftime('%Y-%m-%d %H:%M'))
    rows = _q('select questions from qa.eval_suite where id=%s', (suite_id,))
    if not rows:
        return _jr({'error': '没有这个题集'}, 400)
    qs = rows[0]['questions'] or []
    if limit:
        qs = qs[:limit]
    from app.secretary import agent
    run_row = _q("insert into qa.eval_run (suite_id, tag, model, total) values (%s,%s,%s,%s) "
                 "returning id", (suite_id, tag, '', len(qs)))
    run_id = run_row[0]['id']
    passed, by_class = 0, {}
    for i, item in enumerate(qs, 1):
        q = item if isinstance(item, str) else (item.get('q') or '')
        cls = '' if isinstance(item, str) else (item.get('cls') or '')
        t0 = time.time()
        try:
            r = agent.ask(q, user_id=actor.user_id, user_code=actor.username)
            ans = r.get('answer') or ''
            ok = _judge(item, r)
            verdict = '通过' if ok else '不通过'
        except Exception as exc:                   # noqa: BLE001
            ans, ok, verdict = '', False, '异常：%s' % str(exc)[:120]
        passed += 1 if ok else 0
        by_class.setdefault(cls or '未分类', [0, 0])
        by_class[cls or '未分类'][0] += 1 if ok else 0
        by_class[cls or '未分类'][1] += 1
        _q("""insert into qa.eval_item (run_id, seq, cls, question, answer, passed, verdict, secs)
              values (%s,%s,%s,%s,%s,%s,%s,%s)""",
           (run_id, i, cls, q, ans[:4000], ok, verdict[:200], round(time.time() - t0, 1)))
    score = round(passed * 100.0 / max(len(qs), 1), 2)
    _q('update qa.eval_run set passed=%s, score=%s, by_class=%s::jsonb where id=%s',
       (passed, score, json.dumps(by_class, ensure_ascii=False), run_id))
    return _jr({'run_id': run_id, 'total': len(qs), 'passed': passed, 'score': score,
                'by_class': by_class})


def _judge(item, result) -> bool:
    """单题判据：题集里的 expect（子串/正则）命中就算过；没写 expect 就看有没有交付。"""
    if isinstance(item, str):
        return bool((result.get('answer') or '').strip())
    exp = item.get('expect')
    ans = result.get('answer') or ''
    if exp:
        return all(str(e) in ans for e in (exp if isinstance(exp, list) else [exp]))
    if item.get('need_file'):
        return bool(result.get('artifacts'))
    if item.get('need_check'):
        return bool((result.get('self_check') or {}).get('ok'))
    return bool(ans.strip())


@router.get('/runs', summary='跑分历史')
def runs(actor: CurrentActor = Depends(get_current_actor)):
    ensure()
    return _jr({'items': _q('select id, suite_id, tag, total, passed, score, by_class, '
                            'created_at from qa.eval_run order by id desc limit 50')})


@router.get('/run/{run_id}', summary='一次跑分的逐题结果')
def run_detail(run_id: int, actor: CurrentActor = Depends(get_current_actor)):
    ensure()
    return _jr({'run': (_q('select * from qa.eval_run where id=%s', (run_id,)) or [{}])[0],
                'items': _q('select seq, cls, question, answer, passed, verdict, secs '
                            'from qa.eval_item where run_id=%s order by seq', (run_id,))})


@router.get('/compare', summary='改造前后对比（两次跑分并排）')
def compare(a: int, b: int, actor: CurrentActor = Depends(get_current_actor)):
    ensure()
    ra = (_q('select * from qa.eval_run where id=%s', (a,)) or [{}])[0]
    rb = (_q('select * from qa.eval_run where id=%s', (b,)) or [{}])[0]
    ia = {r['question']: r for r in _q('select question, passed, verdict from qa.eval_item '
                                       'where run_id=%s', (a,))}
    ib = {r['question']: r for r in _q('select question, passed, verdict from qa.eval_item '
                                       'where run_id=%s', (b,))}
    rows = []
    for q in list(ia)[:200]:
        x, y = ia.get(q) or {}, ib.get(q) or {}
        rows.append({'question': q, 'a_passed': bool(x.get('passed')),
                     'b_passed': bool(y.get('passed')),
                     'a_verdict': x.get('verdict') or '', 'b_verdict': y.get('verdict') or '',
                     'changed': bool(x.get('passed')) != bool(y.get('passed'))})
    return _jr({'a': ra, 'b': rb, 'rows': rows,
                'gained': len([r for r in rows if r['b_passed'] and not r['a_passed']]),
                'lost': len([r for r in rows if r['a_passed'] and not r['b_passed']])})
