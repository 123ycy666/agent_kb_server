# -*- coding: utf-8 -*-
"""核实闸：这一轮模型没调工具时，这句答案能不能直接交出去。

为什么单独一个模块（2026-11-02）
--------------------------------
旧闸门是「问题侧词表 + 答案侧数字」两条正则：
  · `agent._BIZ`（问题里有没有业务词）决定要不要逼它查库；
  · `agent._CLAIM`（答案里有没有「数字 + 单位」）决定算不算下了结论。
内网实测（测试.txt 三连）的后果：
  「全市情况怎么样」没命中任何业务词 → 判 general → **不给工具** →
  模型只能写「数据缺失，无法作答」；这句话没有数字，于是被当成通用问答放行；
  同一个问题若它顺手写了数字，反被要求核实、最后拦下。
两条路都不对，根源是**问题侧词永远补不完**（全市/全县/全公司/啥情况/咋样…）。

现在只看答案侧：答案里有没有「没有工具结果支撑的具体断言」。
这件事机器能判，不需要认识业务词；问题怎么问都不影响判词。
纯逻辑：不碰数据库、不碰网络，单测见 tests/agent/test_evidence_gate.py。
"""
from __future__ import annotations

import re

# 闸门三个动作
ACT_PASS = 'pass'      # 可以交卷
ACT_NUDGE = 'nudge'    # 先给一次机会（提醒 + 把工具补上）
ACT_BLOCK = 'block'    # 仍不核实 → 拦下，不给领导

# 数字 + 单位：这是在给业务数字（「0 条 / 0 行」同样算）
_UNITS = ('万|亿|千瓦时|千瓦|kWh|kwh|%|％|件|个|条|户|元|次|小时|天|台|人|项|笔|张|份|倍')
_CLAIM = re.compile(r'\d+(?:\.\d+)?\s*(?:' + _UNITS + r')|百分之[一二三四五六七八九十百千零几\d.]+')

# 「没有」也是一种断言：没查就说没有，是最容易骗过领导的答案
_MISSING = re.compile(
    r'数据缺失|缺少数据|没有数据|无数据|未查得|未查到|查不到|查无|无记录|没有记录|'
    r'无相关记录|暂无|尚无|未采集|没有采集|未获取|未取到|不存在')


def evidence_reason(answer: str) -> str:
    """答案里第一处「需要工具支撑却没支撑」的断言；没有就返回空串。

    返回的是给 trace 看的一句话 —— 内网排障时要能一眼看出为什么被拦/被放。
    """
    a = answer or ''
    m = _CLAIM.search(a)
    if m:
        return '答案里出现具体数字「%s」，但本轮没有工具结果支撑' % m.group(0)
    m = _MISSING.search(a)
    if m:
        return '答案里断言「%s」，但本轮没有工具结果支撑' % m.group(0)
    return ''


def decide(*, used_tool: bool, nudged: bool, answer: str,
           mode: str = 'data') -> tuple[str, str]:
    """返回 (动作, 理由)。

    · 查过工具 → pass（照旧另起一次「写汇报」）
    · 没查工具、答案里也没有具体断言 → pass（问候/写字/解释概念这类本来不该查库）
    · 没查工具、属于查数模式（mode='data'）→ 先 nudge 一次：不把查数题当闲聊答完
    · 没查工具、但答案里有具体断言 → 先 nudge；已经 nudge 过还敢说 → block
      **只有 data 模式才拦**：写通知/写汇报里的「3 项要求」「12 个乡镇」是正文内容，
      不是查数结论；拦掉等于让领导交不了差（这类由提示词去管，代码别替它判）。
    """
    if used_tool:
        return ACT_PASS, ''
    why = evidence_reason(answer)
    if not why:
        if mode == 'data' and not nudged:
            return ACT_NUDGE, '查数题这一轮没调工具，先给一次核实机会（并补上工具）'
        return ACT_PASS, '答案里没有需要工具支撑的具体断言'
    if not nudged:
        return ACT_NUDGE, why
    if mode == 'data':
        return ACT_BLOCK, why
    return ACT_PASS, why + '（非查数模式：提醒过就不再拦，按时限出材料）'
