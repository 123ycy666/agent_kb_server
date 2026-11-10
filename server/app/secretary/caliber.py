# -*- coding: utf-8 -*-
"""指标契约：口径存表（caliber_metric / caliber_base），代码只有机制。

为什么要有（2026-11-10）：府河所那题里，工单侧漏了年度条件查成四年累计，
执行率因为"证据包禁止相除"答成「需另查」，成效极值因为证据只摊 12 行答成「需另查」。
三处的共性都是**口径写在提示词里、正确性靠模型自觉**。所以把口径搬进数据，
代码只留三个通用动作：expand（摊成 SQL）、check_sql（缺必需过滤就拒）、verify（交卷对账）。

这里不认识「预算」「工单」这些词 —— 业务全在数据行里，改口径 = 改数据。
"""
from __future__ import annotations

import re
import time

from app.secretary import pgdb

_AGG = ('sum', 'count', 'max', 'min', 'avg', 'last')
_NAME = re.compile(r'^[A-Za-z0-9_]+$')
_IDENT = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
YEAR_VAR = '$问题年份'
_SAFE_EXPR = re.compile(r'^[0-9eE+\-*/(). ]+$')

_CACHE: dict = {'t': 0.0, 'rows': None}
_TTL = 30.0


def _rows() -> list[dict]:
    now = time.time()
    if _CACHE['rows'] is not None and now - _CACHE['t'] < _TTL:
        return _CACHE['rows']
    out: list[dict] = []
    try:
        c = pgdb.connect()
        cur = c.cursor()
        cur.execute('SELECT code,label,unit,digits,direction,expr,notes,enabled,version_no,'
                    'updated_by,updated_at FROM caliber_metric ORDER BY code')
        for r in cur.fetchall():
            r = dict(r)
            r['bases'] = []
            out.append(r)
        by = {r['code']: r for r in out}
        cur.execute('SELECT metric_code,table_name,column_name,agg,scope_col,must,sort_key '
                    'FROM caliber_base ORDER BY id')
        for b in cur.fetchall():
            b = dict(b)
            m = by.get(b['metric_code'])
            if m is not None:
                m['bases'].append(b)
        c.close()
    except Exception as exc:                       # noqa: BLE001 - 契约读不到不能阻断问答
        print('[caliber] 契约读取失败：%s: %s' % (type(exc).__name__, str(exc)[:120]), flush=True)
        return _CACHE['rows'] if _CACHE['rows'] is not None else []
    _CACHE.update({'t': now, 'rows': out})
    return out


def reload() -> None:
    _CACHE.update({'t': 0.0, 'rows': None})


def metrics(only_enabled: bool = True) -> list[dict]:
    return [r for r in _rows() if r['enabled'] or not only_enabled]


def get(code: str) -> dict | None:
    for r in _rows():
        if r['code'] == str(code or '').strip():
            return r
    return None


def menu() -> str:
    """可用指标清单（给模型看代号，不给算式）。"""
    lines = []
    for r in metrics():
        unit = ('（%s）' % r['unit']) if r['unit'] else ''
        lines.append('- %s ｜ %s%s' % (r['code'], r['label'], unit))
    return '\n'.join(lines)


# ── 展成 SQL ──────────────────────────────────────────────────────────
def _val(v) -> str:
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", '') + "'"


def _where(must, year, scope_col, scope_id) -> tuple[list[str], list[str]]:
    """契约的必需过滤 → WHERE 片段；同时返回「本契约要求、SQL 里必须出现的列」。"""
    parts, need = [], []
    for m in (must or []):
        col = str(m.get('col') or '').strip()
        op = str(m.get('op') or '=').strip()
        if not col or not _NAME.match(col):
            continue
        need.append(col)
        val = m.get('value')
        if val == YEAR_VAR:
            if year in (None, ''):
                continue                       # 没给年份就不拼；check_sql 会因此拦下
            val = str(year)
        if op.lower() == 'in' and isinstance(val, (list, tuple)):
            parts.append('%s IN (%s)' % (col, ', '.join(_val(x) for x in val)))
        elif op in ('=', '!=', '<>', '>', '<', '>=', '<='):
            parts.append('%s %s %s' % (col, op, _val(val)))
    if scope_col and scope_id not in (None, '') and _NAME.match(str(scope_col)):
        need.append(str(scope_col))
        parts.append('%s = %s' % (scope_col, _val(scope_id)))
    return parts, need


