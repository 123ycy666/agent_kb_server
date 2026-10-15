# -*- coding: utf-8 -*-
"""每日预警：10 类判据 + 一件事一句话。

三段式：**取数（每条 SQL 都带同一套范围 + 年度）→ 判据（每类返回 dict 或 None）→ 拼句**。

四条纪律（都是踩过坑之后定的）：
  1. **不调模型**。标题是模板填空 —— 模型每次现写同一请求会数出不同结果（report.py:219 的教训）。
  2. **只返回有异常的**。判据不命中就返回 None。"花得完就不发"是硬要求。
  3. **一条一件事**。预算花不完、未审批积压是两件事 → 两条，不合并成一句。
  4. **语气用他们的话**：未审批 / 未审核 / 待审批 / 已通过；
     **不许**出现「卡在 / 单子 / 卡住 / 超标」——这些是我们的分析词，不是他们的业务词。
"""
import time

import alert_rules as R
import alert_scope
import tools_db

TITLE_MAX = 40            # 一句话上限（汉字数）
NODE_NAME_MAX = 8         # 节点名拼进标题时截断，避免超字数


# ─────────────────────────────────────────────────────────── 工具

def _one(sql, args=None):
    rows = _all(sql, args, limit=1)
    return rows[0] if rows else {}


def _all(sql, args=None, limit=800):
    r = tools_db._exec(sql, args, limit_rows=limit)
    return [{(k or '').lower(): v for k, v in row.items()} for row in (r.get('rows') or [])]


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _i(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def wan(yuan):
    """元 → 万元字符串，两位小数。"""
    return '%.2f万' % (_f(yuan) / 10000.0)


def _marks(n):
    return ','.join(['%s'] * n)


def _node(node):
    n = str(node or '')
    return n if len(n) <= NODE_NAME_MAX else n[:NODE_NAME_MAX] + '…'


def _num(v):
    """指标值去掉无意义的小数尾（962.0000 → 962；1.9316 → 1.93），省字数。"""
    x = _f(v)
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    return ('%.2f' % x).rstrip('0').rstrip('.')


# ─────────────────────────────────────────────────────────── 取数

def _facts(station_ids, year, today):
    """一次把该范围需要的数全取出来。**每条 SQL 都带 station_id IN (范围) + 年度。**"""
    ids = [int(x) for x in station_ids]
    mk = _marks(len(ids))
    f = {'year': year, 'today': today, 'station_ids': ids}

    # 1) 分配额（所级，该年唯一有效行）
    f['alloc'] = {}
    for r in _all('SELECT power_station_id AS sid, SUM(budget_amount) AS amt '
                  'FROM t_power_company_budget_result WHERE deleted_flag=0 AND year=%s '
                  'AND power_station_id IN (' + mk + ') GROUP BY power_station_id',
                  [year] + ids):
        f['alloc'][_i(r['sid'])] = _f(r['amt'])

    # 2) 工单按所 + 状态：件数与金额（当年创建）
    f['wo'] = {}
    for r in _all('SELECT power_company_id AS sid, work_status AS st, COUNT(*) AS cnt, '
                  'SUM(budget_total) AS amt, MAX(budget_total) AS maxamt '
                  'FROM t_power_work_order WHERE deleted_flag=%s AND YEAR(create_time)=%s '
                  'AND power_company_id IN (' + mk + ') '
                  'GROUP BY power_company_id, work_status', ['0', year] + ids):
        f['wo'].setdefault(_i(r['sid']), {})[str(r['st'])] = {
            'cnt': _i(r['cnt']), 'amt': _f(r['amt']), 'max': _f(r['maxamt'])}

    # 2.5) 本范围当年单项目金额中位数（pipeline 口径要用）。
    #      单独查、只取预算列，避免把 work_order 宽表整行拉回来（实测宽表拉 2 万行要 20 秒）。
    vals = sorted(_f(r['b']) for r in _all(
        'SELECT budget_total AS b FROM t_power_work_order WHERE deleted_flag=%s '
        'AND YEAR(create_time)=%s AND budget_total>0 AND power_company_id IN (' + mk + ')',
        ['0', year] + ids, limit=20000))
    f['mid_amount'] = vals[len(vals) // 2] if vals else 0.0

    # 3) 审批待办（停留天数 + 节点），按所汇总
    f['audit'] = {}
    for r in _all('SELECT w.power_company_id AS sid, a.node_name AS node, COUNT(*) AS cnt, '
                  'SUM(w.budget_total) AS amt, MAX(DATEDIFF(%s, a.create_time)) AS max_days, '
                  'SUM(CASE WHEN DATEDIFF(%s, a.create_time) > %s THEN 1 ELSE 0 END) AS over_cnt '
                  'FROM t_work_order_audit_task a JOIN t_power_work_order w ON w.id=a.work_order_id '
                  'WHERE a.is_deleted=0 AND a.task_status=0 AND w.deleted_flag=%s '
                  'AND w.power_company_id IN (' + mk + ') GROUP BY w.power_company_id, a.node_name',
                  [today, today, R.STAY_BACKLOG_DAYS, '0'] + ids):
        f['audit'].setdefault(_i(r['sid']), []).append({
            'node': str(r['node'] or ''), 'cnt': _i(r['cnt']), 'amt': _f(r['amt']),
            'max_days': _i(r['max_days']), 'over_cnt': _i(r['over_cnt'])})

    # 4) 按审批节点（供「停留超期」按角色节点算）
    f['by_node'] = {}
    for r in _all('SELECT a.node_name AS node, COUNT(*) AS cnt, SUM(w.budget_total) AS amt, '
                  'MAX(DATEDIFF(%s, a.create_time)) AS max_days, '
                  'SUM(CASE WHEN DATEDIFF(%s, a.create_time) > %s THEN 1 ELSE 0 END) AS over_cnt '
                  'FROM t_work_order_audit_task a JOIN t_power_work_order w ON w.id=a.work_order_id '
                  'WHERE a.is_deleted=0 AND a.task_status=0 AND w.deleted_flag=%s '
                  'AND w.power_company_id IN (' + mk + ') GROUP BY a.node_name',
                  [today, today, R.STAY_BACKLOG_DAYS, '0'] + ids):
        f['by_node'][str(r['node'] or '')] = {
            'cnt': _i(r['cnt']), 'amt': _f(r['amt']),
            'max_days': _i(r['max_days']), 'over_cnt': _i(r['over_cnt'])}

    # 5) 金额结构异常（工程费或物资费为 0）
    f['struct_zero'] = _all(
        'SELECT id, work_name, power_company_name, budget_total, '
        'budget_engineering, budget_material FROM t_power_work_order '
        'WHERE deleted_flag=%s AND YEAR(create_time)=%s AND power_company_id IN (' + mk + ') '
        'AND (budget_engineering=0 OR budget_material=0) ORDER BY budget_total DESC',
        ['0', year] + ids, limit=200)

    # 6) 指标恶化：只取**年度同比**口径（跳闸/线损的同比记在 period_type='year' 上；
    #    月度口径里恶化的只有售电量，而售电量的累计口径没对齐 = 假恶化，必须排除）。
    f['metrics'] = []
    f['metric_period'] = ''
    if R.ABNORMAL_METRICS:
        per = _one('SELECT MAX(current_period_key) AS k FROM t_power_ai_metric_comparison '
                   "WHERE deleted_flag=0 AND period_type='year'")
        key = per.get('k')
        if key:
            f['metric_period'] = str(key)
            mnames = _marks(len(R.ABNORMAL_METRICS))
            f['metrics'] = _all(
                'SELECT scope_type, scope_name, metric_code, metric_name, current_value, '
                'base_value, change_rate, trend_result FROM t_power_ai_metric_comparison '
                'WHERE deleted_flag=0 AND current_period_key=%s AND period_type=%s '
                'AND metric_code IN (' + mnames + ') AND trend_result=%s',
                [key, 'year'] + list(R.ABNORMAL_METRICS) + ['deteriorated'])
    return f


def _picked(facts, station_ids):
    out = []
    for sid in station_ids:
        out.append({'sid': int(sid),
                    'alloc': _f(facts['alloc'].get(int(sid))),
                    'wo': facts['wo'].get(int(sid)) or {},
                    'audit': facts['audit'].get(int(sid)) or []})
    return out


def _sum_wo(wo, statuses, field='amt'):
    return sum(_f((wo.get(s) or {}).get(field)) for s in statuses)


def _cnt_wo(wo, statuses):
    return sum(_i((wo.get(s) or {}).get('cnt')) for s in statuses)


def _head(sc):
    return '全市' if sc['scope_type'] == 'city' else sc['scope']


def _stations_of(sc, ids):
    m = dict(zip(sc['station_ids'], sc['station_names']))
    return [m.get(i, '') for i in ids]


# ─────────────────────────────────────────────────────────── 判据

def _budget_underspend(facts, sc, today):
    """预测：**按这个花法，年底花得完吗**。花得完 → None（不发）。"""
    em = R.elapsed_months(today)
    ratio = 12.0 / em
    mid = _f(facts.get('mid_amount'))
    hit = []
    for r in _picked(facts, sc['station_ids']):
        alloc = r['alloc']
        if alloc <= 0:
            continue
        approved = _sum_wo(r['wo'], R.STATUS_APPROVED)
        if R.forecast_mode() == 'pipeline':
            monthly = sum(_i(v.get('cnt')) for v in r['wo'].values()) / em
            can_do = monthly * max(0.0, 12.0 - em)
            need = (alloc - approved) / mid if mid > 0 else 0.0
            bad = mid > 0 and need > can_do / R.UNDERSPEND_COEF
            detail = {'need_projects': round(need, 1), 'can_do': round(can_do, 1)}
        else:
            year_end = approved * ratio
            bad = year_end < alloc * R.UNDERSPEND_COEF
            detail = {'year_end': round(year_end, 2)}
        if bad:
            hit.append({'sid': r['sid'], 'alloc': alloc, 'approved': approved,
                        'left': alloc - approved, 'detail': detail})
    if not hit:
        return None
    hit.sort(key=lambda x: -x['left'])
    tot_left = sum(x['left'] for x in hit)
    tot_alloc = sum(x['alloc'] for x in hit)
    zero = sum(1 for x in hit if x['approved'] <= 0)
    if sc['scope_type'] == 'station':
        h = hit[0]
        title = '%s：分配%s已通过%s，按此进度年底结余%s' % (
            sc['scope'], wan(h['alloc']), wan(h['approved']), wan(h['left']))
    else:
        scope_alloc = sum(_f(facts['alloc'].get(i)) for i in sc['station_ids'])
        head = '全市' if sc['scope_type'] == 'city' else sc['scope']
        extra = '，%d个所零审批' % zero if zero else ''
        title = '%s：%d个所超期未动，共分%s%s' % (head, len(hit), wan(scope_alloc), extra)
        tot_alloc = scope_alloc
    return {'type': 'budget_underspend', 'title': title, 'total': len(hit), 'amount': tot_left,
            'data': {'alloc': round(tot_alloc, 2), 'left': round(tot_left, 2),
                     'zero_stations': zero, 'stations': len(hit),
                     'worst': [{'station': s, 'left': round(x['left'], 2)}
                               for s, x in zip(_stations_of(sc, [y['sid'] for y in hit]), hit)][:5],
                     **hit[0]['detail']}}


def _zero_execution(facts, sc, today):
    """零执行：县/市级才有意义（所级零执行就是它自己的预算花不完）。"""
    if sc['scope_type'] == 'station':
        return None
    zero = [r for r in _picked(facts, sc['station_ids'])
            if r['alloc'] > 0 and _sum_wo(r['wo'], R.STATUS_APPROVED) <= 0]
    if not zero:
        return None
    amt = sum(r['alloc'] for r in zero)
    if sc['scope_type'] == 'county':
        title = '%s：%d个所今年未审批通过一笔，分配%s' % (sc['scope'], len(zero), wan(amt))
    else:
        title = '全市%d个所今年未审批通过一笔，分配%s' % (len(zero), wan(amt))
    return {'type': 'zero_execution', 'title': title, 'total': len(zero), 'amount': amt,
            'data': {'alloc': round(amt, 2), 'stations': len(zero)}}


def _approval_backlog(facts, sc, today):
    """未审批积压：状态 0 草稿 / 1 提交审核 / 9 驳回已修改（判据规则 三.2）。"""
    cnt = amt = over = 0
    for r in _picked(facts, sc['station_ids']):
        cnt += _cnt_wo(r['wo'], R.STATUS_UNAPPROVED)
        amt += _sum_wo(r['wo'], R.STATUS_UNAPPROVED)
        over += sum(a['over_cnt'] for a in r['audit'])
    if not cnt:
        return None
    if sc['scope_type'] == 'station':
        title = '%s：截至今日%d个项目未审批%s' % (sc['scope'], cnt, wan(amt))
    else:
        title = '%s：截至今日%d个项目未审批%s' % (_head(sc), cnt, wan(amt))
    if over:
        title += '，%d个停留超90天' % over
    return {'type': 'approval_backlog', 'title': title, 'total': cnt, 'amount': amt,
            'data': {'cnt': cnt, 'amount': round(amt, 2), 'over_90': over}}


def _approved_not_started(facts, sc, today):
    """已批未开工：状态 3（审核通过待施工）且已等待超 30 天。"""
    hit = []
    for r in _picked(facts, sc['station_ids']):
        st3 = r['wo'].get(R.STATUS_NOT_STARTED) or {}
        if _i(st3.get('cnt')) <= 0:
            continue
        days = max([a['max_days'] for a in r['audit']] or [0])
        if days > R.NOT_STARTED_DAYS:
            hit.append({'sid': r['sid'], 'cnt': _i(st3['cnt']), 'amt': _f(st3['amt']), 'days': days})
    if not hit:
        return None
    cnt = sum(x['cnt'] for x in hit)
    amt = sum(x['amt'] for x in hit)
    days = max(x['days'] for x in hit)
    title = '%s：%d个项目已通过审批未开工%s，最久%d天' % (
        _head(sc) if sc['scope_type'] != 'station' else sc['scope'], cnt, wan(amt), days)
    return {'type': 'approved_not_started', 'title': title, 'total': cnt, 'amount': amt,
            'data': {'cnt': cnt, 'amount': round(amt, 2), 'max_days': days}}


def _node_stay_overdue(facts, sc, today):
    """审批停留超期：只算**本角色对应节点**的待办 —— 判据规则十二节禁止拿全库量当某人待办。"""
    node = sc.get('ledger_node') or ''
    if not node:
        return None
    b = facts['by_node'].get(node)
    if not b or not b['cnt']:
        return None
    if b['over_cnt']:
        title = '%s：%d个项目在%s停留超90天' % (sc['scope'], b['over_cnt'], _node(node))
    else:
        title = '%s：%d个项目在%s待处理' % (sc['scope'], b['cnt'], _node(node))
    return {'type': 'node_stay_overdue', 'title': title, 'total': b['cnt'], 'amount': b['amt'],
            'data': {'node': node, 'cnt': b['cnt'], 'over_90': b['over_cnt'],
                     'max_days': b['max_days'], 'amount': round(b['amt'], 2)}}


def _node_backlog(facts, sc, today):
    """卡在哪个环节：按节点分布（判据规则 19.2 的「集中在哪」）。"""
    if not facts['by_node']:
        return None
    agg = {}
    for node, b in facts['by_node'].items():
        a = agg.setdefault(node, {'cnt': 0})
        a['cnt'] += b['cnt']
    top = sorted(agg.items(), key=lambda kv: -kv[1]['cnt'])[:2]
    detail = '、'.join('%s%d个' % (_node(n), v['cnt']) for n, v in top)
    title = '%s：%d个项目待审批，%s' % (_head(sc), sum(v['cnt'] for _, v in top), detail)
    return {'type': 'node_backlog', 'title': title,
            'total': sum(v['cnt'] for _, v in top),
            'data': {'by_node': {n: v['cnt'] for n, v in top}}}


def _data_quality_amount(facts, sc, today):
    rows = facts['struct_zero']
    if not rows:
        return None
    tot = sum(_f(r['budget_total']) for r in rows)
    title = '%s：%d个项目工程费或物资费为零' % (_head(sc), len(rows))
    return {'type': 'data_quality_amount', 'title': title, 'total': len(rows), 'amount': tot,
            'data': {'cnt': len(rows), 'amount': round(tot, 2)},
            'items': [{'work_order_id': _i(r['id']), 'name': r['work_name'],
                       'station': r['power_company_name'], 'amount': _f(r['budget_total']),
                       'engineering': _f(r['budget_engineering']),
                       'material': _f(r['budget_material'])} for r in rows[:20]]}


def _data_quality_status(facts, sc, today):
    cnt = amt = 0
    for r in _picked(facts, sc['station_ids']):
        cnt += _cnt_wo(r['wo'], (R.STATUS_UNREGISTERED,))
        amt += _sum_wo(r['wo'], (R.STATUS_UNREGISTERED,))
    if not cnt:
        return None
    title = '%s：%d个项目状态未登记%s' % (_head(sc), cnt, wan(amt))
    return {'type': 'data_quality_status', 'title': title, 'total': cnt, 'amount': amt,
            'data': {'cnt': cnt, 'amount': round(amt, 2)}}


def _data_quality_missing(facts, sc, today):
    """计划完工日缺失 —— 台账层面的问题，不上报业务人（判据规则只看数量）。"""
    if sc['scope_type'] == 'station':
        return None
    ids = sc['station_ids']
    mk = _marks(len(ids))
    r = _one('SELECT COUNT(*) AS cnt, SUM(CASE WHEN plan_end_time IS NULL THEN 1 ELSE 0 END) AS miss '
             'FROM t_power_work_order WHERE deleted_flag=%s AND YEAR(create_time)=%s '
             'AND power_company_id IN (' + mk + ')', ['0', facts['year']] + ids)
    cnt, miss = _i(r.get('cnt')), _i(r.get('miss'))
    if not cnt or not miss:
        return None
    title = '%s：今年%d个项目均未填计划完工日期' % (_head(sc), cnt)
    return {'type': 'data_quality_missing', 'title': title, 'total': miss,
            'data': {'cnt': cnt, 'missing': miss}}


def _metric_deteriorated(facts, sc, today):
    """指标恶化：只推跳闸次数与线损率；售电量口径未对齐（假恶化）已在上游排除。"""
    if sc['scope_type'] == 'station':
        return None
    status = 'abnormal' if facts['metrics'] else 'ok'
    hit = []
    for m in facts['metrics']:
        sn = str(m.get('scope_name') or '')
        if sc['scope_type'] == 'city' and sn in ('随州市', '全市'):
            hit.append(m)
        elif sc['scope_type'] == 'county' and sn == sc['scope']:
            hit.append(m)
    if not hit:
        return None
    worst = max(hit, key=lambda m: abs(_f(m['change_rate'])))
    pct = _f(worst['change_rate']) * 100.0
    title = '%s：%s同比%s%.1f%%（%s→%s）' % (
        _head(sc), worst['metric_name'], '上升' if pct > 0 else '下降', abs(pct),
        _num(worst['base_value']), _num(worst['current_value']))
    return {'type': 'metric_deteriorated', 'title': title, 'total': len(hit),
            'data': {'period': facts.get('metric_period'),
                     'items': [{'metric': m['metric_name'], 'current': _f(m['current_value']),
                                'base': _f(m['base_value']),
                                'change_rate': _f(m['change_rate'])} for m in hit]}}


JUDGES = {
    'budget_underspend': _budget_underspend,
    'zero_execution': _zero_execution,
    'approval_backlog': _approval_backlog,
    'approved_not_started': _approved_not_started,
    'node_stay_overdue': _node_stay_overdue,
    'node_backlog': _node_backlog,
    'data_quality_amount': _data_quality_amount,
    'data_quality_status': _data_quality_status,
    'data_quality_missing': _data_quality_missing,
    'metric_deteriorated': _metric_deteriorated,
}

# 判据执行顺序（也是并列时的优先次序：钱的 → 停留的 → 积压的 → 质量的 → 指标的）
ORDER = ['budget_underspend', 'zero_execution', 'node_stay_overdue', 'approval_backlog',
         'approved_not_started', 'node_backlog', 'data_quality_amount', 'data_quality_status',
         'metric_deteriorated', 'data_quality_missing']


# ─────────────────────────────────────────────────────────── 主函数

def daily(account, today=None, when=None, limit=10):
    """账号 → 今天该办的几件事。一件事一条，每条一句话，只返回有异常的。"""
    t0 = time.time()
    today = (today or time.strftime('%Y-%m-%d'))[:10]
    when = when or time.strftime('%Y-%m-%d %H:%M:%S')
    try:
        limit = max(1, min(50, int(limit)))
    except (TypeError, ValueError):
        limit = 10

    sc = alert_scope.resolve(account)
    base = {'account': sc.get('account') or (account or ''), 'name': sc.get('name') or '',
            'role_code': sc.get('role_code') or '', 'role': sc.get('role_name') or '',
            'scope': sc.get('scope') or '', 'scope_type': sc.get('scope_type') or '',
            'today': today, 'triggered_at': when,
            'checked_at': time.strftime('%Y-%m-%d %H:%M:%S')}
    if not sc.get('ok'):
        return dict(base, status='unknown', alerts=[], error=sc.get('error') or 'unknown',
                    elapsed_ms=int((time.time() - t0) * 1000))

    types = R.ROLE_ALERTS.get(sc['role_code']) or []
    base['station_ids'] = sc['station_ids']
    if not types:
        return dict(base, status='ok', alerts=[], note='该角色本期不推送预警',
                    elapsed_ms=int((time.time() - t0) * 1000))

    facts = _facts(sc['station_ids'], R.year_of(today), today)
    min_amt = R.min_amount()
    alerts = []
    for code in ORDER:
        if code not in types:
            continue
        fn = JUDGES.get(code)
        if not fn:
            continue
        try:
            a = fn(facts, sc, today)
        except Exception as e:                          # noqa: BLE001
            a = {'type': code, 'level': 'low',
                 'title': '（%s 取数失败）' % R.ALERT_TYPES[code]['name'],
                 'total': 0, 'error': '%s: %s' % (type(e).__name__, str(e)[:120])}
        if not a:
            continue
        if min_amt and _f(a.get('amount')) and _f(a['amount']) < min_amt:
            continue
        a.setdefault('level', R.ALERT_TYPES[code]['level'])
        a['name'] = R.ALERT_TYPES[code]['name']
        if len(a['title']) > TITLE_MAX:
            a['title'] = a['title'][:TITLE_MAX - 1] + '…'
        if a.get('items'):
            a['returned'] = min(len(a['items']), limit)
            a['items'] = a['items'][:limit]
        alerts.append(a)

    alerts.sort(key=lambda x: R.LEVEL_WEIGHT.get(x.get('level'), 9))
    cap = R.MAX_ALERTS.get(sc['role_code'])
    suppressed = []
    if cap is not None and len(alerts) > cap:
        suppressed = [a['type'] for a in alerts[cap:]]
        alerts = alerts[:cap]
    return dict(base, status='abnormal' if alerts else 'ok', alerts=alerts,
                suppressed=suppressed,
                elapsed_ms=int((time.time() - t0) * 1000))
