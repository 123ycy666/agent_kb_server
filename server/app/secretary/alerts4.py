# -*- coding: utf-8 -*-
"""四类预警判据（**纯计算，不写库**）。

输入：角色 + 数据范围（alert_scope.resolve 的结果） + 业务日期
输出：0~N 条预警 dict（含 dedupe_key / verify_hint / data），由
      `app/services/alert_service.py` 负责 upsert 落库。

四类：budget / project / review / metric；子判据 13 个（配置见 dispatch_rules.SUBTYPES）。

与旧 `alerts.py` 的关系：
  · 复用它的取数（`_facts`）与三个成熟判据（花不完 / 指标恶化 / 金额存疑）；
  · **不沿用**它按节点拆类型的做法 —— 节点只作为 `data.by_node` 的属性；
  · 不调模型：标题是模板填空（report.py:219 的教训）。
"""
from __future__ import annotations

from datetime import datetime

from app import dispatch_rules as DR
from app.secretary import alerts as A
from app.secretary import alert_rules as R
from app.secretary import tools_db

# 判据阈值（与配置同源）
STAY_BACKLOG_DAYS = DR.STAY_BACKLOG_DAYS
OVERRUN_RATIO = DR.OVERRUN_RATIO
LOSS_RATE_MAX = DR.LOSS_RATE_MAX
ROI_MIN = DR.ROI_MIN
ROI_MIN_INVEST = DR.ROI_MIN_INVEST
NOT_REVIEWED_DAYS = DR.NOT_REVIEWED_DAYS
TITLE_MAX = DR.TITLE_MAX


# ─────────────────────────────────────────────────────────── 工具

def _q(sql, args=None, limit=800):
    return A._all(sql, args, limit=limit)


def _f(v):
    return A._f(v)


def _i(v):
    return A._i(v)


def _wan(v):
    return A.wan(v)


def _marks(n):
    return A._marks(n)


def _norm_company(name) -> str:
    """公司名归一：去「国网」、统一「市供电/县供电」与「供电中心/供电公司」的写法。

    实测同一家县公司有 6 种写法（§5.3 K1），不归一就会漏配。
    """
    n = str(name or '').strip()
    for pre in ('国网', '国网湖北', '湖北省'):
        if n.startswith(pre):
            n = n[len(pre):]
    n = n.replace('供电中心', '供电公司')
    n = n.replace('市供电公司', '供电公司')
    return n


def _scope_key(sc) -> str:
    """所=所 id；县=部门 id；市='city'（**不是**部门 id：部门 id ≠ 所 id）。"""
    if sc['scope_type'] == 'station':
        return str(sc['station_ids'][0])
    if sc['scope_type'] == 'county':
        return str(sc.get('dept_id') or '')
    return 'city'


def _prefix(sc) -> str:
    """标题范围前缀：所「两水供电所：」、县「曾都区公司：」、市不加（句里已有全市）。"""
    if sc['scope_type'] == 'station':
        return '%s：' % (sc.get('scope') or '')
    if sc['scope_type'] == 'county':
        return '%s：' % str(sc.get('scope') or '').replace('供电公司', '公司').replace('供电中心', '中心')
    return ''


def _fit(text: str) -> str:
    t = str(text or '').strip()
    if len(t) <= TITLE_MAX:
        return t
    head, sep, tail = t.partition('，')
    if sep:
        room = TITLE_MAX - len(tail) - 1
        if room > 4:
            return head[:room] + '，' + tail
    return t[:TITLE_MAX - 1] + '…'


