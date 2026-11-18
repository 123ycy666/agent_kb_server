# -*- coding: utf-8 -*-
"""业务库只读访问 + SQL 安全闸。除只读外不含任何业务判断。"""
import re
import threading

import pymysql

from app.secretary import config

_local = threading.local()
NAME_OK = re.compile(r'^[A-Za-z0-9_]+$')


def _new_conn():
    return pymysql.connect(cursorclass=pymysql.cursors.DictCursor, autocommit=True,
                           connect_timeout=8, read_timeout=30, charset='utf8mb4', **config.DB)


def _drop_conn():
    c = getattr(_local, 'conn', None)
    if c is not None:
        try:
            c.close()
        except Exception:
            pass
    _local.conn = None
    _local.key = None


def _key():
    """这条连接是连哪个库建的。页面换库后靠它判断要不要重建。"""
    d = config.DB
    return (d.get('host'), d.get('port'), d.get('db'), d.get('user'))


def _get_conn():
    """每个线程一条连接；配置换了或连接死了就重建。

    ⚠️ 必须比 `_key()`：`runtime_cfg.refresh()` 里的 `_drop_conn()` 只关掉当前线程那条，
       而 run_sql 跑在别的工作线程上 —— 只靠 drop 会出现「换了库仍在查旧库」，
       而 ping 探得通，所以永远不重连（2026-11-18 实测踩到）。
    """
    c = getattr(_local, 'conn', None)
    if c is not None and getattr(_local, 'key', None) != _key():
        _drop_conn()
        c = None
    if c is None:
        c = _new_conn()
        _local.conn = c
        _local.key = _key()
        return c
    try:
        c.ping(reconnect=True)
    except Exception:
        _drop_conn()
        c = _new_conn()
        _local.conn = c
    _local.key = _key()
    return c


def _exec_once(sql, params, limit_rows):
    cur = _get_conn().cursor()
    try:
        cur.execute('SET SESSION MAX_EXECUTION_TIME=%d' % config.SQL_TIMEOUT_MS)
    except Exception:
        pass
    # 注意：必须传 None 而不是 []。传了参数（哪怕是空列表）pymysql 就会对 SQL 做 % 格式化，
    # 任何 LIKE '%关键字%' 都会报 unsupported format character，逼模型把 % 写成 %% 再重试。
    cur.execute(sql, params)
    cols = [d[0] for d in cur.description] if cur.description else []
    rows = cur.fetchmany(limit_rows) if cur.description else []
    cur.close()
    return {'columns': cols, 'rows': rows, 'row_count': len(rows)}


# 只有这几种错误才值得重连重试（连接断了）；SQL 语法/字段名错误重试没有意义
_RETRY_ERRNOS = (2006, 2013, 2055)


def _exec(sql, params=None, limit_rows=config.SQL_MAX_ROWS):
    """所有取数都走这里。只读由业务库账号权限保证，这里不做语句检查。"""
    try:
        return _exec_once(sql, params, limit_rows)
    except pymysql.err.InterfaceError:
        _drop_conn()
        return _exec_once(sql, params, limit_rows)
    except pymysql.err.OperationalError as e:
        if e.args and e.args[0] in _RETRY_ERRNOS:
            _drop_conn()
            return _exec_once(sql, params, limit_rows)
        raise


def _cols_live(table: str) -> list:
    """真库里的列名（业务字典为空时兜底）。"""
    try:
        r = _exec('SELECT column_name AS c FROM information_schema.columns '
                  'WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position',
                  [config.SCHEMA, table], limit_rows=200)
        return [str(x.get('c')) for x in (r.get('rows') or [])]
    except Exception:                              # noqa: BLE001 - 只是提示，取不到就算了
        return []


def _tables_live(limit: int = 60) -> list:
    """当前连的这个库有哪些表。"""
    try:
        r = _exec('SELECT table_name AS t FROM information_schema.tables '
                  'WHERE table_schema=%s ORDER BY table_name', [config.SCHEMA],
                  limit_rows=limit)
        return [str(x.get('t')) for x in (r.get('rows') or [])]
    except Exception:                              # noqa: BLE001
        return []


