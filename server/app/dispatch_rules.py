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

# 范围级别 → 应办角色（2026-10-28 从 dispatch_service._target_role 外置）：
# 原来这三组是写在业务代码里的字面量，换单位/换岗位码就得改代码。
LEVEL_ROLE = {'station': 'sz', 'county': 'xgscwb', 'city': 'sgscwb'}

# ══════════════════════════════════════════════════════════════
#  时限与升级
# ══════════════════════════════════════════════════════════════

LEVEL_REPLY_HOURS = {'high': 24, 'mid': 72, 'low': 168}     # 回执时限（小时）
LEVEL_TARGET_DAYS = {'high': 30, 'mid': 30, 'low': 30}      # 目标时限（天）
LEVEL_GRACE_DAYS = {'high': 1, 'mid': 1, 'low': 2}          # 超期宽限（天）
URGE_BEFORE_HOURS = 4                                        # 回执前 4 小时提醒
URGE_MIN_INTERVAL_H = 24                                     # 催办最小间隔
REOPEN_DAYS = 7                                              # 闭环后多少天内可重开
DRAFT_DAYS = 7                                               # 草稿多少天没确认自动取消

# 预算执行率达标线（2026-10-28 外置）：预警判据 alerts4._rate_rule 和建议话术
# dispatch_ai 共用这一个数，别再各写一份 0.6。
RATE_TARGET = 0.6

# 建议话术里的时限（天）。换单位/换节奏只改这里，别改代码里的句子。
#   plan  要求下级交方案的期限
#   turn  要求把执行率扭转到位的期限
ADVICE_WINDOWS = {'plan': 7, 'turn': 30}


# 判据阈值（与 alert_rules 保持一致，避免两套数）
STAY_BACKLOG_DAYS = R.STAY_BACKLOG_DAYS      # 90
STAY_SLOW_DAYS = R.STAY_SLOW_DAYS            # 15
UNDERSPEND_COEF = R.UNDERSPEND_COEF          # 年底预计执行 / 分配 < 该值 = 花不完（来源 alert_rules）
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


# ══════════════════════════════════════════════════════════════
#  身份建议规则（智能体"给什么建议"的唯一真源）
#
#  为什么要有这张表：同一件事，市领导、县财务、所长要的答案完全不同。
#  市领导要的是"怎么让下面动起来"，所长要的是"这笔钱该花在哪个方向"。
#  不把口径写死，模型就会给所长讲"加强领导重视"，给市领导讲"去新建项目"。
#
#  字段：
#    angle     一句话结论口径（给提示词当"你是谁、你该回答什么"）
#    data      这个身份该看的数据维度（提示词里要求引用，且必须来自真实查询）
#    must_any  结论里至少命中一个（机器粗判"有没有说到点上"）
#    must_not  命中即判失败（越界了）
#    actions   这个身份允许的下一步动作（超出就是不给他选项）
#    no_noise  这个身份最怕的空话（额外禁）
# ══════════════════════════════════════════════════════════════

# 谁都说不得的空话（对所有身份生效）
ADVICE_BANNED = (
    '加强管理', '高度重视', '进一步提升', '切实做好', '狠抓落实',
    '不断优化', '全面推进', '有效提升',
)


