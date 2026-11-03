# -*- coding: utf-8 -*-
"""纠错词典的页面读写（2026-11-02）。

这四张表在**本系统 PG**（`ad_new`）里，不在业务库：

    t_nc_typo       错别字 → 正确写法（105 条）
    t_nc_term       术语与别名（75 条）
    t_nc_ambiguous  同音/近形歧义（81 条）
    t_nc_wordlist   标准词表（197 条）

`t_nc_catalog`（2 万条地名/台区索引）和 `t_nc_pinyin`（2 万字表）**不开放手改** ——
它们是离线编译产物，改一个字要重算全表，从页面改只会把索引改坏。

★ 改完怎么生效：`name_fix._maybe_reload()` 每 60 秒比一次 `MAX(update_time)` 指纹，
  一变就把纠错引擎整体重建 —— 所以这里每次写都必须 `update_time = now()`，
  否则改了不生效（name_fix 原注释里就强调过：物理 DELETE 改不动 update_time，
  探测不到）。删除也因此走 `deleted_flag = 1` 软删。
"""
from __future__ import annotations

from app.secretary import pgdb

# key -> 表结构与可编辑列（列白名单：页面只能改这些，其余列一律不接受）
TABLES = {
    'typo': {
        'table': 't_nc_typo',
        'name': '错别字纠正',
        'note': '把用户打错的字改成正确写法。改完最多 60 秒生效。',
        'cols': [('wrong', '错误写法'), ('right_word', '正确写法'),
                 ('org', '适用组织'), ('freq_level', '频率等级'),
                 ('reason', '错因类型'), ('sample', '纠正示例')],
        'search': ('wrong', 'right_word', 'org'),
        'required': ('wrong', 'right_word'),
    },
    'term': {
        'table': 't_nc_term',
        'name': '术语与别名',
        'note': '术语定义与常见别名，用于口径归一。',
        'cols': [('term', '术语'), ('definition', '定义'), ('alias', '常见表达'),
                 ('org', '适用组织')],
        'search': ('term', 'alias', 'definition'),
        'required': ('term',),
    },
    'ambiguous': {
        'table': 't_nc_ambiguous',
        'name': '歧义确认',
        'note': '同音/近形、系统不敢自己定的，按 action 处理（如「反问确认」）。',
        'cols': [('py_code', '拼音码'), ('name_a', '名称 A'), ('kind_a', '类别 A'),
                 ('name_b', '名称 B'), ('kind_b', '类别 B'), ('action', '处理方式')],
        'search': ('name_a', 'name_b', 'py_code'),
        'required': ('name_a', 'name_b'),
    },
    'wordlist': {
        'table': 't_nc_wordlist',
        'name': '标准词表',
        'note': '标准词及拼音码/首字母；拼音留空会按字表自动补。',
        'cols': [('word', '标准词'), ('domain', '领域'), ('src', '来源'),
                 ('py_full', '拼音码'), ('initials', '首字母')],
        'search': ('word', 'domain'),
        'required': ('word',),
    },
}


def _spec(key):
    return TABLES.get(str(key or '').strip())


def _conn(autocommit: bool = True):
    return pgdb.connect(autocommit=autocommit)


def overview() -> list[dict]:
    """四张表的概览：行数 + 列定义（页面据此渲染表格与编辑框）。"""
    out = []
    for key, spec in TABLES.items():
        n = 0
        try:
            conn = _conn()
            cur = conn.cursor()
            try:
                cur.execute('SELECT count(*) n FROM %s WHERE deleted_flag = 0' % spec['table'])
                n = int((cur.fetchone() or {}).get('n') or 0)
            finally:
                cur.close()
                conn.close()
        except Exception as exc:                       # noqa: BLE001
            print('[lex_admin] 统计 %s 失败：%s' % (key, str(exc)[:120]), flush=True)
        out.append({'key': key, 'name': spec['name'], 'note': spec['note'], 'rows': n,
                    'cols': [{'key': c, 'label': lb} for c, lb in spec['cols']]})
    return out


def rows(key: str, q: str = '', limit: int = 50, offset: int = 0) -> dict:
    """分页查一张表；q 在 search 列里做 ILIKE 模糊匹配。"""
    spec = _spec(key)
    if not spec:
        return {'error': '未知词表：%s' % key}
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    cols = ['id'] + [c for c, _ in spec['cols']] + ['update_time']
    where = ['deleted_flag = 0']
    params: list = []
    q = str(q or '').strip()
    if q:
        ors = []
        for c in spec['search']:
            ors.append('%s ILIKE %%s' % c)
            params.append('%' + q + '%')
        where.append('(' + ' OR '.join(ors) + ')')
    where_sql = ' AND '.join(where)
    try:
        conn = _conn()
        cur = conn.cursor()
        try:
            cur.execute('SELECT count(*) n FROM %s WHERE %s' % (spec['table'], where_sql),
                        tuple(params))
            total = int((cur.fetchone() or {}).get('n') or 0)
            cur.execute('SELECT %s FROM %s WHERE %s ORDER BY id DESC LIMIT %%s OFFSET %%s'
                        % (','.join(cols), spec['table'], where_sql),
                        tuple(params + [limit, offset]))
            data = [dict(r) for r in (cur.fetchall() or [])]
        finally:
            cur.close()
            conn.close()
    except Exception as exc:                           # noqa: BLE001
        return {'error': '查询失败：%s' % str(exc)[:200]}
    return {'ok': True, 'key': key, 'name': spec['name'], 'total': total,
            'limit': limit, 'offset': offset,
            'cols': [{'key': c, 'label': lb} for c, lb in spec['cols']],
            'rows': data}