def _mk(sc, role_code, subtype, title, data, verify_hint=None,
        object_key='*', object_type='station', level=None, detail=''):
    m = DR.meta(subtype)
    cat = DR.category_of(subtype)
    sk = _scope_key(sc)
    return {
        'alert_type': cat,
        'category_name': DR.CATEGORY_NAMES.get(cat, cat),
        'subtype': subtype,
        'name': m.get('name') or subtype,
        'level': level or m.get('level') or 'mid',
        'title': _fit(title),
        'detail': detail or '',
        'scope_type': sc['scope_type'],
        'scope_key': sk,
        'scope_name': sc.get('scope') or '',
        'object_type': object_type or m.get('object') or 'station',
        'object_key': str(object_key or '*'),
        'assignee_role': role_code,
        'data': data or {},
        'verify_hint': verify_hint or {},
        'dedupe_key': '%s|%s|%s|%s|%s' % (cat, subtype, role_code, sk, object_key or '*'),
        'verify_type': DR.verify_kind(subtype),
    }


def _rate_rule(sc, year, target=0.6):
    return {'kind': 'budget_rate', 'station_ids': list(sc['station_ids']),
            'year': int(year), 'op': '>=', 'target': float(target), 'close_when_met': True}


def _node_rule(sc, node):
    return {'kind': 'audit_node', 'station_ids': list(sc['station_ids']), 'node': node,
            'op': '<=', 'target': 0, 'close_when_met': True}


def _status_rule(sc, status, target=0):
    return {'kind': 'status_count', 'station_ids': list(sc['station_ids']),
            'status': str(status), 'op': '<=', 'target': int(target), 'close_when_met': True}


def _metric_rule(sc, code, target, op='<=', period_type='yearToDate'):
    sk = _scope_key(sc)
    return {'kind': 'metric_value', 'scope_type': sc['scope_type'], 'scope_key': sk,
            'metric_code': code, 'op': op, 'target': float(target),
            'period_type': period_type, 'close_when_met': True}


# ─────────────────────────────────────────────────────────── 预算

def _d_budget_underspend(ctx):
    r = A._budget_underspend(ctx['facts'], ctx['sc'], ctx['today'])
    if not r:
        return []
    d = dict(r.get('data') or {})
    alloc = _f(d.get('alloc'))
    left = _f(d.get('left'))
    approved = max(0.0, alloc - left)
    d['approved'] = round(approved, 2)
    d['rate_pct'] = round((approved / alloc * 100.0) if alloc > 0 else 0.0, 2)
    d['threshold_pct'] = round(R.UNDERSPEND_COEF * 100, 1)
    return [_mk(ctx['sc'], ctx['role'], 'budget.underspend',
                _prefix(ctx['sc']) + r['title'], d,
                _rate_rule(ctx['sc'], ctx['year']),
                object_key='*', object_type='station', detail='按当前审批速度外推到年底')]


def _d_budget_overrun(ctx):
    alloc = approved = 0.0
    stations = []
    for row in A._picked(ctx['facts'], ctx['sc']['station_ids']):
        a = _f(row['alloc'])
        ap = A._sum_wo(row['wo'], R.STATUS_APPROVED)
        alloc += a
        approved += ap
        if a > 0 and ap > a * OVERRUN_RATIO:
            stations.append({'station_id': row['sid'], 'alloc': round(a, 2),
                             'approved': round(ap, 2), 'over': round(ap - a, 2)})
    if alloc <= 0 or approved <= alloc * OVERRUN_RATIO:
        return []
    title = '%s已审批%s，超过分配额%s' % (
        _prefix(ctx['sc']), _wan(approved), _wan(alloc))
    d = {'alloc': round(alloc, 2), 'approved': round(approved, 2),
         'over': round(approved - alloc, 2),
         'rate_pct': round(approved / alloc * 100.0, 2), 'stations': stations[:10]}
    return [_mk(ctx['sc'], ctx['role'], 'budget.overrun', title, d,
                _rate_rule(ctx['sc'], ctx['year'], target=OVERRUN_RATIO),
                object_key='*', object_type='station')]


# ─────────────────────────────────────────────────────────── 项目全过程

