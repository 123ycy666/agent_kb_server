# -*- coding: utf-8 -*-
"""四类预警与任务流转的**配置层**（改口径、改职责、改时限只改这里）。

纪律：
  1. 这里只放「收什么、阈值多少、谁负责、默认时限」，不查库、不写逻辑。
  2. **不写死任何业务数**（分配额、所数、金额不许出现）。阈值来自 `skills/判据规则.md`。
  3. 关掉的判据必须写明原因（`note`），不许"因为不好做"就静默删掉。
"""
from __future__ import annotations

import os

from app.secretary import alert_rules as R

# ══════════════════════════════════════════════════════════════
#  四类（对外契约，只允许这四个值）
# ══════════════════════════════════════════════════════════════

CATEGORY_NAMES = {
    'budget': '预算预警',
    'project': '项目全过程管理预警',
    'review': '穿透式复盘预警',
    'metric': '指标预警',
}

# 子判据元数据
#   level      默认级别（high/mid/low）
#   cadence    daily / weekly / monthly / yearly
#   enabled    是否参与扫描（关掉要在 note 里写原因）
#   roles      哪些角色会收到（None = 不限）
#   closure    auto=可自动核销 / human=人工回执
#   reply_h   回执时限（小时），0=用级别默认
#   target_d  目标时限（天），0=下发时手填
#   object    预警对象类型（写进 alert_item.object_type）
SUBTYPES = {
    'budget.underspend': {
        'name': '预算花不完', 'level': 'high', 'cadence': 'daily', 'enabled': True,
        'roles': ['sz', 'xgscwb', 'sgscwb', 'master'], 'closure': 'auto', 'reply_h': 24,
        'target_d': 30, 'object': 'station',
        'note': '按当前审批速度外推到年底 < 分配×60% 才发；花得完不发',
    },
    'budget.overrun': {
        'name': '预算超支风险', 'level': 'high', 'cadence': 'daily', 'enabled': True,
        'roles': ['sz', 'xgscwb', 'sgscwb', 'master'], 'closure': 'auto', 'reply_h': 24,
        'target_d': 30, 'object': 'station',
        'note': '已审批 > 分配；当前实测 0 命中，留作守门判据',
    },

    'project.approval_stay': {
        'name': '审批停留超期', 'level': 'high', 'cadence': 'daily', 'enabled': True,
        'roles': ['sz', 'cto', 'yxb', 'xgscwb', 'sgscwb', 'master'], 'closure': 'auto',
        'reply_h': 24, 'target_d': 7, 'object': 'node',
        'note': '只算本角色对应节点；>90 天=积压(高)，16~90 天=偏慢(中)',
    },
    'project.approved_not_started': {
        'name': '已批未开工', 'level': 'mid', 'cadence': 'daily', 'enabled': True,
        'roles': ['sz'], 'closure': 'auto', 'reply_h': 72, 'target_d': 30,
        'object': 'station',
        'note': '状态3 且审批完成 >30 天；无审批完成时间时用 update_time 并在 data 标注',
    },
    'project.exec_stalled': {
        'name': '施工停滞', 'level': 'high', 'cadence': 'daily', 'enabled': True,
        'roles': ['sz'], 'closure': 'auto', 'reply_h': 72, 'target_d': 30,
        'object': 'station',
        'note': '状态6 且停留 >90 天；2026 年 0 单，判据保留给存量',
    },
    'project.acceptance_overdue': {
        'name': '完工未上报', 'level': 'mid', 'cadence': 'daily', 'enabled': False,
        'roles': ['sz'], 'closure': 'auto', 'reply_h': 72, 'target_d': 30,
        'object': 'station',
        'note': '默认关：状态历史表 2026 年状态7 无记录，update_time 是同日批量写，判"进入状态时间"不可靠',
    },
    'project.amount_structure': {
        'name': '申报金额存疑', 'level': 'low', 'cadence': 'weekly', 'enabled': False,
        'roles': ['sz', 'xgscwb'], 'closure': 'auto', 'reply_h': 168, 'target_d': 7,
        'object': 'station',
        'note': '默认关：24 单常驻，每天推同一句；先放进"提交前自检"，不做每日预警',
    },

    'review.low_roi': {
        'name': '投入产出偏低', 'level': 'mid', 'cadence': 'yearly', 'enabled': True,
        'roles': ['xgscwb', 'sgscwb', 'master'], 'closure': 'human', 'reply_h': 168,
        'target_d': 90, 'object': 'org',
        'note': '按行取治理成效表，**不 SUM**（表内含全市行，求和会翻倍）',
    },
    'review.no_effect': {
        'name': '投入未见效', 'level': 'mid', 'cadence': 'yearly', 'enabled': False,
        'roles': ['xgscwb', 'sgscwb', 'master'], 'closure': 'human', 'reply_h': 168,
        'target_d': 90, 'object': 'org',
        'note': '默认关：2026 年产出全空、2024 年投入全空，跨年对比无法成立；等业务补数据',
    },
    'review.not_reviewed': {
        'name': '完工未复盘', 'level': 'low', 'cadence': 'monthly', 'enabled': True,
        'roles': ['sz'], 'closure': 'human', 'reply_h': 168, 'target_d': 30,
        'object': 'station',
        'note': '完工 >90 天且无复盘任务；进入状态时间按 update_time 估算，data 里标 estimated',
    },

    'metric.deteriorated': {
        'name': '指标同比恶化', 'level': 'mid', 'cadence': 'monthly', 'enabled': True,
        'roles': ['xgscwb', 'cto', 'yxb', 'sgscwb', 'master'], 'closure': 'auto',
        'reply_h': 168, 'target_d': 30, 'object': 'metric',
        'note': '排除售电量（累计口径没对齐=假恶化）；只推跳闸与线损',
    },
    'metric.threshold': {
        'name': '指标越阈值', 'level': 'mid', 'cadence': 'monthly', 'enabled': True,
        'roles': ['sz', 'xgscwb', 'cto', 'sgscwb'], 'closure': 'auto', 'reply_h': 168,
        'target_d': 30, 'object': 'metric',
        'note': '线损率 >3%（优秀线）；其他指标缺业务目标值，先不做',
    },
    'metric.stale': {
        'name': '指标数据过期', 'level': 'low', 'cadence': 'monthly', 'enabled': False,
        'roles': ['sgscwb'], 'closure': 'auto', 'reply_h': 168, 'target_d': 30,
        'object': 'metric',
        'note': '默认关：数据治理项，进周报即可，不占每日待办',
    },
}

