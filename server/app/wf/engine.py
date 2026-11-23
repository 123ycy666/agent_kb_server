"""执行器：按文档取数 → 分组计算 → 判断 → 回填。一轮一个事务，失败整轮回滚。"""
from __future__ import annotations

import datetime as _dt
import time

from sqlalchemy.orm import Session

from app.wf import compiler, meta, spec as spec_mod
from app.wf.expr import Expr
from app.wf.models import Flow, FlowRun


class FlowError(ValueError):
    """文档本身不成立（校验不过 / 编译不过）。"""


class RunError(RuntimeError):
    """跑的过程中出错（连不上库 / SQL 报错 / 回填失败）。"""


def _jsonable(v):
    if isinstance(v, (_dt.date, _dt.datetime)):
        return v.isoformat()
    if isinstance(v, (int, float, str, bool)) or v is None:
        return v
    try:
        return float(v)
    except (TypeError, ValueError):
        return str(v)


def _row_ctx(raw: dict, refs: dict) -> dict:
    """一条原始行 → 表达式能用的上下文：`别名.列`、裸列名都指得到。"""
    ctx = dict(raw)
    for ref, (alias, col) in refs.items():
        ctx[ref] = raw.get('%s__%s' % (alias, col))
    return ctx


def _pick(item: dict, name: str):
    """取值：先认计算结果里的名字，再认本轮取到的列。"""
    if name in item:
        return item.get(name)
    src = item.get('__src__') or {}
    return src.get(name)


def _cond_hit(cond: dict, item: dict) -> bool:
    v = _pick(item, str(cond.get('ref') or ''))
    op = str(cond.get('op') or '')
    want = cond.get('value')

    def num(x):
        if isinstance(x, bool) or x is None:
            return None
        if isinstance(x, (int, float)):
            return x
        try:
            return float(str(x).strip())
        except ValueError:
            return None

    if op == 'isnull':
        return v is None or v == ''
    if op == 'notnull':
        return not (v is None or v == '')
    a, b = num(v), num(want)
    if a is not None and b is not None:
        cmp_a, cmp_b = a, b
    else:
        cmp_a, cmp_b = ('' if v is None else str(v)), ('' if want is None else str(want))
    if op == 'eq':
        return cmp_a == cmp_b
    if op == 'ne':
        return cmp_a != cmp_b
    if op == 'gt':
        return cmp_a > cmp_b
    if op == 'ge':
        return cmp_a >= cmp_b
    if op == 'lt':
        return cmp_a < cmp_b
    if op == 'le':
        return cmp_a <= cmp_b
    if op in ('in', 'notin'):
        vals = want if isinstance(want, list) else [x.strip() for x in str(want or '').split(',') if x.strip()]
        vals_s = [str(x) for x in vals]
        inside = str(v) in vals_s
        return inside if op == 'in' else not inside
    if op == 'contains':
        return str(want or '') in str(v or '')
    if op == 'starts':
        return str(v or '').startswith(str(want or ''))
    if op == 'ends':
        return str(v or '').endswith(str(want or ''))
    return False


def _match_rule(rule: dict, item: dict) -> bool:
    when = rule.get('when') or []
    if not when:
        return False
    results = [_cond_hit(c, item) for c in when]
    return any(results) if str(rule.get('op') or 'AND').upper() == 'OR' else all(results)


def judge(item: dict, spec: dict) -> dict:
    for rule in (spec.get('judge') or {}).get('rules') or []:
        if _match_rule(rule, item):
            then = rule.get('then') or {}
            return {'hit': bool(then.get('hit')), 'tier': str(then.get('tier') or '')}
    d = (spec.get('judge') or {}).get('default') or {}
    return {'hit': bool(d.get('hit')), 'tier': str(d.get('tier') or '')}


