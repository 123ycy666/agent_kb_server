"""文档结构：五段定死，少一段不存、多一个槽不认；保存时逐槽校验。

形状：
{
  "source": {"db":"biz","tables":[{"table","alias","columns":[]}],
             "joins":[{"left","right","type"}], "filter":{...}, "limit":200000},
  "calc":   {"grain":[{"name","ref"}], "fields":[{"name","expr"}],
             "row_policy":"latest|sum|max|min|first", "order_ref":""},
  "judge":  {"rules":[{"when":[...],"op":"AND","then":{"hit":true,"tier":""}}],
             "default":{"hit":false,"tier":""}},
  "write":  {"db":"biz","table":"","mode":"update|upsert|insert",
             "key":[{"target","from"}], "map":[{"target","from"|"value"}], "miss":"keep|clear|expire"},
  "note":   ""
}
"""
from __future__ import annotations

from typing import Any

from app.wf import meta
from app.wf.expr import AGG_FUNCS, Expr, ExprError

COND_OPS = {
    'isnull': '为空', 'notnull': '不为空', 'eq': '等于', 'ne': '不等于',
    'gt': '大于', 'ge': '大于等于', 'lt': '小于', 'le': '小于等于',
    'in': '属于', 'notin': '不属于', 'contains': '包含', 'starts': '开头是', 'ends': '结尾是',
}
RESERVED = ('hit', 'tier')


def blank() -> dict:
    return {
        'source': {'db': meta.BIZ, 'tables': [], 'joins': [],
                   'filter': {'op': 'AND', 'items': []}, 'limit': 200000},
        'calc': {'grain': [], 'fields': [], 'row_policy': 'first', 'order_ref': ''},
        'judge': {'rules': [], 'default': {'hit': False, 'tier': ''}},
        'write': {'db': meta.BIZ, 'table': '', 'mode': 'update',
                  'key': [], 'map': [], 'miss': 'keep'},
        'note': '',
    }


# ── 引用解析 ────────────────────────────────────────────────────────────
def source_refs(spec: dict) -> dict[str, tuple[str, str]]:
    """能引用的来源列：`别名.列` 一定可以，`列` 在不重名时也可以。"""
    out: dict[str, tuple[str, str]] = {}
    plain: dict[str, list[str]] = {}
    for t in (spec.get('source', {}) or {}).get('tables', []) or []:
        alias = str(t.get('alias') or '')
        for c in t.get('columns') or []:
            out['%s.%s' % (alias, c)] = (alias, c)
            plain.setdefault(c, []).append(alias)
    for c, aliases in plain.items():
        if len(aliases) == 1 and c not in out:
            out[c] = (aliases[0], c)
    return out


def result_names(spec: dict) -> list[str]:
    """计算结果里能引用的名字（分组列 + 计算列 + 判断产生的 hit / tier）。"""
    names: list[str] = []
    calc = spec.get('calc', {}) or {}
    for g in calc.get('grain', []) or []:
        n = str(g.get('name') or '').strip()
        if n and n not in names:
            names.append(n)
    for f in calc.get('fields', []) or []:
        n = str(f.get('name') or '').strip()
        if n and n not in names and n not in RESERVED:
            names.append(n)
    return names + list(RESERVED)


def allowed_names(spec: dict) -> set[str]:
    """判断与回填能引用的名字：计算结果 + 本轮取到的列。"""
    return set(result_names(spec)) | set(source_refs(spec).keys())


# ── 校验 ────────────────────────────────────────────────────────────────
def _err(section: str, slot: str, message: str) -> dict:
    return {'section': section, 'slot': slot, 'message': message}