# 每个角色默认收哪些类（细化到子判据时看 SUBTYPES[*].roles）
ROLE_CATEGORIES = {
    'sz': ['budget', 'project', 'review'],
    'xgscwb': ['budget', 'project', 'review', 'metric'],
    'cto': ['project', 'metric'],
    'yxb': ['project', 'metric'],
    'sgscwb': ['budget', 'project', 'review', 'metric'],
    'master': ['budget', 'review', 'metric'],
    'codea': [],
}

# ══════════════════════════════════════════════════════════════
#  职责线（升级：只在同一职责线内找上一级；市领导只收大方向）
# ══════════════════════════════════════════════════════════════

# 按节点定责：审批停留类
NODE_ROLE = {
    '供电所内部审核': 'sz',
    '县公司运检部审核': 'cto',
    '营销部审核': 'yxb',
    '县公司财务部审核': 'xgscwb',
    '市公司财务部审核': 'sgscwb',
    '市公司领导审批': 'master',
}

# 指标定责：谁背这个指标
METRIC_ROLE = {
    'lossRate': 'cto',
    'tripCount': 'cto',
    'averageOutageDuration': 'cto',
    'faultRepairWorkOrder': 'cto',
    'salesVolume': 'yxb',
    'feedbackWorkOrder': 'yxb',
}

# 升级链：category → 当前角色 → 上一级角色（按序取第一个有账号的）
DUTY_CHAIN = {
    'budget': {
        'sz': ['xgscwb'], 'xgscwb': ['sgscwb'], 'sgscwb': ['master'],
    },
    'project': {
        # 审批停留按节点角色升；其余项目类所内先到县运检
        'sz': ['cto', 'xgscwb'], 'cto': ['sgscwb'], 'yxb': ['sgscwb'],
        'xgscwb': ['sgscwb'], 'sgscwb': ['master'],
    },
    'metric': {
        'sz': ['cto', 'yxb'], 'cto': ['sgscwb'], 'yxb': ['sgscwb'],
        'xgscwb': ['sgscwb'], 'sgscwb': ['master'],
    },
    'review': {
        'sz': ['xgscwb'], 'xgscwb': ['sgscwb'], 'sgscwb': ['master'],
    },
}