def _d_approval_stay(ctx):
    node = ctx['sc'].get('ledger_node') or ''
    if not node:
        return []
    b = (ctx['facts'].get('by_node') or {}).get(node)
    if not b or not b.get('cnt'):
        return []
    over = _i(b.get('over_cnt'))
    level = 'high' if over > 0 else 'mid'
    if over:
        title = '%s%d个项目在%s滞留超%d天' % (
            _prefix(ctx['sc']), over, node, STAY_BACKLOG_DAYS)
    else:
        title = '%s%d个项目在%s待处理' % (_prefix(ctx['sc']), _i(b['cnt']), node)
    by_node = {n: {'total': v['cnt'], 'over_90': v['over_cnt'],
                   'max_days': v['max_days']} for n, v in (ctx['facts']['by_node'] or {}).items()}
    d = {'node': node, 'total': _i(b['cnt']), 'over_90': over,
         'max_days': _i(b['max_days']), 'amount': round(_f(b['amt']), 2),
         'stay_backlog_days': STAY_BACKLOG_DAYS, 'by_node': by_node,
         'coverage_note': '按审批任务表 task_status=0（待处理）实时统计'}
    return [_mk(ctx['sc'], ctx['role'], 'project.approval_stay', title, d,
                _node_rule(ctx['sc'], node), object_key='node:%s' % node,
                object_type='node', level=level)]


def _status_stay_rows(ctx, status):
    """某状态且停留 >N 天的件数（按所）。返回 (cnt, amt, max_days, estimated, ids)。"""
    ids = ctx['sc']['station_ids']
    mk = _marks(len(ids))
    rows = _q(
        'SELECT w.power_company_id AS sid, w.id AS wid, w.budget_total AS amt, '
        'h.last_t AS last_t, w.update_time AS upd '
        'FROM t_power_work_order w '
        'LEFT JOIN (SELECT work_order_id, MAX(create_time) AS last_t '
        '           FROM t_power_work_order_status WHERE is_deleted=0 AND work_status=%s '
        '           GROUP BY work_order_id) h ON h.work_order_id=w.id '
        'WHERE w.deleted_flag=%s AND YEAR(w.create_time)=%s AND w.work_status=%s '
        'AND w.power_company_id IN (' + mk + ')',
        [status, '0', ctx['year'], status] + ids, limit=2000)
    today = datetime.strptime(ctx['today'], '%Y-%m-%d').date()
    out = []
    estimated = 0
    for r in rows:
        t = r.get('last_t') or r.get('upd')
        est = r.get('last_t') is None
        if est:
            estimated += 1
        if not t:
            continue
        try:
            d0 = t.date() if hasattr(t, 'date') else datetime.strptime(str(t)[:10], '%Y-%m-%d').date()
        except (TypeError, ValueError):
            continue
        days = (today - d0).days
        out.append({'sid': _i(r.get('sid')), 'wid': _i(r.get('wid')),
                    'amt': _f(r.get('amt')), 'days': days, 'estimated': est})
    return out, estimated


def _d_approved_not_started(ctx):
    rows, estimated = _status_stay_rows(ctx, R.STATUS_NOT_STARTED)
    hit = [r for r in rows if r['days'] > R.NOT_STARTED_DAYS]
    if not hit:
        return []
    cnt = len(hit)
    amt = sum(r['amt'] for r in hit)
    days = max(r['days'] for r in hit)
    title = '%s%d个项目已审批通过未开工，合计%s，最久%d天' % (
        _prefix(ctx['sc']), cnt, _wan(amt), days)
    d = {'cnt': cnt, 'amount': round(amt, 2), 'max_days': days,
         'threshold_days': R.NOT_STARTED_DAYS, 'estimated': estimated > 0,
         'note': '停留按审批完成时间估算' if estimated else '停留按审批完成时间'}
    return [_mk(ctx['sc'], ctx['role'], 'project.approved_not_started', title, d,
                _status_rule(ctx['sc'], R.STATUS_NOT_STARTED),
                object_type='station')]