def validate(spec: dict) -> list[dict]:
    errors: list[dict] = []
    spec = spec or {}
    src = spec.get('source') or {}
    calc = spec.get('calc') or {}
    judge = spec.get('judge') or {}
    write = spec.get('write') or {}

    db = str(src.get('db') or meta.BIZ)
    tables = src.get('tables') or []
    if not tables:
        errors.append(_err('source', 'tables', '至少要选一张来源表'))

    aliases: dict[str, dict] = {}
    for t in tables:
        table = str(t.get('table') or '')
        alias = str(t.get('alias') or '').strip()
        if not table:
            errors.append(_err('source', 'tables', '有一张来源表没选表名'))
            continue
        if not alias:
            errors.append(_err('source', 'tables', '表 %s 没起别名' % table))
            continue
        if alias in aliases:
            errors.append(_err('source', 'tables', '别名 %s 重复' % alias))
            continue
        info = meta.get_table(db, table)
        if info is None:
            errors.append(_err('source', 'tables', '元数据里没有表 %s' % table))
            continue
        cols = t.get('columns') or []
        if not cols:
            errors.append(_err('source', 'tables', '表 %s 一列都没选' % table))
        known = {c['name'] for c in info['columns']}
        for c in cols:
            if str(c) not in known:
                errors.append(_err('source', 'tables', '表 %s 没有列 %s' % (table, c)))
        aliases[alias] = {'table': table, 'columns': [str(c) for c in cols]}

    refs = source_refs(spec)

    for j in src.get('joins') or []:
        left, right = str(j.get('left') or ''), str(j.get('right') or '')
        for side in (left, right):
            if side and side not in refs:
                errors.append(_err('source', 'joins', '关联条件里的 %s 不是已选的列' % side))
        if left and right and left.split('.')[0] == right.split('.')[0]:
            errors.append(_err('source', 'joins', '关联两边不能是同一张表'))
        if str(j.get('type') or 'inner') not in ('inner', 'left'):
            errors.append(_err('source', 'joins', '关联方式只能是内连或左连'))

    def check_filter(items, path):
        for it in items or []:
            kind = str(it.get('kind') or 'cond')
            if kind == 'group':
                if str(it.get('op') or 'AND').upper() not in ('AND', 'OR'):
                    errors.append(_err('source', 'filter', '条件组只能用 AND 或 OR'))
                check_filter(it.get('items') or [], path + 1)
                continue
            ref = str(it.get('ref') or '')
            op = str(it.get('op') or '')
            if ref not in refs:
                errors.append(_err('source', 'filter', '筛选条件里的 %s 不是已选的列' % ref) if ref
                              else _err('source', 'filter', '有一条筛选条件没选列'))
            if op not in COND_OPS:
                errors.append(_err('source', 'filter', '运算 %s 不认识' % op))

    check_filter((src.get('filter') or {}).get('items'), 0)

    # calc
    seen = set()
    for g in calc.get('grain') or []:
        name = str(g.get('name') or '').strip()
        ref = str(g.get('ref') or '').strip()
        if not name:
            errors.append(_err('calc', 'grain', '分组列没起名字'))
            continue
        if name in RESERVED:
            errors.append(_err('calc', 'grain', '%s 是保留名，换一个' % name))
        if name in seen:
            errors.append(_err('calc', 'grain', '名字 %s 重复' % name))
        seen.add(name)
        if ref not in refs:
            errors.append(_err('calc', 'grain', '分组列 %s 指向的 %s 不是已选的列' % (name, ref)))

    for f in calc.get('fields') or []:
        name = str(f.get('name') or '').strip()
        text = str(f.get('expr') or '').strip()
        if not name:
            errors.append(_err('calc', 'fields', '有一个计算列没起名字'))
            continue
        if name in RESERVED:
            errors.append(_err('calc', 'fields', '%s 是保留名，换一个' % name))
        if name in seen:
            errors.append(_err('calc', 'fields', '名字 %s 重复' % name))
        seen.add(name)
        usable = set(refs) | (seen - {name})
        try:
            ex = Expr(text)
        except ExprError as exc:
            errors.append(_err('calc', 'fields', '%s 的式子写不通：%s' % (name, exc)))
            continue
        for n in ex.names:
            if n not in usable:
                errors.append(_err('calc', 'fields',
                                   '%s 的式子引用了 %s，它既不是已选的列，也不是前面算出来的列'
                                   % (name, n)))

    if not calc.get('grain'):
        errors.append(_err('calc', 'grain', '至少要选一个分组列（一行代表什么）'))
    if str(calc.get('row_policy') or 'first') not in ('first', 'latest', 'sum', 'max', 'min'):
        errors.append(_err('calc', 'row_policy', '同键多行的取法不认识'))

    # judge
    names = result_names(spec)
    allowed = names + [x for x in source_refs(spec).keys()]
    for idx, rule in enumerate(judge.get('rules') or [], 1):
        when = rule.get('when') or []
        if not when:
            errors.append(_err('judge', 'rules', '第 %d 条没有条件' % idx))
        for cond in when:
            ref = str(cond.get('ref') or '')
            op = str(cond.get('op') or '')
            if ref not in allowed:
                errors.append(_err('judge', 'rules', '第 %d 条的条件列 %s 既不是计算结果也不是已选的列' % (idx, ref)))
            if op not in COND_OPS:
                errors.append(_err('judge', 'rules', '第 %d 条的运算 %s 不认识' % (idx, op)))
        then = rule.get('then') or {}
        if not isinstance(then.get('hit'), bool):
            errors.append(_err('judge', 'rules', '第 %d 条没写命中还是不命中' % idx))
    if not (judge.get('rules') or []):
        errors.append(_err('judge', 'rules', '至少要有一条判断规则'))

    # write
    wtable = str(write.get('table') or '')
    wdb = str(write.get('db') or meta.BIZ)
    if not wtable:
        errors.append(_err('write', 'table', '还没选回填到哪张表'))
    else:
        info = meta.get_table(wdb, wtable)
        if info is None:
            errors.append(_err('write', 'table', '元数据里没有表 %s' % wtable))
        else:
            known = {c['name'] for c in info['columns']}
            key = write.get('key') or []
            if not key:
                errors.append(_err('write', 'key', '没设匹配键'))
            for k in key:
                if str(k.get('target') or '') not in known:
                    errors.append(_err('write', 'key', '目标表没有列 %s' % k.get('target')))
                if str(k.get('from') or '') not in allowed:
                    errors.append(_err('write', 'key', '匹配键取值的 %s 既不是计算结果也不是已选的列' % k.get('from')))
            uniques = [set(u) for u in (info.get('uniques') or [])]
            if info.get('primary'):
                uniques.append(set(info['primary']))
            key_cols = {str(k.get('target') or '') for k in key if k.get('target')}
            if uniques and not any(u <= key_cols for u in uniques):
                errors.append(_err('write', 'key', '匹配键没盖住 %s 的唯一键，更新会写错行' % wtable))
            if not write.get('map'):
                errors.append(_err('write', 'map', '没有一列要回填'))
            for m in write.get('map') or []:
                tgt = str(m.get('target') or '')
                if tgt not in known:
                    errors.append(_err('write', 'map', '目标表没有列 %s' % tgt))
                if 'from' in m and str(m.get('from') or '') not in allowed:
                    errors.append(_err('write', 'map', '列 %s 取值的 %s 既不是计算结果也不是已选的列' % (tgt, m.get('from'))))
                if 'from' not in m and 'value' not in m:
                    errors.append(_err('write', 'map', '列 %s 没写取哪个值' % tgt))
    if str(write.get('mode') or 'update') not in ('update', 'upsert', 'insert'):
        errors.append(_err('write', 'mode', '写模式不认识'))
    if str(write.get('miss') or 'keep') != 'keep':
        errors.append(_err('write', 'miss', '本期只支持「本轮没算到的行保留不动」'))
    return errors


