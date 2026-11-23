"""编译：把五段配置翻成一条 SELECT 和一套回填动作。人看的就是这里出来的原文。"""
from __future__ import annotations

from app.wf import meta
from app.wf.spec import COND_OPS, source_refs


def _q(db: str, ident: str) -> str:
    name = str(ident or '')
    if db == meta.FW:
        return '"' + name.replace('"', '""') + '"'
    return '`' + name.replace('`', '``') + '`'


def _coerce(v):
    """条件值：像数字就给数字，其余原样。"""
    if isinstance(v, (int, float, bool)) or v is None:
        return v
    s = str(v).strip()
    if s == '':
        return ''
    try:
        if s.lstrip('-').isdigit():
            return int(s)
        return float(s)
    except ValueError:
        return s


class Sql:
    """带占位符的 SQL 与参数。`text` 与 `params` 成对使用。"""

    def __init__(self, db: str):
        self.db = db
        self.params: list = []
        self.text = ''

    def ph(self, value) -> str:
        self.params.append(_coerce(value))
        return '%s'


def _cond_sql(c: dict, refs: dict, sql: Sql, db: str) -> str:
    ref = str(c.get('ref') or '')
    target = refs.get(ref)
    if target is None:
        raise ValueError('条件里的 %s 不是已选的列' % ref)
    col = '%s.%s' % (_q(db, target[0]), _q(db, target[1]))
    op = str(c.get('op') or '')
    val = c.get('value')
    if op == 'isnull':
        return '(%s IS NULL)' % col
    if op == 'notnull':
        return '(%s IS NOT NULL)' % col
    if op == 'eq':
        return '(%s = %s)' % (col, sql.ph(val))
    if op == 'ne':
        return '(%s <> %s)' % (col, sql.ph(val))
    if op == 'gt':
        return '(%s > %s)' % (col, sql.ph(val))
    if op == 'ge':
        return '(%s >= %s)' % (col, sql.ph(val))
    if op == 'lt':
        return '(%s < %s)' % (col, sql.ph(val))
    if op == 'le':
        return '(%s <= %s)' % (col, sql.ph(val))
    if op in ('in', 'notin'):
        vals = val if isinstance(val, list) else [x.strip() for x in str(val or '').split(',') if x.strip()]
        if not vals:
            return '(1=0)' if op == 'in' else '(1=1)'
        holes = ', '.join(sql.ph(v) for v in vals)
        kw = 'IN' if op == 'in' else 'NOT IN'
        return '(%s %s (%s))' % (col, kw, holes)
    if op == 'contains':
        return '(%s LIKE %s)' % (col, sql.ph('%' + str(val or '') + '%'))
    if op == 'starts':
        return '(%s LIKE %s)' % (col, sql.ph(str(val or '') + '%'))
    if op == 'ends':
        return '(%s LIKE %s)' % (col, sql.ph('%' + str(val or '')))
    raise ValueError('不认识的运算 %s' % op)


def _filter_sql(flt: dict, refs: dict, sql: Sql, db: str) -> str:
    items = (flt or {}).get('items') or []
    if not items:
        return ''
    chunks = []
    for it in items:
        if str(it.get('kind') or 'cond') == 'group':
            inner = _filter_sql(it, refs, sql, db)
            if inner:
                chunks.append('(' + inner + ')')
            continue
        chunks.append(_cond_sql(it, refs, sql, db))
    joiner = ' OR ' if str((flt or {}).get('op') or 'AND').upper() == 'OR' else ' AND '
    return joiner.join(chunks)


def build_select(spec: dict) -> dict:
    """返回 {db, tables, sql, params, columns}；columns 是行里的键名（别名__列）。"""
    src = spec.get('source') or {}
    db = str(src.get('db') or meta.BIZ)
    sql = Sql(db)
    refs = source_refs(spec)

    select_parts: list[str] = []
    columns: list[str] = []
    for t in src.get('tables') or []:
        alias = str(t.get('alias'))
        for c in t.get('columns') or []:
            key = '%s__%s' % (alias, c)
            columns.append(key)
            select_parts.append('%s.%s AS %s' % (_q(db, alias), _q(db, c), _q(db, key)))

    first = (src.get('tables') or [{}])[0]
    frm = '%s %s' % (_q(db, str(first.get('table'))), _q(db, str(first.get('alias'))))

    for j in src.get('joins') or []:
        left = str(j.get('left') or '').split('.', 1)
        right = str(j.get('right') or '').split('.', 1)
        if len(left) != 2 or len(right) != 2:
            raise ValueError('关联条件写法不对：%s = %s' % (j.get('left'), j.get('right')))
        right_alias = right[0]
        tinfo = next((x for x in src.get('tables') or [] if str(x.get('alias')) == right_alias), None)
        if tinfo is None:
            raise ValueError('关联里用到的别名 %s 不在来源表里' % right_alias)
        kind = 'LEFT JOIN' if str(j.get('type') or 'inner') == 'left' else 'INNER JOIN'
        frm += ' %s %s %s ON %s.%s = %s.%s' % (
            kind, _q(db, str(tinfo.get('table'))), _q(db, right_alias),
            _q(db, left[0]), _q(db, left[1]), _q(db, right[0]), _q(db, right[1]))

    where = _filter_sql(src.get('filter') or {}, refs, sql, db)
    body = 'SELECT %s\nFROM %s' % (',\n       '.join(select_parts), frm)
    if where:
        body += '\nWHERE ' + where
    limit = int(src.get('limit') or 200000)
    body += '\nLIMIT %d' % max(1, min(limit, 500000))
    sql.text = body
    return {'db': db, 'sql': sql.text, 'params': sql.params, 'columns': columns}


