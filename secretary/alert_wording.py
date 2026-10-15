# -*- coding: utf-8 -*-
"""预警话术：**事实 + 催办**。改话术只改这里，不用动判据。

**为什么单独一个文件**：判据是「算得对不对」，话术是「说不说得动」。
两件事的验收标准完全不同 —— 判据要能对账，话术要能催人动手。

## 口吻规则（Fred 2026-10-15：**不要全是陈述句**）

| 要 | 不要 |
|---|---|
| **末句是催办**：请抓紧 / 请督促 / 请安排开工 / 请核实 | 只报事实（"XX 有 23 单未审批"）—— 那是报表 |
| **该谁动就写谁的动**（所长→提交审核；县财务→督促各所） | 无主句 |
| **省字**：所名简称（两水供电所→两水所），催办语≤12 字 | 把整句挤到 40 字被截断、催办被吃掉 |
| 业务词：未审批 / 未开工 / 已批 / 逐年 | 我们的分析词：卡在 / 单子 / 卡住 / 超标 / 异常 |

格式统一：`【类别】对象：事实，催办`，**整句 ≤40 个汉字，末尾不许被截掉**。
"""
# 类别前缀（一眼看出是什么事）
TAG = {
    'budget_underspend':    '【预算预警】',
    'zero_execution':       '【零审批预警】',
    'approval_backlog':     '【待审批预警】',
    'approved_not_started': '【待开工预警】',
    'node_stay_overdue':    '【审批滞留预警】',
    'node_backlog':         '【审批积压预警】',
    'data_quality_amount':  '【金额存疑】',
    'data_quality_status':  '【状态未登记】',
    'data_quality_missing': '【数据缺失】',
    'metric_deteriorated':  '【指标预警】',
}

# 催办语：按角色分 —— 同一件事，不同角色该动的手不一样。
# 长度纪律：**≤14 字**，否则会把事实挤掉（拼句总长上限 40）。
URGE = {
    'budget_underspend': {
        'station': '请抓紧立项审批',
        'county':  '请督促各所加快立项',
        'city':    '请督促各县公司',
    },
    'zero_execution': {
        'county': '请督促这些所报项目',
        'city':   '请督促相关县公司',
    },
    'approval_backlog': {
        'station': '请尽快提交审核',
        'county':  '请督促各所尽快报审',
        'city':    '请督促各所与县公司',
    },
    'approved_not_started': {
        'station': '请尽快安排开工',
        'county':  '请督促各所开工',
    },
    'node_stay_overdue': {
        'sz':      '请抓紧审核',
        'cto':     '请抓紧审核',
        'yxb':     '请抓紧审核',
        'xgscwb':  '请抓紧审核',
        'sgscwb':  '请抓紧审核',
        'master':  '请督促办结',
        'default': '请抓紧办结',
    },
    'node_backlog': {
        'station': '请推动下一环节',
        'county':  '请督办该环节',
        'city':    '请督办该环节',
    },
    'data_quality_amount': {
        'station': '请核实并修改',
        'default': '请核实并修正',
    },
    'data_quality_status': {
        'default': '请核实并补登',
    },
    'data_quality_missing': {
        'default': '请补录',
    },
    'metric_deteriorated': {
        'default': '请分析原因并治理',
    },
}


def urge(code, scope_type, role_code=''):
    """取催办语。三层兜底：精确 scope_type → 角色码 → default。"""
    table = URGE.get(code) or {}
    for key in (scope_type, role_code, 'default'):
        if key and key in table:
            return table[key]
    return ''


def compose(code, subject, fact, scope_type='', role_code=''):
    """统一拼句：`【类别】对象：事实，催办`。"""
    tag = TAG.get(code, '')
    body = '%s：%s' % (subject, fact) if subject else fact
    u = urge(code, scope_type, role_code)
    return '%s%s，%s' % (tag, body, u) if u else tag + body