def _d_exec_stalled(ctx):
    rows, estimated = _status_stay_rows(ctx, '6')
    hit = [r for r in rows if r['days'] > STAY_BACKLOG_DAYS]
    if not hit:
        return []
    cnt = len(hit)
    amt = sum(r['amt'] for r in hit)
    days = max(r['days'] for r in hit)
    title = '%s%d个项目施工停滞超%d天，合计%s' % (
        _prefix(ctx['sc']), cnt, STAY_BACKLOG_DAYS, _wan(amt))
    d = {'cnt': cnt, 'amount': round(amt, 2), 'max_days': days,
         'threshold_days': STAY_BACKLOG_DAYS, 'estimated': estimated > 0}
    return [_mk(ctx['sc'], ctx['role'], 'project.exec_stalled', title, d,
                _status_rule(ctx['sc'], '6'), object_type='station')]


def _d_acceptance_overdue(ctx):
    rows, _ = _status_stay_rows(ctx, '7')
    hit = [r for r in rows if r['days'] > NOT_REVIEWED_DAYS and not r['estimated']]
    if not hit:
        return []
    cnt = len(hit)
    title = '%s%d个项目完工未上报超%d天' % (_prefix(ctx['sc']), cnt, NOT_REVIEWED_DAYS)
    d = {'cnt': cnt, 'amount': round(sum(r['amt'] for r in hit), 2),
         'max_days': max(r['days'] for r in hit),
         'note': '只用状态历史表时间；无历史的单不判'}
    return [_mk(ctx['sc'], ctx['role'], 'project.acceptance_overdue', title, d,
                _status_rule(ctx['sc'], '7'), object_type='station')]


def _d_amount_structure(ctx):
    facts = ctx['facts']
    zero = facts.get('struct_zero') or []
    big = facts.get('big_amount') or []
    cnt = len(zero) + len(big)
    if not cnt:
        return []
    amt = sum(_f(r.get('budget_total')) for r in zero) + \
        sum(_f(r.get('budget_total')) for r in big)
    title = '%s%d个项目申报金额存疑' % (_prefix(ctx['sc']), cnt)
    d = {'cnt': cnt, 'amount': round(amt, 2),
         'struct_zero': len(zero), 'extreme': len(big),
         'extreme_limit': R.EXTREME_AMOUNT,
         'ids': [{'work_order_id': _i(r.get('id')), 'name': r.get('work_name'),
                  'amount': _f(r.get('budget_total'))}
                 for r in (zero + big)[:20]]}
    return [_mk(ctx['sc'], ctx['role'], 'project.amount_structure', title, d,
                {'kind': 'manual', 'evidence': '修改申报金额后重跑扫描'}, object_type='station')]


# ─────────────────────────────────────────────────────────── 穿透式复盘

def _gov_rows(year):
    return _q('SELECT year, county_company_id AS cid, county_company_name AS name, '
              'total_budget_amount AS invest, total_economic_benefit AS benefit '
              'FROM t_governance_effectiveness WHERE deleted_flag=0 AND year=%s',
              [year], limit=20)


def _pick_gov(sc, year):
    """按范围取治理成效行：市取全市行（cid 为空），县按公司名归一匹配，所不判。"""
    if sc['scope_type'] == 'station':
        return None
    rows = _gov_rows(year)
    if sc['scope_type'] == 'city':
        for r in rows:
            if r.get('cid') in (None, 0, '0'):
                return r
        return None
    target = _norm_company(sc.get('scope'))
    for r in rows:
        if _norm_company(r.get('name')) == target:
            return r
    return None


def _d_low_roi(ctx):
    if ctx['sc']['scope_type'] == 'station':
        return []
    year = int(ctx['year']) - 1
    row = _pick_gov(ctx['sc'], year)
    if not row:
        return []
    invest = _f(row.get('invest'))
    benefit = _f(row.get('benefit'))
    if invest < ROI_MIN_INVEST:
        return []
    ratio = (benefit / invest) if invest > 0 else 0.0
    if ratio >= ROI_MIN:
        return []
    title = '%s%d年投入%s、产出%s，投入产出比%.2f' % (
        _prefix(ctx['sc']), year, _wan(invest), _wan(benefit), ratio)
    d = {'year': year, 'invest': round(invest, 2), 'benefit': round(benefit, 2),
         'ratio': round(ratio, 4), 'threshold': ROI_MIN,
         'note': '按行取治理成效表，不跨行求和（含全市行会翻倍）'}
    return [_mk(ctx['sc'], ctx['role'], 'review.low_roi', title, d,
                {'kind': 'manual', 'evidence': '复盘报告'},
                object_key=str(year), object_type='org')]