def run_sql(sql):
    """跑一条 SQL，结果原样返回。只读由业务库账号权限保证，这里不判不拦不汇总。"""
    s = str(sql or '').strip()
    try:
        r = _exec(s)
    except Exception as e:                          # noqa: BLE001 - 报错原样回给模型
        return {'error': str(e)[:300], 'sql': s, 'columns': [], 'rows': [], 'row_count': 0}
    r['sql'] = s
    return r

def find_column(keyword):
    """在业务字典里按关键词搜：真实列名 / 列注释 / 字典中文名 / 表名 / 表说明。

    ★ 2026-11-10 修两个缺口：
      ① `r['rows']` 是 tuple，`.sort()` 直接崩 —— 这只工具每次必挂（实测 q2 连崩 5 次）；
      ② 原来只 LIKE information_schema 的列名与注释，**字典里登记的中文名和表名都不参与匹配**，
         命中字典却没进 information_schema 的行会被整条丢掉（实测「指标」「六项」→ 0 行）。
    """
    from app.secretary import semantic
    allowed = sorted(semantic.allowed_tables())
    if not allowed:
        return {'columns': [], 'rows': [], 'row_count': 0, 'error': '业务字典为空'}
    word = str(keyword).strip()
    kw = '%' + word + '%'
    marks = ','.join(['%s'] * len(allowed))
    r = _exec('SELECT table_name AS tbl_name, column_name AS col_name, column_type AS col_type, '
              'column_comment AS col_comment FROM information_schema.columns '
              'WHERE table_schema=%s AND table_name IN (' + marks + ') '
              'AND (column_name LIKE %s OR column_comment LIKE %s) '
              'ORDER BY table_name, ordinal_position',
              [config.SCHEMA] + allowed + [kw, kw], limit_rows=80)
    dict_cols = {}
    for t, c in semantic.find_columns(word):
        dict_cols[(t, c['column_name'])] = c
    out, seen = [], set()
    # ① 真库列命中（补字典中文名/示例值）
    for row in r['rows']:
        row = dict(row)
        c = dict_cols.get((row['tbl_name'], row['col_name']))
        if c:
            row['col_cn_name'] = c['column_cn_name']
            row['col_example'] = c['example_value']
            row['in_dict'] = 1
        row['match'] = '字段'
        out.append(row)
        seen.add((row['tbl_name'], row['col_name']))
    # ② 只在字典里有、information_schema 没匹配到的列（中文名命中就在这一类）
    for (t, cname), c in dict_cols.items():
        if (t, cname) in seen:
            continue
        out.append({'tbl_name': t, 'col_name': cname, 'col_type': c.get('data_type') or '',
                    'col_comment': c.get('business_desc') or '',
                    'col_cn_name': c.get('column_cn_name') or '',
                    'col_example': c.get('example_value') or '',
                    'in_dict': 1, 'match': '字典'})
        seen.add((t, cname))
    # ③ 表名 / 表说明命中：告诉它"这个词落在哪张表"
    low = word.lower()
    for t in semantic.table_list():
        blob = (t['table_name'] + ' ' + (t['table_cn_name'] or '') + ' '
                + (t['business_desc'] or '')).lower()
        if low and low in blob:
            out.append({'tbl_name': t['table_name'], 'col_name': '（整张表）',
                        'col_type': '', 'col_comment': (t['business_desc'] or '')[:120],
                        'col_cn_name': t['table_cn_name'] or '',
                        'col_example': '', 'in_dict': 1, 'match': '表'})
    order = {'字段': 0, '表': 1, '字典': 2}
    out.sort(key=lambda x: (0 if x.get('in_dict') else 3, order.get(x.get('match'), 9),
                            x.get('tbl_name') or '', x.get('col_name') or ''))
    return {'columns': ['tbl_name', 'col_name', 'col_type', 'col_comment',
                        'col_cn_name', 'col_example', 'in_dict', 'match'],
            'rows': out, 'row_count': len(out)}