def _one_sql(code: str, b: dict, year, scope_id) -> dict:
    table = str(b.get('table_name') or '')
    col = str(b.get('column_name') or '')
    agg = str(b.get('agg') or 'sum').lower()
    if not (_NAME.match(table) and _NAME.match(col)) or agg not in _AGG:
        return {'error': '契约字段不合法：%s.%s agg=%s' % (table, col, agg)}
    expr = 'COUNT(*)' if agg == 'count' else '%s(%s)' % (agg.upper(), col)
    parts, need = _where(b.get('must'), year, b.get('scope_col'), scope_id)
    sql = 'SELECT %s AS v FROM %s WHERE %s' % (
        expr, table, ' AND '.join(parts) if parts else '1=1')
    if b.get('sort_key'):
        sql += ' ORDER BY %s DESC' % b['sort_key']
    return {'sql': sql, 'need': need, 'table': table, 'agg': agg}


def expand(code: str, year=None, scope_id=None) -> dict:
    """把一条契约摊成 SQL：返回 {sql, selects, expr, unit, digits, direction, need}。"""
    m = get(code)
    if not m:
        return {'error': '没有这个指标代号：%s' % code, 'available': [r['code'] for r in metrics()]}
    out = {'code': m['code'], 'label': m['label'], 'unit': m['unit'], 'digits': m['digits'],
           'direction': m['direction'], 'expr': (m['expr'] or '').strip(), 'sql': [],
           'items': [], 'need': {}}
    names = [t for t in _IDENT.findall(out['expr'])] if out['expr'] else [m['code']]
    for name in dict.fromkeys(names):
        base = next((b for b in m['bases'] if b['metric_code'] == name), None)
        target = m if name == m['code'] else get(name)
        if base is None and target is not None:
            base = (target['bases'] or [None])[0]
        if not base:
            out.setdefault('missing', []).append(name)
            continue
        one = _one_sql(name, base, year, scope_id)
        if one.get('error'):
            return one
        out['sql'].append(one['sql'])
        out['items'].append({'code': name, 'sql': one['sql']})
        out['need'][name] = one['need']
    return out


def check_sql(sql: str, year=None) -> dict:
    """闸1：这条 SQL 命中了契约表，就必须带上该契约声明的必需列。

    只认契约数据，不认识具体业务；契约里没声明必需列的，一律放行。
    """
    from app.secretary import semantic
    s = str(sql or '')
    tables = semantic.tables_in_sql(s)
    missing = []
    for m in metrics():
        owned = {b['table_name'] for b in (m['bases'] or [])}
        if not (owned & tables):
            continue
        for b in m['bases'] or []:
            if b['table_name'] not in tables:
                continue
            _, need = _where(b.get('must'), year, b.get('scope_col'), None)
            for col in need:
                if not re.search(r'\b%s\b' % re.escape(col), s, re.I):
                    missing.append({'code': m['code'], 'col': col,
                                    'why': '契约 %s（%s）要求带上 %s' % (
                                        b['metric_code'] or m['code'], m['label'], col)})
    if not missing:
        return {'ok': True}
    uniq, seen = [], set()
    for x in missing:
        key = (x['code'], x['col'])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(x)
    missing = uniq
    hint = '；'.join('缺 %s' % x['col'] for x in missing)
    return {'ok': False, 'code': 'ERROR_CALIBER', 'missing': missing,
            'hint': ('这条 SQL 命中了指标契约，必须先满足契约声明的过滤（%s）。'
                     '按契约取数请直接调 metric_query(代号)，它会把条件拼全。' % hint)}