def _d_no_effect(ctx):
    """投入同比增、但 ≥2 项指标恶化 —— 需要两年都有投入与产出，当前数据不全。"""
    return []


def _d_not_reviewed(ctx):
    """完工 >90 天且没有复盘任务（候选集合交给 service 与 PG 里的复查任务比对）。"""
    ids = ctx['sc']['station_ids']
    mk = _marks(len(ids))
    rows = _q(
        'SELECT w.id AS wid, w.budget_total AS amt, w.update_time AS upd '
        'FROM t_power_work_order w WHERE w.deleted_flag=%s AND YEAR(w.create_time)=%s '
        'AND w.work_status=%s AND w.power_company_id IN (' + mk + ') '
        'AND DATEDIFF(%s, w.update_time) > %s',
        ['0', ctx['year'], '7'] + ids + [ctx['today'], NOT_REVIEWED_DAYS], limit=500)
    if not rows:
        return []
    d = {'candidate_ids': [_i(r.get('wid')) for r in rows],
         'cnt': len(rows), 'estimated': True,
         'note': '完工时间按 update_time 估算（状态历史对 7 不可靠）'}
    title = '%s%d个项目完工超%d天未复盘' % (_prefix(ctx['sc']), len(rows), NOT_REVIEWED_DAYS)
    return [_mk(ctx['sc'], ctx['role'], 'review.not_reviewed', title, d,
                {'kind': 'manual', 'evidence': '复盘任务闭环'}, object_type='station')]


# ─────────────────────────────────────────────────────────── 指标

METRIC_CODE_BY_NAME = {
    '线损率': 'lossRate',
    '故障跳闸次数': 'tripCount',
    '用户平均停电时间': 'averageOutageDuration',
    '故障报修工单数': 'faultRepairWorkOrder',
    '售电量': 'salesVolume',
    '意见工单数': 'feedbackWorkOrder',
}


def _d_metric_deteriorated(ctx):
    sc = ctx['sc']
    r = A._metric_deteriorated(ctx['facts'], sc, ctx['today'])
    if not r:
        return []
    items = (r.get('data') or {}).get('items') or []
    worst = max(items, key=lambda m: abs(_f(m.get('change_rate')))) if items else {}
    name = str(worst.get('metric') or '')
    code = METRIC_CODE_BY_NAME.get(name) or 'metric'
    base = _f(worst.get('base'))
    d = {'period': (r.get('data') or {}).get('period'), 'items': items,
         'worst': {'metric': name, 'code': code,
                   'current': _f(worst.get('current')), 'base': base,
                   'change_rate': _f(worst.get('change_rate'))},
         'excluded': ['salesVolume'], 'note': '售电量累计口径未对齐=假恶化，已排除'}
    title = _prefix(sc) + r['title']
    hint = (_metric_rule(sc, code, base, op='<=', period_type='year')
            if code != 'metric' and base else {'kind': 'manual', 'evidence': '下期指标改善确认'})
    return [_mk(sc, ctx['role'], 'metric.deteriorated', title, d, hint,
                object_key=code, object_type='metric')]


