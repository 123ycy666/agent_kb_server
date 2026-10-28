# -*- coding: utf-8 -*-
"""每日预警的口径与角色归属（**配置层**：改预警只改这里）。

两条纪律：
  1. 这里只放「收哪些、怎么算、阈值多少」，不写逻辑、不查库。
  2. **不写死任何业务数**（分配额、所数、金额都不许出现在这里）。
     —— 唯一的例外是判据规则里业务方定的**阈值**（90 天、0.6 系数），它们不是业务数据。
"""
import os
import time

# ── 预警类型 ────────────────────────────────────────────────────────────
# scope_level: station=所级明细口径 / county=县汇总口径 / city=全市汇总口径 / any=都行
ALERT_TYPES = {
    'budget_underspend':    {'name': '预算花不完预警',   'level': 'high', 'scope_level': 'any'},
    'zero_execution':       {'name': '零执行预警',       'level': 'mid',  'scope_level': 'county'},
    'approval_backlog':     {'name': '未审批积压预警',   'level': 'high', 'scope_level': 'any'},
    'approved_not_started': {'name': '已批未开工预警',   'level': 'mid',  'scope_level': 'station'},
    'node_stay_overdue':    {'name': '审批停留超期预警', 'level': 'high', 'scope_level': 'any'},
    'node_backlog':         {'name': '审批环节积压预警', 'level': 'mid',  'scope_level': 'any'},
    'data_quality_amount':  {'name': '金额存疑预警',     'level': 'mid',  'scope_level': 'any'},
    'extreme_amount':       {'name': '金额异常预警',     'level': 'mid',  'scope_level': 'any'},
    'data_quality_status':  {'name': '状态未登记预警',   'level': 'low',  'scope_level': 'any'},
    'data_quality_missing': {'name': '数据缺失预警',     'level': 'low',  'scope_level': 'any'},
    'metric_deteriorated':  {'name': '指标恶化预警',     'level': 'mid',  'scope_level': 'any'},
}

# ── 角色 → 收哪些类 ─────────────────────────────────────────────────────
# 原则：预警发给「能动手的人」；层级越高只收汇总与要决策的；小问题不上大领导。
ROLE_ALERTS = {
    'sz': ['budget_underspend', 'approval_backlog', 'approved_not_started',
           'node_stay_overdue', 'node_backlog', 'data_quality_amount', 'extreme_amount'],
    'xgscwb': ['budget_underspend', 'zero_execution', 'approval_backlog',
               'node_stay_overdue', 'node_backlog', 'metric_deteriorated'],
    'cto': ['node_stay_overdue', 'node_backlog', 'approved_not_started', 'approval_backlog'],
    'yxb': ['node_stay_overdue', 'node_backlog'],
    'sgscwb': ['budget_underspend', 'zero_execution', 'approval_backlog',
               'node_stay_overdue', 'node_backlog', 'extreme_amount', 'data_quality_amount',
               'data_quality_status', 'data_quality_missing', 'metric_deteriorated'],
    # 市领导：只收全市汇总 + 指标，**执行层与数据质量问题一律不给**
    'master': ['budget_underspend', 'metric_deteriorated'],
    'codea': [],
}

ROLE_NAMES = {
    'sz': '供电所所长', 'codea': '录入人员', 'cto': '县公司运检部',
    'yxb': '县公司营销部', 'xgscwb': '县公司财务部',
    'sgscwb': '市公司财务部', 'master': '市公司领导',
}

# 同一个人挂多个角色时，取下面靠前的那个（权限最大的）
ROLE_PRIORITY = ['master', 'sgscwb', 'xgscwb', 'cto', 'yxb', 'sz', 'codea']

# 审批节点名（t_work_order_audit_task.node_name 真值）→ 归属角色码
LEDGER_ROLE_BY_NODE = {
    '供电所内部审核': 'sz',
    '县公司运检部审核': 'cto',
    '营销部审核': 'yxb',
    '县公司财务部审核': 'xgscwb',
    '市公司财务部审核': 'sgscwb',
    '市公司领导审批': 'master',
}

LEVEL_WEIGHT = {'high': 0, 'mid': 1, 'low': 2}

