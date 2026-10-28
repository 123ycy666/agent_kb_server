# -*- coding: utf-8 -*-
"""核心指标的口径兜底：**固定 SQL + 固定参数**，不让模型自由拼。

为什么要有这个文件（2026-10-21 实测发现）
--------------------------------------
同一个问题「环潭所 2026 年预算执行率」连跑三次，得到两种答案：
22.79%（把在途工单算进已审批）与 0.00%（只认 1–9 月创建的已审批工单）。
两次都"有 SQL 出处"，所以数字守卫拦不住 —— 它只管出处，不管口径。

口径这种"业务常识"，交给模型每次现编必漂。做法：**高频指标写成参数化查询**，
模型只负责传参数（哪个所、哪年、截至哪天），SQL 与口径文案都不由模型决定。

本文件只放"口径明确的指标"，不是通用查询器：
- budget_execution：预算执行率 = 已审批工单预算合计 ÷ 当年分配预算
  · 已审批 = work_status IN (3,6,7)（审核通过待施工 / 施工中 / 完工待验收）
  · 时间字段 = create_time，默认窗口 = 当年 1 月 1 日至 as_of
- work_order_mix：工单状态分布（给"为什么是 0"提供证据）

只读：全部走 tools_db._exec（只允许 SELECT）。
"""
from __future__ import annotations

from datetime import date

from app.secretary import tools_db

APPROVED_STATUS = (3, 6, 7)          # 已审批口径（与 business_rules 一致）
CALIBER = ('执行率 = 当年已审批工单预算合计 ÷ 当年分配预算；'
           '已审批 = 状态 3（审核通过待施工）/ 6（施工中）/ 7（完工待验收）；'
           '时间字段 = create_time；默认窗口 = 当年 1 月 1 日至统计日')


class MetricError(RuntimeError):
    pass


def _q(sql, params=None, limit=50) -> list[dict]:
    r = tools_db._exec(sql, params, limit_rows=limit)     # noqa: SLF001（只读闸在里面）
    if r.get('error'):
        raise MetricError(str(r['error'])[:200])
    return [{(k or '').lower(): v for k, v in row.items()} for row in (r.get('rows') or [])]


def budget_execution(station: str, year: int | None = None,
                     as_of: str | None = None) -> dict:
    """某所在某年的预算执行率（口径固定）。返回数字 + 口径 + 证据。"""
    name = (station or '').strip()
    if not name:
        raise MetricError('缺少供电所名称')
    today = date.today()
    y = int(year or today.year)
    end = (as_of or '').strip() or today.isoformat()
    start = '%d-01-01' % y
    if end < start:
        raise MetricError('统计截止日 %s 早于 %s 年初，口径不成立' % (end, y))

    alloc = _q('SELECT power_station_name, year, budget_amount '
               'FROM t_power_company_budget_result '
               'WHERE power_station_name LIKE %s AND year = %s AND deleted_flag = 0',
               ['%' + name + '%', y], limit=5)
    if not alloc:
        raise MetricError('没有查到「%s」%d 年的预算分配记录' % (name, y))
    station_full = alloc[0].get('power_station_name') or name
    alloc_amt = float(alloc[0].get('budget_amount') or 0)

    stat = _q('SELECT COUNT(*) AS cnt, COALESCE(SUM(budget_total), 0) AS amt '
              'FROM t_power_work_order '
              "WHERE power_company_name = %s AND deleted_flag = '0' "
              'AND work_status IN (3, 6, 7) AND create_time >= %s AND create_time < %s',
              [station_full, start, end], limit=5)
    cnt = int((stat[0].get('cnt') if stat else 0) or 0)
    approved = float((stat[0].get('amt') if stat else 0) or 0)
    rate = (approved / alloc_amt * 100.0) if alloc_amt else None

    mix = _q('SELECT work_status, COUNT(*) AS cnt, COALESCE(SUM(budget_total), 0) AS amt '
             'FROM t_power_work_order '
             "WHERE power_company_name = %s AND deleted_flag = '0' "
             'AND create_time >= %s AND create_time < %s '
             'GROUP BY work_status ORDER BY work_status',
             [station_full, start, end], limit=20)

    return {'metric': 'budget_execution', 'station': station_full,
            'year': y, 'window': [start, end],
            'allocated_yuan': alloc_amt, 'approved_yuan': approved, 'approved_orders': cnt,
            'rate_pct': None if rate is None else round(rate, 2),
            'approved_status': list(APPROVED_STATUS), 'caliber': CALIBER,
            'by_status': [{'status': int(r.get('work_status') or 0),
                           'orders': int(r.get('cnt') or 0),
                           'amount_yuan': float(r.get('amt') or 0)} for r in mix]}


def as_markdown(m: dict) -> str:
    """给人看的版本（模型可以据此作答，不必再算）。"""
    lines = ['%s %d 年预算执行率：%s' % (
        m['station'], m['year'],
        ('%.2f%%' % m['rate_pct']) if m['rate_pct'] is not None else '无法计算'),
        '口径：%s' % m['caliber'],
        '统计窗口：%s 至 %s' % (m['window'][0], m['window'][1]),
        '当年分配预算：%.2f 万元' % (m['allocated_yuan'] / 10000.0),
        '已审批工单：%d 张，预算合计 %.2f 万元' % (m['approved_orders'],
                                            m['approved_yuan'] / 10000.0)]
    if m.get('by_status'):
        parts = ['状态 %d %d 张 %.2f 万' % (x['status'], x['orders'], x['amount_yuan'] / 10000.0)
                 for x in m['by_status']]
        lines.append('窗口内各状态分布：' + '；'.join(parts))
    return '\n'.join(lines)


# ── 注册给模型用（工具定义与调用入口）──────────────────────────────
TOOL = {'type': 'function', 'function': {
    'name': 'budget_execution',
    'description': '预算执行率（口径已固定，**不要自己写 SQL 算这个**）。'
                   '问「某所某年预算执行率 / 预算花了多少 / 还剩多少」时调用它。'
                   '返回：分配预算、已审批预算（状态 3/6/7）、执行率、窗口内各状态分布，'
                   '并带口径说明与统计窗口。',
    'parameters': {'type': 'object', 'properties': {
        'station': {'type': 'string', 'description': '供电所名称（可只写简称，如「环潭」）'},
        'year': {'type': 'integer', 'description': '年份，默认当前年'},
        'as_of': {'type': 'string', 'description': '统计截止日 YYYY-MM-DD，默认今天'}},
        'required': ['station']}}}


def handle(args: dict) -> dict:
    """工具调用入口：返回给模型看的结果（markdown + 数字 + 口径）。"""
    try:
        m = budget_execution(args.get('station') or '', args.get('year'), args.get('as_of'))
    except MetricError as exc:
        return {'error': str(exc)}
    return {'markdown': as_markdown(m), 'numbers': {
                'allocated_yuan': m['allocated_yuan'], 'approved_yuan': m['approved_yuan'],
                'approved_orders': m['approved_orders'], 'rate_pct': m['rate_pct']},
            'window': m['window'], 'caliber': m['caliber']}