ROLE_ADVICE = {
    'master': {
        'name': '市领导',
        'angle': '你管全市，不懂也不想懂具体怎么花钱。你要回答的是：哪些县垫底、'
                 '卡在哪个环节、用什么办法让他们动起来。',
        'data': ['县级执行率排名', '全市预算执行率', '已批未开工总额', '最长审批停留天数'],
        'must_any': ['执行率', '排名', '垫底', '已批未开工', '停留', '督办', '限期', '约谈'],
        'must_not': ['新建项目', '申报项目', '报审批', '调整线损', '编制预算',
                     '储备项目', '可研', '施工'],
        'actions': ['下发要求', '抄送知会', '写说明'],
        'no_noise': ['具体项目', '技术方案'],
    },
    'sgscwb': {
        'name': '市公司财务部',
        'angle': '你管全市的钱。你要回答的是：全市怎么统筹调剂、给哪个县下限期、'
                 '哪些必须报给市领导定大方向。执行细节不用你管。',
        'data': ['县级执行率排名', '全市预算执行率', '全市余额', '可统筹调剂额度'],
        'must_any': ['统筹', '调剂', '排名', '余额', '限期', '下发', '上报'],
        'must_not': ['新建项目', '申报项目', '技术方案', '调整线损'],
        'actions': ['全市统筹', '下发县财务', '上报市领导', '抄送知会'],
        'no_noise': ['本所'],
    },
    'xgscwb': {
        'name': '县公司财务部',
        'angle': '你管一个县。你要回答的是：这件事本级能不能自己了结。'
                 '能——给出县内调剂方案；不能——明确往下压给哪几个所、各要完成多少。',
        'data': ['本县各所执行率排名', '卡住的审批节点与单数', '县内可调剂额度'],
        'must_any': ['本级', '了结', '调剂', '往下', '下发', '上报', '各所'],
        'must_not': ['新建项目', '申报项目', '技术方案', '调整线损'],
        'actions': ['本级办结', '往下发子任务', '上报市公司财务部', '补充说明'],
        'no_noise': ['全市排名'],
    },
    'cto': {
        'name': '县公司运检部',
        'angle': '你管一个县的专业线（运检）。预算怎么花不是你的活，'
                 '你要回答的是：从运行检修这块能配合什么、能消化多少。',
        'data': ['线损率', '跳闸次数', '平均停电时长', '抢修工单数'],
        'must_any': ['线损', '跳闸', '停电', '抢修', '配合', '协办', '消纳'],
        'must_not': ['新建项目', '编制预算', '下发任务', '要求各所'],
        'actions': ['协办', '抄送知会', '写说明'],
        'no_noise': ['预算分配'],
    },
    'yxb': {
        'name': '县公司营销部',
        'angle': '你管一个县的专业线（营销）。预算怎么花不是你的活，'
                 '你要回答的是：从营销服务这块能配合什么、能消化多少。',
        'data': ['售电量', '投诉工单数', '业扩报装', '电费回收'],
        'must_any': ['售电', '投诉', '业扩', '电费', '配合', '协办', '消纳'],
        'must_not': ['新建项目', '编制预算', '下发任务', '要求各所'],
        'actions': ['协办', '抄送知会', '写说明'],
        'no_noise': ['预算分配'],
    },
    'sz': {
        'name': '供电所所长',
        'angle': '你管一个所，你是真正要把钱花掉的人。你要回答的是：'
                 '本所哪个指标拖后腿、这笔钱该往哪个方向花、先动哪个具体项目。',
        'data': ['本所预算分配与执行', '线损率', '跳闸次数', '平均停电时长',
                 '抢修/投诉工单数', '本所项目（审批中/已批未开工/施工中）与金额',
                 '审批停留天数'],
        'must_any': ['线损', '跳闸', '停电', '工单', '已批未开工', '审批', '储备',
                     '报项目', '推进'],
        'must_not': ['要求下级', '责成各所', '全县排名', '下发任务', '督办各县'],
        'actions': ['自己办', '上报县公司财务部', '交方案', '补充说明'],
        'no_noise': ['全市', '全县排名'],
    },
}

# 有账号、但不管任务的角色：不给他们生成处置建议
ADVICE_NO_ROLE = ('codea', 'admin')


def advice_rule(role_code: str | None) -> dict:
    """取这个身份的建议规则；不认识的按所长处理（最细的一层，宁可多给不看空话）。"""
    rc = (role_code or '').strip()
    if rc in ADVICE_NO_ROLE:
        return {}
    return ROLE_ADVICE.get(rc) or ROLE_ADVICE['sz']


def advice_violations(role_code: str | None, text: str) -> list:
    """粗判一段建议有没有越界。返回命中的词（空列表 = 没越界）。

    只做词表级判断 —— 严格语义判断交给人工抽检，别假装机器能判"中不中肯"。
    """
    rule = advice_rule(role_code)
    if not rule:
        return []
    body = str(text or '')
    hits = [w for w in ADVICE_BANNED if w in body]
    hits += [w for w in (rule.get('no_noise') or []) if w in body]
    hits += [w for w in (rule.get('must_not') or []) if w in body]
    return hits


def advice_on_point(role_code: str | None, text: str) -> bool:
    """说没说到点上：至少命中一个 must_any。"""
    rule = advice_rule(role_code)
    if not rule:
        return True
    body = str(text or '')
    return any(w in body for w in (rule.get('must_any') or []))


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


_apply_pack_rules("dispatch")