# 每个角色一次最多回几条（一次一件事，但不能无限多条把接口变成报表）
# 超出按 LEVEL_WEIGHT 截断，被截掉的类型名放进返回的 `suppressed` 里（只给类型名，便于排查）
MAX_ALERTS = {
    'sz': 6, 'xgscwb': 6, 'cto': 4, 'yxb': 3, 'sgscwb': 7, 'master': 2, 'codea': 0,
}

# ── 判据阈值（出处：skills/判据规则.md 的起始约定）─────────────────────
STAY_BACKLOG_DAYS = 90        # 审批停留 >90 天 = 积压（判据规则 八.3）
STAY_SLOW_DAYS = 15           # >15 天 = 偏慢（判据规则 八.3）
UNDERSPEND_COEF = 0.6         # 执行率/进度系数（判据规则 三.7、17.1）
NOT_STARTED_DAYS = 30         # 已批未开工超过这么多天才报（判据规则 11.1 一般环节 >30 天）
STATUS_UNREGISTERED = '-1'    # 原系统状态未登记（判据规则 21 节：单列，不并进草稿）
EXTREME_AMOUNT = 1000000.0    # 单笔 ≥ 100 万视为异常（需人工核；实测有一笔 4383 万）
STATUS_APPROVED = ('3', '6', '7')   # 已审批（判据规则 三.1）
STATUS_UNAPPROVED = ('0', '1', '9')  # 未审批（判据规则 三.2）
STATUS_NOT_STARTED = '3'      # 审核通过待施工
ABNORMAL_METRICS = ('tripCount', 'lossRate')   # 只推这两项；售电量口径没对齐（假恶化）
METRIC_PERIOD_KEY = None      # None = 取最新期


def forecast_mode():
    """'pace'（默认：按已审批速度外推到年底）或 'pipeline'（按立项能力推算）。"""
    return (os.environ.get('SECRETARY_ALERT_FORECAST') or 'pace').strip().lower()


def min_amount():
    """明细最小金额门槛（元）。0 = 不过滤。"""
    try:
        return float(os.environ.get('SECRETARY_ALERT_MIN_AMOUNT') or 0)
    except ValueError:
        return 0.0


def _is_leap(y):
    return y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)


def elapsed_months(today=None):
    """年初到今天过了几个月（按 30.4 天/月）。把当前速度外推到全年要用它。"""
    t = (today or time.strftime('%Y-%m-%d'))
    try:
        y, m, d = [int(x) for x in str(t)[:10].split('-')]
    except Exception:                                   # noqa: BLE001
        lt = time.localtime()
        y, m, d = lt.tm_year, lt.tm_mon, lt.tm_mday
    dim = [31, 29 if _is_leap(y) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    doy = sum(dim[:m - 1]) + d
    return max(1.0, doy / 30.4)


def elapsed_ratio(today=None):
    """已过比例（0~1），用于「已过时间 × 0.6」这类阈值。"""
    t = (today or time.strftime('%Y-%m-%d'))
    try:
        y = int(str(t)[:4])
    except Exception:                                   # noqa: BLE001
        y = time.localtime().tm_year
    total = 366.0 if _is_leap(y) else 365.0
    return min(1.0, max(0.0, elapsed_months(t) * 30.4 / total))


def year_of(today=None):
    t = (today or time.strftime('%Y-%m-%d'))
    try:
        return str(int(str(t)[:4]))
    except Exception:                                   # noqa: BLE001
        return str(time.localtime().tm_year)


# ── Site pack override (P3, 2026-10-21) ─────────────────────────────────
# 业务表（判据/阈值/角色码/节点）的真源在 config/sites/<SITE>/rules/*.yml。
# 本文件只保留兜底默认值；包里有同名 UPPERCASE 键就覆盖，没有就沿用默认。
def _apply_pack_rules(key: str) -> None:
    try:
        from app.secretary import site_pack
        ov = ((site_pack.load(strict=False) or {}).get("rules") or {}).get(key) or {}
    except Exception as exc:                       # noqa: BLE001
        print("[rules] pack load failed, keep defaults:", exc)
        return
    for k, v in (ov or {}).items():
        if not k.isupper() or k not in globals():
            continue
        cur = globals()[k]
        globals()[k] = tuple(v) if isinstance(cur, tuple) and isinstance(v, list) else v


_apply_pack_rules("alerts")