# ── 取数（派生值由这里算，模型没有算式入口）──────────────────────────
def _eval_expr(expr: str, vals: dict) -> float | None:
    s = str(expr or '').strip()
    if not s:
        return None
    for name in set(_IDENT.findall(s)):
        if name not in vals:
            return None
        s = re.sub(r'\b%s\b' % re.escape(name), '(%r)' % float(vals[name]), s)
    if not _SAFE_EXPR.match(s):
        return None
    try:
        return float(eval(s, {'__builtins__': {}}, {}))    # noqa: S307 - 只允许数字与四则运算
    except Exception:                              # noqa: BLE001
        return None


def query(code: str, year=None, scope_id=None) -> dict:
    """按契约取数：基础指标走 SQL，派生指标用契约里的算式算。"""
    from app.secretary import tools_db
    plan = expand(code, year=year, scope_id=scope_id)
    if plan.get('error'):
        return plan
    vals, detail = {}, []
    for item in plan['items']:
        name, sql = item['code'], item['sql']
        r = tools_db.run_sql(sql)
        if r.get('error'):
            return {'error': '契约 %s 取数失败：%s' % (name, r['error']), 'sql': sql}
        rows = r.get('rows') or []
        raw = rows[0].get('v') if rows else None
        try:
            vals[name] = float(raw) if raw is not None else 0.0
        except (TypeError, ValueError):
            vals[name] = 0.0
        detail.append({'code': name, 'value': raw, 'sql': sql})
    value = _eval_expr(plan['expr'], vals) if plan['expr'] else vals.get(plan['code'])
    if value is None:
        return {'error': '契约算式算不出来：%s' % (plan['expr'] or code), 'detail': detail}
    digits = int(plan.get('digits') or 0)
    text = ('%.*f' % (digits, value))
    return {'code': plan['code'], 'label': plan['label'], 'value': text, 'unit': plan['unit'],
            'direction': plan['direction'], 'year': str(year or ''), 'scope_id': scope_id,
            'expr': plan['expr'], 'detail': detail,
            'text': '%s=%s%s' % (plan['label'], text, plan['unit'])}


# ── 口径的编辑入口（页面用；改完下一轮生效，不用重启）─────────────────
CODE_RE = re.compile(r'^[a-z][a-z0-9_]{1,63}$')


def _norm_bases(bases) -> list[dict]:
    out = []
    for b in (bases or []):
        if not isinstance(b, dict):
            continue
        table = str(b.get('table_name') or '').strip()
        col = str(b.get('column_name') or '').strip()
        if not (_NAME.match(table) and _NAME.match(col)):
            continue
        agg = str(b.get('agg') or 'sum').lower()
        scope = str(b.get('scope_col') or '').strip()
        if scope and not _NAME.match(scope):
            scope = ''
        must = []
        for m in (b.get('must') or []):
            if not isinstance(m, dict):
                continue
            c = str(m.get('col') or '').strip()
            if not _NAME.match(c):
                continue
            must.append({'col': c, 'op': str(m.get('op') or '=').strip(),
                         'value': m.get('value')})
        out.append({'table_name': table, 'column_name': col,
                    'agg': agg if agg in _AGG else 'sum', 'scope_col': scope,
                    'must': must, 'sort_key': str(b.get('sort_key') or '').strip()})
    return out


