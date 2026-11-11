# -*- coding: utf-8 -*-
"""第 5 步「问题确定」（2026-11-03 定稿；2026-11-08 只整理问题，别的一概不碰）。

这一步只干一件事：把前面几步的产物整理成**一个确定的问题**，第 6 步照着它干活、
不再自己判断（该答哪几个方面、什么范围、什么时间，全在这一句里定死）。
怎么整理——用哪句问法、把补全的哪些方面落进去、时间地点从哪继承——**全写在页面框
`confirm` 里**（页面 `/prompts` 的「问题确定」）。

★ 2026-11-08（Fred）：
  · 框空 → **什么都不做，原路下发**（`raw_block` 六段资料原样交给第 6 步，它自己判断）；
  · 框有内容 → 调模型按框整理，产物就是第 6 步的问题；
  · 这一步不拼"要答的方面"（那是第 4 步补全的产物，直接给下游），也不改问题措辞
    之外的任何东西。

旧做法（已废）：把「原话 / 更正后 / 上一轮地区时间 / 兜底问法 / 方面」平铺还要标
"仅供参考"，模型把补全当可选参考，自己挑着答。
"""
from __future__ import annotations


def raw_block(question: str, fixed: dict, comp: dict, history=None,
              ask_time: str = '') -> str:
    """送进 `confirm` 框的资料：全是事实，不带任何"参考/兜底"字眼。

    ★ 2026-11-11：多一段【提问时间】—— 问问题的时候带进来的（接口 `today` /
      `business_date`，不传就按系统当天）。原话没说时间时，默认补成它的上一个
      自然月靠这一段，模型不用再猜"今天是几号"。
    """
    fixed_text = str((fixed or {}).get('text') or question or '').strip()
    comp = comp or {}
    comp_text = str(comp.get('text') or '').strip()
    items = [str(h.get('fill') or '').strip() for h in (comp.get('hits') or [])]
    items = [x for x in items if x]
    lines = ['【提问时间】%s' % (str(ask_time or '').strip() or '（没传）'),
             '【本轮原话】%s' % (question or ''),
             '【更正后】%s' % (fixed_text or question or ''),
             '【补问法后】%s' % (comp_text or fixed_text or question or ''),
             '【补全表命中】%s' % ('\n'.join(items) if items else '（无）')]
    hist = list(history or [])[-3:]
    if hist:
        rows = []
        for h in hist:
            rows.append('用户：%s ／ 系统：%s ／ 上轮用过 时间=%s 地区=%s'
                        % (str(h.get('raw_question') or '')[:60],
                           str(h.get('answer') or '').replace('\n', ' ')[:80],
                           h.get('time_text') or '无', h.get('place') or '无'))
        lines.append('【最近几轮】\n' + '\n'.join(rows))
    else:
        lines.append('【最近几轮】（无）')
    return '\n'.join(lines)


def run(question: str, fixed: dict, comp: dict, history=None,
        ask=None, ask_time: str = '') -> dict:
    """跑第 5 步。返回 {'used': bool, 'text': 整理后的问题 / 空, 'block': 资料}。

    `ask(system, user)` 由调用方注入（agent 的一次模型调用）；框空或没有 ask → 不调模型，
    原路下发（block 就是第 6 步的输入）。
    `ask_time` = 提问时间（YYYY-MM-DD），原话没说时间时「默认上一个月」的基准。
    """
    block = raw_block(question, fixed, comp, history, ask_time)
    out = {'used': False, 'text': '', 'block': block}
    try:
        from app.secretary import prompt_text
        rule = (prompt_text.get('confirm', '') or '').strip()
    except Exception:                              # noqa: BLE001 - 读不到框就不整理
        return out
    if not rule or ask is None:
        return out
    # 只给资料，不给格式：输出长什么样由页面框决定。
    try:
        text = str(ask(rule, block) or '').strip()
    except Exception as exc:                       # noqa: BLE001 - 归整失败就原路下发
        print('[confirm] 问题确定失败（原路下发）：%s: %s'
              % (type(exc).__name__, str(exc)[:120]), flush=True)
        return out
    if not text:
        return out
    out['used'] = True
    out['text'] = text
    return out
