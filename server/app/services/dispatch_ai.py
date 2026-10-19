# -*- coding: utf-8 -*-
"""任务流转的 AI / 规则建议层。

两条纪律：
  1. **数字只来自预警/任务里的实测值**（`data` / `dispatch_check`），模型只负责措辞；
     模型输出里出现原文没有的数字 → 整段丢弃，退回规则文本。
  2. 一切失败都不阻断业务：模型不可用就只用规则建议（`ai_used=false`）。

对外三个入口：
  · brief(alert)    预警首段话 + 3 条建议（A7/A1）
  · propose(alert, i) 按第 i 条建议生成拟办草稿（A8，不落库）
  · plan_brief(task)  执行侧"怎么干"建议（A4）
"""
from __future__ import annotations

import logging
import re

from app import dispatch_rules as DR

logger = logging.getLogger(__name__)

MAX_AI_WAIT_S = 12


def _wan(v):
    try:
        return '%.2f万' % (float(v) / 10000.0)
    except (TypeError, ValueError):
        return '—'


def _num(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return '—'
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    return ('%.2f' % x).rstrip('0').rstrip('.')


def _digits(text: str) -> set[str]:
    return set(re.findall(r'\d+(?:\.\d+)?', text or ''))


def _safe_polish(text: str, must_keep: str) -> tuple[str, bool]:
    """让模型润色；出现原文没有的数字就整段丢弃（数字出处守卫）。"""
    if not DR.ai_on():
        return text, False
    try:
        from app.providers import get_providers
        bundle = get_providers()
        if getattr(bundle, 'mode', '') == 'mock':
            return text, False
        import concurrent.futures as cf
        msgs = [
            {'role': 'system', 'content': '你是供电公司督办助手。只改写措辞，'
             '**不得新增、删除或修改任何数字、日期、单位名**。保持中文公文语气，不超过 300 字。'},
            {'role': 'user', 'content': text},
        ]
        with cf.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(bundle.llm.chat, msgs, temperature=0.2, max_tokens=600)
            out = fut.result(timeout=MAX_AI_WAIT_S)
        out = str(out or '').strip()
        if not out:
            return text, False
        allowed = _digits(must_keep)
        if _digits(out) - allowed:
            logger.warning('AI 润色出现无出处数字，已丢弃：%s', (_digits(out) - allowed))
            return text, False
        return out, True
    except Exception as exc:  # noqa: BLE001 - 模型不可用不影响业务
        logger.info('AI 润色跳过（%s: %s）', type(exc).__name__, exc)
        return text, False


# ─────────────────────────────────────────────────────────── 建议

def _advice(alert: dict) -> list[dict]:
    """按子判据给 3 条建议；每条都必须带 evidence（来自 data）。"""
    d = alert.get('data') or {}
    st = alert.get('subtype') or ''
    if st == 'budget.underspend':
        alloc = d.get('alloc') or 0
        approved = d.get('approved') or 0
        left = d.get('left') or 0
        need = max(0.0, float(alloc) * 0.6 - float(approved))
        return [
            {'action': '本月内再审批通过约 %s 的储备项目' % _wan(need),
             'evidence': '当前已审批 %s、执行率 %s%%，距 60%% 还差 %s'
                         % (_wan(approved), _num(d.get('rate_pct')), _wan(need)),
             'expected': '年底预计执行率达到 60% 以上'},
            {'action': '先清审批积压：把待审批件推到通过或退回补正',
             'evidence': '余额 %s，不推进则年底结余接近余额' % _wan(left),
             'expected': '30 天内待审批件明显下降'},
            {'action': '确无储备：向县财务申请调减预算，把余额调给执行率高的所',
             'evidence': '余额 %s；本所今年已审批 %s' % (_wan(left), _wan(approved)),
             'expected': '预算执行率与全市差距收窄'},
        ]
    if st == 'budget.overrun':
        return [
            {'action': '立即核查超支原因，暂停新增审批',
             'evidence': '已审批 %s、分配 %s，超 %s'
                         % (_wan(d.get('approved')), _wan(d.get('alloc')), _wan(d.get('over'))),
             'expected': '3 天内给出超支说明'},
            {'action': '按项目逐单核对预算构成，超范围部分压缩或调整',
             'evidence': '超支 %s' % _wan(d.get('over')), 'expected': '超支额归零'},
            {'action': '把超支情况报县财务备案', 'evidence': '超支 %s' % _wan(d.get('over')),
             'expected': '形成调整方案'},
        ]
    if st == 'project.approval_stay':
        node = d.get('node') or ''
        return [
            {'action': '让%s逐单说明停留原因（3 天内）' % node,
             'evidence': '%d 个项目待处理，其中 %d 个超 90 天，最久 %d 天'
                         % (int(d.get('total') or 0), int(d.get('over_90') or 0),
                            int(d.get('max_days') or 0)),
             'expected': '3 天内出停留原因清单'},
            {'action': '超 90 天的先办：材料缺失退回补正，金额大的先审',
             'evidence': '涉及金额 %s' % _wan(d.get('amount')),
             'expected': '15 天内超 90 天件数下降一半'},
            {'action': '设节点时限：新进节点超 15 天自动提醒',
             'evidence': '偏慢档阈值为 15 天', 'expected': '不再产生新的超 90 天件'},
        ]
    if st == 'project.approved_not_started':
        return [
            {'action': '7 天内确定开工时间并回填计划开工日',
             'evidence': '%d 个项目已批未开工，最久 %d 天，金额 %s'
                         % (int(d.get('cnt') or 0), int(d.get('max_days') or 0), _wan(d.get('amount'))),
             'expected': '全部有明确开工时间'},
            {'action': '不具备施工条件的，申请调整项目或退回',
             'evidence': '金额 %s' % _wan(d.get('amount')), 'expected': '状态离开"已批未开工"'},
            {'action': '金额大的先动，按金额排优先级',
             'evidence': '合计 %s' % _wan(d.get('amount')), 'expected': '30 天内开工率过半'},
        ]
    if st == 'project.exec_stalled':
        return [
            {'action': '逐个查明停滞原因（施工力量/物资/协调）',
             'evidence': '%d 个项目停滞超 90 天，最久 %d 天'
                         % (int(d.get('cnt') or 0), int(d.get('max_days') or 0)),
             'expected': '3 天内出原因清单'},
            {'action': '卡在物资的报县运检协调，卡在协调的报县公司',
             'evidence': '涉及金额 %s' % _wan(d.get('amount')), 'expected': '停滞项目恢复推进'},
            {'action': '30 天内无法推进的，按程序调整计划', 'evidence': '停滞超 90 天',
             'expected': '不再挂账'},
        ]
    if st == 'metric.deteriorated':
        w = d.get('worst') or {}
        return [
            {'action': '7 天内提交%s改善报告（目标回到 %s）' % (w.get('metric') or '指标', _num(w.get('base'))),
             'evidence': '当前 %s、基期 %s、变化 %s%%'
                         % (_num(w.get('current')), _num(w.get('base')),
                            _num((w.get('change_rate') or 0) * 100)),
             'expected': '下期回到基期水平'},
            {'action': '按单位拆分，先查最差 3 个单位的现场',
             'evidence': '共 %d 项指标同比恶化' % len(d.get('items') or []),
             'expected': '最差单位 30 天内改善'},
            {'action': '把该指标列入月度考核并周跟踪',
             'evidence': '同比恶化 %s%%' % _num((w.get('change_rate') or 0) * 100),
             'expected': '连续两期不再恶化'},
        ]
    if st == 'metric.threshold':
        return [
            {'action': '7 天内提交降损报告', 'evidence': '线损率 %s%%，优秀线 %s%%'
             % (_num(d.get('value')), _num(d.get('threshold'))), 'expected': '线损率降到 3% 以下'},
            {'action': '排查高损台区，先查计量与窃电', 'evidence': '本期 %s 台区超线'
             % _num(d.get('hit_count')), 'expected': '高损台区数量下降'},
            {'action': '对连续两期超线的台区列为重点整治', 'evidence': '快照期 %s' % d.get('period'),
             'expected': '纳入下批项目储备'},
        ]
    if st == 'review.low_roi':
        return [
            {'action': '组织年度复盘会，逐项说明投入与产出',
             'evidence': '%s 年投入 %s、产出 %s，投入产出比 %s'
                         % (d.get('year'), _wan(d.get('invest')), _wan(d.get('benefit')),
                            _num(d.get('ratio'))),
             'expected': '形成复盘报告与改进清单'},
            {'action': '下一年度预算向高产出单位倾斜',
             'evidence': '当前投入产出比 %s，低于 %s' % (_num(d.get('ratio')), _num(d.get('threshold'))),
             'expected': '投入产出比提升'},
            {'action': '对低产出项目做专项核查，区分口径问题与真实低效',
             'evidence': '投入 %s' % _wan(d.get('invest')), 'expected': '剔除无效投入'},
        ]
    if st == 'review.not_reviewed':
        return [
            {'action': '30 天内完成复盘并上传报告',
             'evidence': '%d 个项目完工超 90 天未复盘' % int(d.get('cnt') or 0),
             'expected': '全部有复盘记录'},
            {'action': '按项目清单逐项核对产出，缺数据的标注',
             'evidence': '候选 %d 个' % int(d.get('cnt') or 0), 'expected': '产出数据补齐'},
            {'action': '把复盘结论作为下一年预算依据', 'evidence': '完工未复盘',
             'expected': '预算分配有据可依'},
        ]
    return [
        {'action': '核对预警涉及的对象与金额，确认事实',
         'evidence': '预警：%s' % alert.get('title'), 'expected': '3 天内确认事实'},
        {'action': '明确责任人与时限，形成整改清单', 'evidence': '预警级别 %s' % alert.get('level'),
         'expected': '30 天内整改'},
        {'action': '整改结果以数据变化为准', 'evidence': '系统按同一口径复核',
         'expected': '指标/状态回归正常'},
    ]


def _why(alert: dict) -> str:
    st = alert.get('subtype') or ''
    d = alert.get('data') or {}
    if st.startswith('budget.'):
        return ('预算的执行进度与时间进度不匹配：已审批 %s，占分配 %s，执行率 %s%%。'
                % (_wan(d.get('approved')), _wan(d.get('alloc')), _num(d.get('rate_pct'))))
    if st == 'project.approval_stay':
        return ('项目停在本节点待处理：共 %s 个，其中 %s 个停留超过 90 天，最久 %s 天。'
                % (_num(d.get('total')), _num(d.get('over_90')), _num(d.get('max_days'))))
    if st.startswith('project.'):
        return ('项目在「%s」这一步停留过久：%s 个，合计 %s，最久 %s 天。'
                % (d.get('node') or '审批/施工',
                   _num(d.get('cnt') or d.get('total')), _wan(d.get('amount')),
                   _num(d.get('max_days'))))
    if st.startswith('review.'):
        return ('钱花出去以后的效果没有跟上：投入 %s、产出 %s，投入产出比 %s。'
                % (_wan(d.get('invest')), _wan(d.get('benefit')), _num(d.get('ratio'))))
    if st.startswith('metric.'):
        w = d.get('worst') or {}
        return ('指标同比走差：%s 当前 %s、基期 %s，变化 %s%%。'
                % (w.get('metric') or d.get('metric_name') or '指标',
                   _num(w.get('current') or d.get('value')),
                   _num(w.get('base') or d.get('threshold')),
                   _num((w.get('change_rate') or 0) * 100)))
    return alert.get('title') or ''


def _impact(alert: dict) -> str:
    st = alert.get('subtype') or ''
    d = alert.get('data') or {}
    if st == 'budget.underspend':
        return '按当前进度，年底预计结余 %s，预算执行率考核会受影响。' % _wan(d.get('left'))
    if st == 'project.approval_stay':
        return '按金额算 %s 的资金被占住，项目开工越晚，年内完工压力越大。' % _wan(d.get('amount'))
    if st == 'metric.deteriorated':
        return '该指标是六项考核指标之一，持续恶化会影响本单位排名。'
    if st == 'metric.threshold':
        return '线损率高于 3% 优秀线，直接影响线损考核与台区治理评价。'
    if st == 'review.low_roi':
        return '投入产出比低于 %s，明年预算安排会受质疑。' % _num(d.get('threshold'))
    return '该问题不处理会持续占用资金/时间，并在下一期重复出现。'


def brief(alert: dict) -> dict:
    """预警首段话（A7）+ 3 条建议（A1）。"""
    advice = _advice(alert)
    lines = [
        '**%s**' % (alert.get('title') or ''),
        '',
        '**关键数字**：%s' % _key_numbers(alert),
        '',
        '**为什么会这样**：%s' % _why(alert),
        '',
        '**影响**：%s' % _impact(alert),
        '',
        '**我建议这么做**：',
    ]
    for i, a in enumerate(advice, 1):
        lines.append('%d. %s（依据：%s；预期：%s）' % (i, a['action'], a['evidence'], a['expected']))
    lines += ['', '**要不要我帮你发起？** 选一条，我把要求、承办人、时限和核销方式都拟好，你确认后再下发。']
    text = '\n'.join(lines)
    polished, used = _safe_polish(text, text)
    return {'markdown': polished, 'advice': advice, 'ai_used': used}


def _key_numbers(alert: dict) -> str:
    d = alert.get('data') or {}
    st = alert.get('subtype') or ''
    if st.startswith('budget.'):
        return '分配 %s / 已审批 %s / 执行率 %s%% / 余额 %s' % (
            _wan(d.get('alloc')), _wan(d.get('approved')), _num(d.get('rate_pct')), _wan(d.get('left')))
    if st == 'project.approval_stay':
        return '待处理 %s 件 / 超 90 天 %s 件 / 最久 %s 天 / 金额 %s' % (
            _num(d.get('total')), _num(d.get('over_90')), _num(d.get('max_days')), _wan(d.get('amount')))
    if st.startswith('project.'):
        return '件数 %s / 金额 %s / 最久 %s 天' % (
            _num(d.get('cnt') or d.get('total')), _wan(d.get('amount')), _num(d.get('max_days')))
    if st.startswith('review.'):
        return '%s 年：投入 %s / 产出 %s / 比值 %s' % (
            d.get('year'), _wan(d.get('invest')), _wan(d.get('benefit')), _num(d.get('ratio')))
    if st.startswith('metric.'):
        w = d.get('worst') or {}
        return '指标 %s / 当前 %s / 基期 %s / 变化 %s%%' % (
            w.get('metric') or d.get('metric_name'), _num(w.get('current') or d.get('value')),
            _num(w.get('base') or d.get('threshold')), _num((w.get('change_rate') or 0) * 100))
    return '—'


def propose(alert: dict, option: int = 1) -> dict:
    """拟办草稿（A8）：不落库；确认后才建任务。"""
    advice = _advice(alert)
    idx = max(1, min(len(advice), int(option or 1))) - 1
    a = advice[idx]
    m = DR.meta(alert.get('subtype') or '')
    level = alert.get('level') or m.get('level') or 'mid'
    title = '%s%s' % (alert.get('scope_name') or '', '整改' if alert.get('alert_type') != 'review' else '复盘')
    title = ('%s：%s' % (alert.get('scope_name') or '', a['action']))[:200]
    requirement = (
        '**目标**：%s\n\n**依据**：%s\n\n**预期结果**：%s\n\n'
        '**回执要求**：请在回执时限前提交实施方案；完成后提交结果与证明材料。'
        % (a['action'], a['evidence'], a['expected'])
    )
    return {
        'option': idx + 1,
        'title': title,
        'requirement': requirement,
        'reply_due_hours': DR.reply_hours(alert.get('subtype') or '', level),
        'target_days': DR.target_days(alert.get('subtype') or ''),
        'require_plan': 1 if alert.get('alert_type') in ('project', 'budget') else 0,
        'verifier_type': 'auto' if DR.verify_kind(alert.get('subtype') or '') == 'auto' else 'human',
        'verify_rule': alert.get('verify_hint') or {},
        'advice': a,
    }


def plan_brief(task: dict, alert: dict | None = None) -> dict:
    """执行侧"怎么干"（A4）：目标、步骤、时间表、风险。"""
    steps = []
    if alert:
        d = alert.get('data') or {}
        if alert.get('subtype') == 'project.approval_stay':
            steps = [
                '第 1 天：把待处理件按停留天数排序，列出超 90 天的清单',
                '第 2–3 天：逐单确认材料是否齐全（缺什么补什么），能批的批',
                '第 4–7 天：对暂时不能批的，写明原因并回报发起人',
            ]
        elif alert.get('subtype', '').startswith('budget.'):
            steps = [
                '第 1–3 天：核对余额与可立项对象（高损台区、低电压、跳闸线路）',
                '第 4–10 天：形成 2–3 个项目建议书并走审批',
                '第 11–30 天：跟踪审批进度，每周回报执行率变化',
            ]
        elif alert.get('subtype', '').startswith('metric.'):
            steps = [
                '第 1–2 天：按台区/线路拆出最差 3 个对象',
                '第 3–5 天：现场核查（计量、窃电、设备）',
                '第 6–7 天：提交改善报告与整治清单',
            ]
        else:
            steps = ['第 1–3 天：核实事实与责任人', '第 4–10 天：形成整改方案', '第 11–30 天：执行并回报']
    text = '**怎么干**：\n' + '\n'.join('%d. %s' % (i + 1, s) for i, s in enumerate(steps))
    text += '\n\n需要的话，我可以直接把这份思路写成实施方案或正式报告。'
    polished, used = _safe_polish(text, text)
    return {'markdown': polished, 'steps': steps, 'ai_used': used}


def polish(text: str) -> dict:
    """润色（A9）：只改措辞，不新增数字；模型不可用时原样返回。"""
    out, used = _safe_polish(text, text)
    return {'content': out, 'ai_used': used}


def report_markdown(task: dict, alert: dict | None = None) -> dict:
    """上报报告（A5，先出 Markdown；docx 由 docgen 在 P3 接）。"""
    lines = [
        '# %s' % (task.get('title') or '处置情况报告'),
        '',
        '## 一、基本情况',
        '- 任务编号：%s' % task.get('task_no'),
        '- 责任单位：%s' % (task.get('scope_name') or ''),
        '- 发起人：%s' % ((task.get('creator') or {}).get('name') or ''),
        '- 时限：回执 %s / 完成 %s' % (task.get('reply_due_at'), task.get('due_at')),
        '',
        '## 二、问题与依据',
    ]
    if alert:
        lines.append('- 预警：%s' % alert.get('title'))
        lines.append('- 关键数据：%s' % (alert.get('data') or {}))
    lines += ['', '## 三、已采取措施']
    for e in (task.get('events') or [])[-8:]:
        lines.append('- %s：%s %s' % (e.get('create_time'), e.get('action'), e.get('content') or ''))
    lines += ['', '## 四、结果与下一步', '- 结果：待填', '- 下一步：待填']
    text = '\n'.join(lines)
    polished, used = _safe_polish(text, text)
    return {'markdown': polished, 'ai_used': used}


def report_sections(task: dict, alert: dict | None = None) -> dict:
    """docx 用的结构化报告：标题 + 四段正文 + 一张事件表。"""
    creator = (task.get('creator') or {})
    assignees = '、'.join('%s(%s)' % (a.get('name') or '', a.get('part') or '')
                          for a in (task.get('assignees') or [])) or '—'
    sec1 = '\n'.join([
        '任务编号：%s' % (task.get('task_no') or ''),
        '责任单位：%s' % (task.get('scope_name') or ''),
        '发起人：%s（%s）' % (creator.get('name') or '', creator.get('login_name') or ''),
        '承办人：%s' % assignees,
        '回执时限：%s' % (task.get('reply_due_at') or '—'),
        '完成时限：%s' % (task.get('due_at') or '—'),
        '当前状态：%s' % (task.get('status') or ''),
    ])
    sec2 = ''
    if alert:
        sec2 = '\n'.join([
            '预警：%s' % (alert.get('title') or ''),
            '级别：%s / %s' % (alert.get('level') or '', alert.get('subtype') or ''),
            '关键数据：%s' % (alert.get('data') or {}),
        ])
    sec2 = sec2 or '无关联预警（由对话/人工发起）。'
    events = task.get('events') or []
    sec3 = '\n'.join('%s %s：%s' % (e.get('create_time') or '', e.get('action') or '',
                                    (e.get('content') or '')[:120]) for e in events[-10:]) or '暂无。'
    checks = task.get('checks') or []
    sec4 = '\n'.join([
        '核销方式：%s' % ('自动（数据实测）' if task.get('verifier_type') == 'auto' else '人工确认'),
        '最近一次实测：%s' % ((checks[0].get('message') if checks else '—') or '—'),
        '结果：待填',
        '下一步：待填',
    ])
    rows = [[e.get('create_time'), e.get('action'), (e.get('actor_name') or ''),
             (e.get('from_status') or '') + '→' + (e.get('to_status') or ''),
             (e.get('content') or '')[:60]] for e in events]
    return {
        'title': task.get('title') or '处置情况报告',
        'sections': [('一、基本情况', sec1), ('二、问题与依据', sec2),
                     ('三、已采取措施', sec3), ('四、结果与下一步', sec4)],
        'tables': [('处置过程', ['时间', '动作', '操作人', '状态变化', '说明'], rows)] if rows else [],
    }