def save(code: str, body: dict, user: str = '') -> dict:
    """新建或改一条契约：写 metric + 替换 base + 留一版快照。"""
    import json
    code = str(code or '').strip()
    if not CODE_RE.match(code):
        return {'error': '代号只能用 a-z 开头的小写字母/数字/下划线，2-64 位'}
    body = body or {}
    expr = str(body.get('expr') or '').strip()
    bases = _norm_bases(body.get('bases'))
    if expr:
        names = set(_IDENT.findall(expr))
        if not names:
            return {'error': '派生式里没有指标代号'}
        known = {m['code'] for m in _rows()} | {code}
        bad = sorted(n for n in names if n not in known)
        if bad:
            return {'error': '派生式引用了不存在的代号：%s' % '、'.join(bad)}
    elif not bases:
        return {'error': '取数类指标至少要有一条 base（表 + 列 + 聚合）'}
    label = str(body.get('label') or '')[:128]
    if not label:
        return {'error': '中文名不能为空'}
    unit = str(body.get('unit') or '')[:16]
    digits = max(0, min(6, int(body.get('digits') or 0)))
    direction = str(body.get('direction') or '')[:16]
    notes = str(body.get('notes') or '')
    enabled = bool(body.get('enabled', True))
    c = pgdb.connect()
    cur = c.cursor()
    try:
        cur.execute('SELECT version_no FROM caliber_metric WHERE code=%s', (code,))
        old = cur.fetchall()
        ver = (int(old[0]['version_no']) + 1) if old else 1
        if old:
            cur.execute('UPDATE caliber_metric SET label=%s,unit=%s,digits=%s,direction=%s,'
                        'expr=%s,notes=%s,enabled=%s,version_no=%s,updated_by=%s,'
                        'updated_at=now() WHERE code=%s',
                        (label, unit, digits, direction, expr, notes, enabled, ver, user, code))
        else:
            cur.execute('INSERT INTO caliber_metric (code,label,unit,digits,direction,expr,'
                        'notes,enabled,version_no,updated_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                        (code, label, unit, digits, direction, expr, notes, enabled, ver, user))
        cur.execute('DELETE FROM caliber_base WHERE metric_code=%s', (code,))
        for b in bases:
            cur.execute('INSERT INTO caliber_base (metric_code,table_name,column_name,agg,'
                        'scope_col,must,sort_key) VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s)',
                        (code, b['table_name'], b['column_name'], b['agg'], b['scope_col'],
                         json.dumps(b['must'], ensure_ascii=False), b['sort_key']))
        cur.execute('INSERT INTO caliber_metric_ver (code,version_no,body,saved_by) '
                    'VALUES (%s,%s,%s::jsonb,%s)',
                    (code, ver, json.dumps({'label': label, 'unit': unit, 'digits': digits,
                                            'direction': direction, 'expr': expr,
                                            'notes': notes, 'enabled': enabled,
                                            'bases': bases}, ensure_ascii=False), user))
    finally:
        c.close()
    reload()
    return {'ok': True, 'code': code, 'version_no': ver}


def toggle(code: str, enabled: bool) -> dict:
    m = get(code)
    if not m:
        return {'error': '没有这个指标代号：%s' % code}
    c = pgdb.connect()
    cur = c.cursor()
    try:
        cur.execute('UPDATE caliber_metric SET enabled=%s, updated_at=now() WHERE code=%s',
                    (bool(enabled), code))
    finally:
        c.close()
    reload()
    return {'ok': True, 'code': code, 'enabled': bool(enabled)}


def history(code: str) -> list[dict]:
    c = pgdb.connect()
    cur = c.cursor()
    try:
        cur.execute('SELECT version_no,body,saved_by,saved_at FROM caliber_metric_ver '
                    'WHERE code=%s ORDER BY version_no DESC LIMIT 50', (code,))
        return [dict(r) for r in cur.fetchall()]
    finally:
        c.close()


# ── 闸3：交卷对账 ─────────────────────────────────────────────────────
_UNIT_NUM = re.compile(r'(\d+(?:\.\d+)?)\s*(?:万元|亿元|元|%|％|件|个|条|户|次|小时|天|台|人|项)')
_ALL_NUM = re.compile(r'\d+(?:\.\d+)?')


def _nums(text: str) -> set[str]:
    return {x.rstrip('0').rstrip('.') if '.' in x else x for x in _ALL_NUM.findall(str(text or ''))}


def verify(answer: str, evidence: str) -> dict:
    """正文里的数必须能在证据里找到同值。找不到的列出来，由调用方决定重写还是标未核实。"""
    ev = _nums(evidence)
    evf = set()
    for x in ev:
        try:
            evf.add(round(float(x), 4))
        except ValueError:
            pass
    missing = []
    for m in _UNIT_NUM.finditer(str(answer or '')):
        raw = m.group(1)
        try:
            v = float(raw)
        except ValueError:
            continue
        hit = any(abs(v - e) <= max(0.011, abs(e) * 0.0005) for e in evf)
        if not hit:
            missing.append({'text': m.group(0), 'value': raw})
    return {'ok': not missing, 'missing': missing}