def compute(spec: dict, raw_rows: list[dict]) -> list[dict]:
    """原始行 → 每个分组一条结果，字段算好、判断也判好。"""
    refs = spec_mod.source_refs(spec)
    calc = spec.get('calc') or {}
    grain = calc.get('grain') or []
    fields = calc.get('fields') or []
    policy = str(calc.get('row_policy') or 'first')
    order_ref = str(calc.get('order_ref') or '')

    groups: dict[tuple, list[dict]] = {}
    for raw in raw_rows:
        ctx = _row_ctx(raw, refs)
        key = tuple(_jsonable(ctx.get(str(g.get('ref')))) for g in grain)
        groups.setdefault(key, []).append(ctx)

    exprs = []
    for f in fields:
        exprs.append((str(f.get('name')), Expr(str(f.get('expr') or ''))))

    out: list[dict] = []
    for _key, rows in groups.items():
        rep = rows[0]
        if policy == 'latest' and order_ref:
            def _ord(r):
                v = r.get(order_ref)
                return (v is not None, str(v))
            rep = max(rows, key=_ord)
        elif policy in ('max', 'min') and order_ref:
            vals = [r.get(order_ref) for r in rows if r.get(order_ref) is not None]
            if vals:
                target = (max if policy == 'max' else min)(vals)
                rep = next((r for r in rows if r.get(order_ref) == target), rep)

        item: dict = {}
        for g in grain:
            item[str(g.get('name'))] = rep.get(str(g.get('ref')))
        eval_ctx = dict(rep)
        eval_ctx.update({k: v for k, v in item.items()})
        for name, ex in exprs:
            try:
                val = ex.eval(eval_ctx, rows if ex.aggregated else None)
            except Exception as exc:      # noqa: BLE001 - 单条算不出来就置空，不让整轮挂
                val = None
                item.setdefault('__errors__', []).append('%s: %s' % (name, exc))
            item[name] = val
            eval_ctx[name] = val
        item['__src__'] = rep
        item.update(judge(item, spec))
        out.append(item)
    return out


# ── 取数 / 回填 ─────────────────────────────────────────────────────────
def _fetch_biz(sql: str, params: list) -> list[dict]:
    import pymysql

    from app.secretary import config
    conn = pymysql.connect(cursorclass=pymysql.cursors.DictCursor, autocommit=True,
                           connect_timeout=8, read_timeout=120, charset='utf8mb4', **config.DB)
    try:
        cur = conn.cursor()
        cur.execute(sql, params or None)
        return list(cur.fetchall())
    finally:
        try:
            conn.close()
        except Exception:                 # noqa: BLE001
            pass


def _fetch_fw(sql: str, params: list) -> list[dict]:
    from app.core.database import wf_engine
    with wf_engine.connect() as conn:
        res = conn.exec_driver_sql(sql, tuple(params or ()))
        cols = list(res.keys())
        return [dict(zip(cols, row)) for row in res.fetchall()]


def fetch(db: str, sql: str, params: list) -> list[dict]:
    return _fetch_fw(sql, params) if db == meta.FW else _fetch_biz(sql, params)


def _key_tuple(plan: dict, item: dict) -> tuple:
    return tuple(_jsonable(item.get(k['from'])) for k in plan['key'])


def write_back(plan: dict, items: list[dict]) -> dict:
    """按回填计划把结果写回目标表。返回命中已有行几条、新插几条。"""
    if not items:
        return {'updated': 0, 'inserted': 0}
    mode = plan['mode']
    if plan['db'] == meta.FW:
        return _write_fw(plan, items, mode)
    return _write_biz(plan, items, mode)


def _clean(params: list) -> list:
    """空字符串写进库应当是 NULL：算不出来的轴宁可空着，也不要一个假值。"""
    return [None if p == '' else p for p in params]


def _write_biz(plan: dict, items: list[dict], mode: str) -> dict:
    import pymysql
    from pymysql.constants import CLIENT

    from app.secretary import config
    # CLIENT.FOUND_ROWS：UPDATE 返回「匹配到几行」而不是「改了几行」。
    # 不然第二轮一个值都没变，会误报成「回填 0 行」（2026-11-23 实测）。
    conn = pymysql.connect(autocommit=False, connect_timeout=8, read_timeout=120,
                           write_timeout=120, charset='utf8mb4', client_flag=CLIENT.FOUND_ROWS,
                           **config.DB)
    updated = inserted = 0
    try:
        cur = conn.cursor()
        if mode == 'update':
            sql = compiler.build_update(plan, items[0])[0]
            rows = [tuple(_clean(compiler.build_update(plan, it)[1])) for it in items]
            cur.executemany(sql, rows)
            updated = len(items)
        elif mode == 'insert':
            sql = compiler.build_insert(plan, items[0])[0]
            rows = [tuple(_clean(compiler.build_insert(plan, it)[1])) for it in items]
            cur.executemany(sql, rows)
            inserted = len(items)
        else:
            for it in items:
                sql, params = compiler.build_update(plan, it)
                cur.execute(sql, _clean(params))
                if cur.rowcount:
                    updated += 1
                else:
                    sql2, params2 = compiler.build_insert(plan, it)
                    cur.execute(sql2, _clean(params2))
                    inserted += 1
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:                 # noqa: BLE001
            pass
        raise
    finally:
        try:
            conn.close()
        except Exception:                 # noqa: BLE001
            pass
    return {'updated': updated, 'inserted': inserted}