def normalize(spec: dict) -> dict:
    """把前端可能少传的槽补成默认值，多传的键丢掉。"""
    base = blank()
    spec = spec or {}
    out: dict[str, Any] = {}
    for sec in ('source', 'calc', 'judge', 'write'):
        merged = dict(base[sec])
        merged.update(spec.get(sec) or {})
        out[sec] = merged
    out['source'].setdefault('filter', {'op': 'AND', 'items': []})
    out['source'].setdefault('tables', [])
    out['source'].setdefault('joins', [])
    out['calc'].setdefault('grain', [])
    out['calc'].setdefault('fields', [])
    out['judge'].setdefault('rules', [])
    out['judge'].setdefault('default', {'hit': False, 'tier': ''})
    out['write'].setdefault('key', [])
    out['write'].setdefault('map', [])
    out['note'] = str(spec.get('note') or '')
    return out


def uses_aggregate(spec: dict) -> bool:
    for f in (spec.get('calc') or {}).get('fields') or []:
        try:
            if Expr(str(f.get('expr') or '')).aggregated:
                return True
        except ExprError:
            continue
    return False


__all__ = ['blank', 'validate', 'normalize', 'source_refs', 'result_names',
           'uses_aggregate', 'COND_OPS', 'RESERVED', 'AGG_FUNCS']