def _d_metric_threshold(ctx):
    sc = ctx['sc']
    per = A._one('SELECT MAX(period_key) AS k FROM t_power_ai_metric_snapshot '
                 'WHERE deleted_flag=0 AND period_type=%s', ['yearToDate'])
    key = str(per.get('k') or '')
    if not key:
        return []
    if sc['scope_type'] == 'station':
        names = sc.get('station_names') or []
        if not names:
            return []
        mk = _marks(len(names))
        rows = _q('SELECT scope_name, metric_value FROM t_power_ai_metric_snapshot '
                  'WHERE deleted_flag=0 AND period_type=%s AND period_key=%s '
                  "AND metric_code='lossRate' AND scope_type='station' "
                  'AND scope_name IN (' + mk + ')',
                  ['yearToDate', key] + names, limit=50)
    elif sc['scope_type'] == 'county':
        rows = _q('SELECT scope_name, metric_value FROM t_power_ai_metric_snapshot '
                  'WHERE deleted_flag=0 AND period_type=%s AND period_key=%s '
                  "AND metric_code='lossRate' AND scope_type='district'",
                  ['yearToDate', key], limit=50)
        target = _norm_company(sc.get('scope'))
        rows = [r for r in rows if _norm_company(r.get('scope_name')) == target]
    else:
        rows = _q('SELECT scope_name, metric_value FROM t_power_ai_metric_snapshot '
                  'WHERE deleted_flag=0 AND period_type=%s AND period_key=%s '
                  "AND metric_code='lossRate' AND scope_type='city'",
                  ['yearToDate', key], limit=5)
    hit = [r for r in rows if r.get('metric_value') is not None
           and _f(r.get('metric_value')) > LOSS_RATE_MAX]
    if not hit:
        return []
    worst = max(hit, key=lambda r: _f(r.get('metric_value')))
    val = _f(worst.get('metric_value'))
    title = '%s台区线损率%.2f%%，高于%.0f%%优秀线' % (_prefix(sc), val, LOSS_RATE_MAX)
    d = {'metric': 'lossRate', 'metric_name': '台区线损率', 'value': round(val, 2),
         'threshold': LOSS_RATE_MAX, 'period': key,
         'scope_name': worst.get('scope_name'), 'hit_count': len(hit)}
    return [_mk(sc, ctx['role'], 'metric.threshold', title, d,
                _metric_rule(sc, 'lossRate', LOSS_RATE_MAX),
                object_key='lossRate', object_type='metric')]


def _d_metric_stale(ctx):
    return []


DETECTORS = {
    'budget.underspend': _d_budget_underspend,
    'budget.overrun': _d_budget_overrun,
    'project.approval_stay': _d_approval_stay,
    'project.approved_not_started': _d_approved_not_started,
    'project.exec_stalled': _d_exec_stalled,
    'project.acceptance_overdue': _d_acceptance_overdue,
    'project.amount_structure': _d_amount_structure,
    'review.low_roi': _d_low_roi,
    'review.no_effect': _d_no_effect,
    'review.not_reviewed': _d_not_reviewed,
    'metric.deteriorated': _d_metric_deteriorated,
    'metric.threshold': _d_metric_threshold,
    'metric.stale': _d_metric_stale,
}


# ─────────────────────────────────────────────────────────── 主入口

def detect(role_code, sc, today, facts, cadence='daily'):
    """返回该角色在该范围、该频率下命中的预警列表（已过小额过滤）。"""
    ctx = {'sc': sc, 'role': role_code, 'today': today,
           'year': str(today)[:4], 'facts': facts}
    out = []
    errors = []
    min_amt = R.min_amount()
    for subtype in DR.enabled_types(role_code, cadence):
        fn = DETECTORS.get(subtype)
        if not fn:
            continue
        try:
            items = fn(ctx) or []
        except Exception as exc:  # noqa: BLE001 - 单条判据失败不许拖垮整轮
            errors.append({'subtype': subtype, 'error': '%s: %s' % (type(exc).__name__, str(exc)[:200])})
            continue
        for a in items:
            if min_amt and _f(a.get('data', {}).get('amount')) and _f(a['data']['amount']) < min_amt:
                continue
            out.append(a)
    return out, errors


__all__ = ['detect', 'DETECTORS', '_norm_company']