# 市领导不接执行层（这些角色升到 master 时改为发起人 + 管理员）
MASTER_ESCALATION_BLOCK = True
ADMIN_ROLE = 'admin'

# ══════════════════════════════════════════════════════════════
#  时限与升级
# ══════════════════════════════════════════════════════════════

LEVEL_REPLY_HOURS = {'high': 24, 'mid': 72, 'low': 168}     # 回执时限（小时）
LEVEL_TARGET_DAYS = {'high': 30, 'mid': 30, 'low': 30}      # 目标时限（天）
LEVEL_GRACE_DAYS = {'high': 1, 'mid': 1, 'low': 2}          # 超期宽限（天）
URGE_BEFORE_HOURS = 4                                        # 回执前 4 小时提醒
URGE_MIN_INTERVAL_H = 24                                     # 催办最小间隔

# 判据阈值（与 alert_rules 保持一致，避免两套数）
STAY_BACKLOG_DAYS = R.STAY_BACKLOG_DAYS      # 90
STAY_SLOW_DAYS = R.STAY_SLOW_DAYS            # 15
OVERRUN_RATIO = 1.0                          # 已审批 / 分配 > 1.0 = 超支
LOSS_RATE_MAX = 3.0                          # 线损率优秀线（%）
ROI_MIN = 0.1                                # 投入产出比下限
ROI_MIN_INVEST = 1000000.0                   # 投入 ≥100 万才判复盘
NOT_REVIEWED_DAYS = 90
TITLE_MAX = 44

# ══════════════════════════════════════════════════════════════
#  运行时开关（环境变量）
# ══════════════════════════════════════════════════════════════


def _env_bool(key: str, default: str = '1') -> bool:
    return (os.environ.get(key) or default).strip().lower() not in ('0', 'false', 'no', 'off')


def dispatch_on() -> bool:
    """总开关：关掉后新路由直接 404/503（用于回滚）。"""
    return _env_bool('DISPATCH_ON', '1')


def ai_on() -> bool:
    """AI 拟办/分析总开关；关掉后只给规则建议（数字仍然真）。"""
    return _env_bool('DISPATCH_AI_ON', '1')


def tick_minutes() -> int:
    """内部 tick 间隔（分钟）；0 = 关闭，只靠外部调 /api/dispatch/tick。"""
    try:
        return max(0, int(os.environ.get('DISPATCH_TICK_MINUTES') or 10))
    except ValueError:
        return 10


def scan_accounts() -> list[str]:
    """默认扫描哪些账号；空 = 全部有业务角色的有效账号。"""
    raw = (os.environ.get('DISPATCH_SCAN_ACCOUNTS') or '').strip()
    return [x.strip() for x in raw.split(',') if x.strip()]


# ══════════════════════════════════════════════════════════════
#  查询工具
# ══════════════════════════════════════════════════════════════


def meta(subtype: str) -> dict:
    return SUBTYPES.get(subtype) or {}


def category_of(subtype: str) -> str:
    return subtype.split('.', 1)[0]


def name_of(subtype: str) -> str:
    return meta(subtype).get('name') or subtype


def enabled_types(role_code: str, cadence: str = 'daily') -> list[str]:
    """某角色在某个频率下要跑哪些子判据。cadence='all' 表示全部启用的。"""
    cats = ROLE_CATEGORIES.get(role_code)
    if cats is None:
        cats = []
    out = []
    for key, m in SUBTYPES.items():
        if not m.get('enabled'):
            continue
        if category_of(key) not in cats:
            continue
        roles = m.get('roles')
        if roles is not None and role_code not in roles:
            continue
        if cadence != 'all' and m.get('cadence') != cadence:
            continue
        out.append(key)
    return out


def reply_hours(subtype: str, level: str) -> int:
    h = int(meta(subtype).get('reply_h') or 0)
    return h or LEVEL_REPLY_HOURS.get(level, 72)


def target_days(subtype: str) -> int:
    return int(meta(subtype).get('target_d') or LEVEL_TARGET_DAYS.get('mid', 30))


def next_roles(category: str, role_code: str) -> list[str]:
    chain = (DUTY_CHAIN.get(category) or {}).get(role_code) or []
    return [r for r in chain if r]


def verify_kind(subtype: str) -> str:
    """默认核销方式：auto（可算）/ human（人工确认）。"""
    return 'auto' if meta(subtype).get('closure') == 'auto' else 'human'
