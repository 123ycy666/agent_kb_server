# -*- coding: utf-8 -*-
"""业务库只读访问 + SQL 安全闸。除只读外不含任何业务判断。"""
import re
import threading

import pymysql

from app.secretary import config

_local = threading.local()
FORBID = re.compile(r'(;|--|/\*|\*/)')
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


def _get_conn():
    """每个线程一条连接；用前先 ping 探活，死了就重连。"""
    c = getattr(_local, 'conn', None)
    if c is None:
        c = _new_conn()
        _local.conn = c
        return c
    try:
        c.ping(reconnect=True)
    except Exception:
        _drop_conn()
        c = _new_conn()
        _local.conn = c
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
    """所有取数都走这里；只允许 SELECT / WITH —— 本服务对业务库与字典表都是只读。"""
    low = ' '.join(str(sql).split()).lower()
    if not (low.startswith('select') or low.startswith('with')):
        raise ValueError('只读：不允许执行非 SELECT 语句')
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


def safe_select(sql):
    s = ' '.join(str(sql).split())
    low = s.lower()
    if not (low.startswith('select') or low.startswith('with')):
        raise ValueError('只允许 SELECT 查询')
    if FORBID.search(s):
        raise ValueError('不允许分号或注释')
    if not re.search(r'\blimit\b', low):
        s += ' LIMIT %d' % config.SQL_MAX_ROWS
    return s


def _summarize(columns, rows):
    """给结果集配一份「合计/均值/最小/最大」—— 汇总数由库算，别让模型把几十行自己相加。

    ★ 必须有：实测模型对着 47 行分配额自己加总，报出来的全市合计与库不符
      （1910 万被写成 1950 万 / 1880 万）。明细看得见，不等于加得对。
    只对「至少两个值都能当数字读」的列生成；单行结果（COUNT/SUM 之类）不生成，它本来就一个数。
    """
    out = {}
    for c in columns:
        vals = []
        for row in rows:
            try:
                vals.append(float(row.get(c)))
            except (TypeError, ValueError, AttributeError):
                pass
        if len(vals) < 2:
            continue
        out[c] = {'sum': round(sum(vals), 2), 'mean': round(sum(vals) / len(vals), 2),
                  'min': min(vals), 'max': max(vals), 'n': len(vals)}
        if len(out) >= 12:                      # 宽表别把 summary 撑成第二份明细
            break
    return out


def empty_verdict(sql):
    """0 行到底什么意思 —— **判定只在这一处**。

    三种意思必须分开（2026-11-10）：
      EMPTY_NOT_READY：表里有数据，但请求的期间还没有 → 不能说"没有数据"
      EMPTY_OK       ：期间没超范围、确实是空集 → 才允许说"按某口径没有记录"
      ERROR_DICT     ：表/字段不在字典（由 run_sql 的错误分支给，不走这里）
    判据来自 semantic.period_facts（表的时间列与最新可用期间，带缓存）。
    """
    from app.secretary import semantic
    s = str(sql or '')
    periods = re.findall(r'\b(20\d{4})\b', s)
    for t in sorted(semantic.tables_in_sql(s)):
        pf = semantic.period_facts(t)
        if not pf.get('col') or not pf.get('max'):
            continue
        want = max(periods) if periods else ''
        if want and want > pf['max']:
            return {'code': 'EMPTY_NOT_READY',
                    'note': '%s 最新只到 %s（%s），%s 还没数据' % (t, pf['max'], pf['col'], want)}
    return {'code': 'EMPTY_OK', 'note': ''}


def run_sql(sql):
    """只读 SELECT；表名必须在业务字典里，否则拒绝并回可用表名。"""
    s = safe_select(sql)
    from app.secretary import semantic
    from app.secretary import caliber
    ck = caliber.check_sql(s)                # 闸1：命中契约表就必须带契约声明的必需过滤
    if not ck.get('ok'):
        return {'error': ck['hint'], '判定': ck.get('code') or 'ERROR_CALIBER',
                'missing': ck.get('missing') or [], 'sql': s,
                'columns': [], 'rows': [], 'row_count': 0}
    try:
        r = _exec(s)
    except Exception as e:
        # 把该表的可用字段一起回给模型，省掉一轮“猜字段名”的试错
        hint = ''
        for t in sorted(semantic.tables_in_sql(s))[:1]:
            cols = list(semantic.table_cols(t).keys())
            if cols:
                hint = '表 %s 在业务字典里登记的字段：%s' % (t, '、'.join(cols[:40]))
        return {'error': str(e)[:300], 'hint': hint, 'sql': s,
                'columns': [], 'rows': [], 'row_count': 0}
    r['sql'] = s
    r['summary'] = _summarize(r.get('columns') or [], r.get('rows') or [])
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