def write_plan(spec: dict) -> dict:
    """回填动作：目标表、写哪些列、按什么键匹配。"""
    w = spec.get('write') or {}
    db = str(w.get('db') or meta.BIZ)
    return {
        'db': db,
        'table': str(w.get('table') or ''),
        'mode': str(w.get('mode') or 'update'),
        'key': [{'target': str(k.get('target') or ''), 'from': str(k.get('from') or '')}
                for k in (w.get('key') or [])],
        'map': [dict(m) for m in (w.get('map') or [])],
        'miss': str(w.get('miss') or 'keep'),
    }


def pick(row: dict, name: str):
    """取一个值：先认计算结果里的名字，再认本轮取到的列。"""
    if name in row:
        return row.get(name)
    src = row.get('__src__') or {}
    return src.get(name)


def build_update(plan: dict, row: dict) -> tuple[str, list]:
    """按一行结果拼一条 UPDATE。"""
    db = plan['db']
    sets, params = [], []
    for m in plan['map']:
        sets.append('%s = %%s' % _q(db, m.get('target')))
        params.append(_map_value(m, row))
    where, wparams = [], []
    for k in plan['key']:
        where.append('%s = %%s' % _q(db, k['target']))
        wparams.append(pick(row, k['from']))
    sql = 'UPDATE %s SET %s WHERE %s' % (_q(db, plan['table']), ', '.join(sets), ' AND '.join(where))
    return sql, params + wparams


def build_insert(plan: dict, row: dict) -> tuple[str, list]:
    db = plan['db']
    cols, holes, params = [], [], []
    for k in plan['key']:
        cols.append(_q(db, k['target']))
        holes.append('%s')
        params.append(pick(row, k['from']))
    for m in plan['map']:
        if m.get('target') in [k['target'] for k in plan['key']]:
            continue
        cols.append(_q(db, m.get('target')))
        holes.append('%s')
        params.append(_map_value(m, row))
    sql = 'INSERT INTO %s (%s) VALUES (%s)' % (_q(db, plan['table']), ', '.join(cols), ', '.join(holes))
    return sql, params


def _map_value(m: dict, row: dict):
    if 'from' in m and m.get('from'):
        return pick(row, str(m.get('from')))
    return m.get('value')


def preview_write(plan: dict) -> str:
    """回填语句的样子（给前端看，不执行）。"""
    db = plan['db']
    t = _q(db, plan['table'])
    sets = ', '.join('%s = ?' % _q(db, m.get('target')) for m in plan['map'])
    where = ' AND '.join('%s = ?' % _q(db, k['target']) for k in plan['key'])
    head = 'UPDATE %s SET %s WHERE %s;' % (t, sets or '—', where or '—')
    if plan['mode'] in ('insert', 'upsert'):
        cols = ', '.join(_q(db, k['target']) for k in plan['key'] +
                         [m for m in plan['map'] if m.get('target') not in [k['target'] for k in plan['key']]])
        head += '\n-- 没有对应行时：INSERT INTO %s (%s) VALUES (...);' % (t, cols)
    return head


def compile_all(spec: dict) -> dict:
    sel = build_select(spec)
    plan = write_plan(spec)
    return {
        'select': {'sql': sel['sql'], 'params': sel['params'], 'db': sel['db']},
        'select_preview': _preview(sel['sql'], sel['params']),
        'write': plan,
        'write_preview': preview_write(plan),
    }


def _preview(sql_text: str, params: list) -> str:
    parts = sql_text.split('%s')
    if len(parts) == 1:
        return sql_text
    buf = [parts[0]]
    for idx, seg in enumerate(parts[1:]):
        v = params[idx] if idx < len(params) else None
        if isinstance(v, str):
            buf.append("'" + v.replace("'", "''") + "'")
        elif v is None:
            buf.append('NULL')
        else:
            buf.append(repr(v))
        buf.append(seg)
    return ''.join(buf)


__all__ = ['build_select', 'write_plan', 'build_update', 'build_insert',
           'preview_write', 'compile_all', 'COND_OPS']