def _clean(spec, data, partial: bool = False) -> tuple[dict, str]:
    """只保留白名单列并转字符串；required 列不能为空。"""
    out = {}
    for c, _label in spec['cols']:
        if c in (data or {}):
            v = data.get(c)
            out[c] = str(v if v is not None else '')
        elif not partial:
            out[c] = ''
    if not partial:
        labels = dict(spec['cols'])
        for c in spec.get('required', ()):
            if not str(out.get(c) or '').strip():
                return {}, '「%s」不能为空' % labels.get(c, c)
    return out, ''


def _fill_pinyin(spec: dict, data: dict) -> dict:
    """标准词表：首字母留空时按 `t_nc_pinyin` 字表补。

    `initials` 是声母串（`业扩报装` → `ykbz`），跟库里既有数据一致，可以放心补。

    `py_full` **不补**：库里存的是离线编译出来的混合码（`业扩报装` → `ekobaozuan`，
    不是纯韵母也不是纯全拼），格式没法从字表可靠复现。瞎填一个值会让模糊音匹配
    出奇怪的候选，比留空更糟 —— 留空就只按汉字 + 首字母匹配。
    """
    if spec['table'] != 't_nc_wordlist':
        return data
    word = str(data.get('word') or '')
    if not word or data.get('initials'):
        return data
    try:
        conn = _conn()
        cur = conn.cursor()
        try:
            cur.execute('SELECT hanzi, initial FROM t_nc_pinyin WHERE hanzi = ANY(%s)',
                        (list(word),))
            m = {r['hanzi']: r for r in (cur.fetchall() or [])}
        finally:
            cur.close()
            conn.close()
    except Exception:                                  # noqa: BLE001
        return data
    data['initials'] = ''.join(str((m.get(ch) or {}).get('initial') or '') for ch in word)
    return data


def create(key: str, data: dict) -> dict:
    spec = _spec(key)
    if not spec:
        return {'error': '未知词表：%s' % key}
    row, err = _clean(spec, data)
    if err:
        return {'error': err}
    row = _fill_pinyin(spec, row)
    cols = list(row)
    sql = ('INSERT INTO %s (%s, deleted_flag, update_time) VALUES (%s, 0, now()) '
           'RETURNING id' % (spec['table'], ','.join(cols),
                             ','.join(['%s'] * len(cols))))
    conn = None
    try:
        conn = _conn()
        cur = conn.cursor()
        try:
            cur.execute(sql, tuple(row[c] for c in cols))
            # ★ 用 lastrowid，不要再 fetchone()：pgdb 的兼容层在 execute 内部已经把
            #   RETURNING 那一行消费掉了，这里再取是空的（实测 id 恒为 0）。
            new_id = int(cur.lastrowid or 0)
        finally:
            cur.close()
        return {'ok': True, 'id': new_id, 'key': key, 'rows': row}
    except Exception as exc:                           # noqa: BLE001
        return {'error': '新增失败：%s' % str(exc)[:200]}
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:                              # noqa: BLE001
            pass


def update(key: str, rid: int, data: dict) -> dict:
    spec = _spec(key)
    if not spec:
        return {'error': '未知词表：%s' % key}
    row, err = _clean(spec, data)
    if err:
        return {'error': err}
    row = _fill_pinyin(spec, row)
    sets = ','.join('%s = %%s' % c for c in row)
    sql = ('UPDATE %s SET %s, update_time = now() WHERE id = %%s AND deleted_flag = 0'
           % (spec['table'], sets))
    conn = None
    try:
        conn = _conn()
        cur = conn.cursor()
        try:
            cur.execute(sql, tuple(row[c] for c in row) + (int(rid),))
            if not cur.rowcount:
                return {'error': '没这一行（或已删除）：%s' % rid}
        finally:
            cur.close()
    except Exception as exc:                           # noqa: BLE001
        return {'error': '保存失败：%s' % str(exc)[:200]}
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:                              # noqa: BLE001
            pass
    return {'ok': True, 'id': int(rid), 'key': key}


def delete(key: str, rid: int) -> dict:
    """软删：`deleted_flag = 1` + `update_time = now()`（物理删改不动版本指纹）。"""
    spec = _spec(key)
    if not spec:
        return {'error': '未知词表：%s' % key}
    conn = None
    try:
        conn = _conn()
        cur = conn.cursor()
        try:
            cur.execute('UPDATE %s SET deleted_flag = 1, update_time = now() '
                        'WHERE id = %%s AND deleted_flag = 0' % spec['table'], (int(rid),))
            if not cur.rowcount:
                return {'error': '没这一行（或已删除）：%s' % rid}
        finally:
            cur.close()
    except Exception as exc:                           # noqa: BLE001
        return {'error': '删除失败：%s' % str(exc)[:200]}
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:                              # noqa: BLE001
            pass
    return {'ok': True, 'id': int(rid), 'key': key, 'deleted': True}