def _write_fw(plan: dict, items: list[dict], mode: str) -> dict:
    from app.core.database import wf_engine
    updated = inserted = 0
    with wf_engine.begin() as conn:
        if mode in ('upsert', 'update'):
            for it in items:
                sql, params = compiler.build_update(plan, it)
                res = conn.exec_driver_sql(sql, tuple(_clean(params)))
                if res.rowcount:
                    updated += 1
                elif mode == 'upsert':
                    sql2, params2 = compiler.build_insert(plan, it)
                    conn.exec_driver_sql(sql2, tuple(_clean(params2)))
                    inserted += 1
        else:
            for it in items:
                sql2, params2 = compiler.build_insert(plan, it)
                conn.exec_driver_sql(sql2, tuple(_clean(params2)))
                inserted += 1
    return {'updated': updated, 'inserted': inserted}


# ── 一轮 ────────────────────────────────────────────────────────────────
def run_flow(db: Session, flow: Flow, trigger: str = 'manual', dry: bool = False,
             sample: int = 20) -> dict:
    spec = spec_mod.normalize(flow.spec or {})
    errors = spec_mod.validate(spec)
    if errors:
        raise FlowError('文档还没填完：' + '；'.join(e['message'] for e in errors[:3]))

    compiled = compiler.compile_all(spec)
    run = FlowRun(flow_id=flow.id, flow_name=flow.name, flow_version=int(flow.version or 1),
                  trigger=trigger, status='running', dry_run=1 if dry else 0,
                  sql_text=compiled['select_preview'])
    db.add(run)
    db.commit()

    t0 = time.time()
    try:
        raw = fetch(compiled['select']['db'], compiled['select']['sql'], compiled['select']['params'])
        items = compute(spec, raw)
        hits = [x for x in items if x.get('hit')]
        written = {'updated': 0, 'inserted': 0}
        if not dry:
            written = write_back(compiled['write'], items)
        run.status = 'ok'
        run.selected_n = len(raw)
        run.hit_n = len(hits)
        run.written_n = int(written.get('updated', 0)) + int(written.get('inserted', 0))
        run.elapsed_ms = int((time.time() - t0) * 1000)
        run.finished_at = _dt.datetime.now(_dt.timezone.utc)
        run.error = None
        db.commit()
        return {
            'run_id': run.id, 'status': 'ok', 'dry': dry,
            'selected_n': run.selected_n, 'hit_n': run.hit_n,
            'written_n': run.written_n, 'updated': written.get('updated', 0),
            'inserted': written.get('inserted', 0),
            'elapsed_ms': run.elapsed_ms,
            'sql': compiled['select_preview'],
            'write_sql': compiled['write_preview'],
            'sample': [{k: _jsonable(v) for k, v in it.items() if not k.startswith('__')}
                       for it in items[:max(1, sample)]],
        }
    except Exception as exc:              # noqa: BLE001 - 失败也要落记录再抛
        run.status = 'failed'
        run.error = '%s: %s' % (type(exc).__name__, str(exc)[:500])
        run.elapsed_ms = int((time.time() - t0) * 1000)
        run.finished_at = _dt.datetime.now(_dt.timezone.utc)
        db.commit()
        raise RunError(run.error) from exc


def compile_flow(flow: Flow) -> dict:
    spec = spec_mod.normalize(flow.spec or {})
    errors = spec_mod.validate(spec)
    if errors:
        return {'ok': False, 'errors': errors}
    out = compiler.compile_all(spec)
    out['ok'] = True
    out['errors'] = []
    return out