def list_tables(keyword=''):
    """只列业务字典里登记的表（白名单）。行数报 0 的用 COUNT 核实，避免把空表当数据源。"""
    from app.secretary import semantic
    info = {}
    try:
        rows = _exec('SELECT table_name AS tbl_name, table_rows AS tbl_rows, update_time AS tbl_update '
                     'FROM information_schema.tables WHERE table_schema=%s',
                     [config.SCHEMA], limit_rows=500)['rows']
        for row in rows:
            info[row['tbl_name']] = row
    except Exception:
        pass
    kw = str(keyword or '').strip().lower()
    listed = semantic.table_list()
    if not listed:
        # 2026-11-11：业务字典没数据时退回 information_schema 直读（只读，办公线用）
        listed = [{'table_name': n, 'table_cn_name': '', 'business_desc': '（字典未登记）',
                   'note': '字典为空，表名取自库内实况'} for n in sorted(info)]
    out = []
    for t in listed:
        name = t['table_name']
        if kw and kw not in (name + ' ' + (t['table_cn_name'] or '') + ' ' + t['business_desc']).lower():
            continue
        src = info.get(name) or {}
        n = src.get('tbl_rows') or 0
        if not n:
            try:
                n = _exec('SELECT COUNT(*) AS c FROM ' + name, limit_rows=1)['rows'][0]['c']
            except Exception:
                n = None
        row = {'tbl_name': name, 'tbl_cn_name': t['table_cn_name'],
               'tbl_comment': t['business_desc'][:150], 'tbl_rows': n,
               'tbl_update': src.get('tbl_update')}
        if t['note']:
            row['note'] = t['note']
        if not n:
            row['note'] = ((row.get('note') or '') + ' ').strip() + '本表当前 0 行，不要用它取数'
        out.append(row)
    return {'columns': ['tbl_name', 'tbl_cn_name', 'tbl_comment', 'tbl_rows', 'tbl_update', 'note'],
            'rows': out, 'row_count': len(out)}


def describe_table(table):
    """看一张表的结构：真库字段 + 业务字典里的中文名与示例值。表必须在字典里。"""
    from app.secretary import semantic
    name = semantic.table_name(table)
    if not NAME_OK.match(name):
        raise ValueError('表名不合法：' + str(table))
    chk = semantic.check_tables([name])      # 表合法性只此一处判
    if not chk['ok']:
        return {'error': '表 %s 不在业务字典里，不能查。%s' % (name, chk['hint'])}
    r = _exec('SELECT column_name AS col_name, column_type AS col_type, '
              'is_nullable AS is_nullable, column_comment AS col_comment '
              'FROM information_schema.columns WHERE table_schema=%s AND table_name=%s '
              'ORDER BY ordinal_position', [config.SCHEMA, name], limit_rows=300)
    cur = semantic.table_cols(name)
    for row in r['rows']:
        c = cur.get(row['col_name'])
        if not c:
            continue
        row['col_cn_name'] = c['column_cn_name']
        row['col_example'] = c['example_value']
        if c['is_dimension']:
            row['col_role'] = '维度'
        elif c['is_metric']:
            row['col_role'] = '指标' + (('(' + c['aggregation_type'] + ')') if c['aggregation_type'] else '')
    r['dict_fields'] = len(cur)
    r['all_fields'] = len(r['rows'])
    try:
        s = _exec('SELECT * FROM ' + name + ' LIMIT 2')
        r['sample_rows'] = s['rows']
        if not s['rows']:
            r['note'] = '本表当前 0 行，不要用它取数'
    except Exception as e:
        r['sample_error'] = str(e)[:120]
    return r


# ── 指标契约的手（2026-11-10）────────────────────────────────────────
# 模型只能报代号，算式与必需过滤由契约（caliber）摊开；派生值也由契约算，
# 模型没有算式入口 —— 这比在提示词里劝它「自己算」硬。
def metric_query(code, year=None, scope_id=None):
    from app.secretary import caliber
    return caliber.query(code, year=year, scope_id=scope_id)


def list_metrics():
    from app.secretary import caliber
    rows = [{'code': m['code'], 'label': m['label'], 'unit': m['unit'],
             'type': '派生' if (m['expr'] or '').strip() else '取数',
             'expr': (m['expr'] or '').strip()} for m in caliber.metrics()]
    return {'columns': ['code', 'label', 'unit', 'type', 'expr'],
            'rows': rows, 'row_count': len(rows)}
