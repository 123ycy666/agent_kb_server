# -*- coding: utf-8 -*-
"""第 6 步「问题确定」（2026-11-03 定稿，Fred；2026-11-04 去掉代码里的输出格式）。

这一步是**信息处理**：把前面几步的产物（原话 / 更正后 / 补后 / 补全表命中 /
最近几轮问答）整理成一句能直接往下问的问题，第 7 步只拿这一句干活。
怎么整理（拼哪些、什么算负面优化、要不要退回原话）全写在页面框 `confirm` 里，
**代码不规定输出格式**：输入是资料，输出由框定。

旧做法（已废）：把上面这一堆原样灌给主循环，让它一边查数一边自己分辨
"这个补全沾不沾边""时间该用上下文还是用默认"。取舍和责任散在两处，改一处不管用。

框空着 = 不整理，把参考资料原样交给第 7 步（`run` 返回 used=False，`raw_block` 拼好）。
"""
from __future__ import annotations


def raw_block(question: str, fixed: dict, comp: dict, scope: dict, history=None) -> str:
    """第 6 步的输入资料（框空时这份原样下传给第 7 步）。

    ★ 2026-11-04 重排形状：原来把「原话 / 更正后 / 补问法后」三句近义的问题平铺，
    还有两行恒空的「时间：这轮说的=…补出来的=…」，真正上一轮的时间和地区埋在一句
    长话里 —— 模型每次挑的问句都不一样，行为跟着抖。
    现在三块：本轮的原话、上一轮用过的地区和时间（明写成字段）、兜底的问法和方面
    （只在用户自己没说清时才该用）。
    """
    fixed_text = str((fixed or {}).get('text') or question or '')
    comp = comp or {}
    comp_text = str(comp.get('text') or '')
    items = list(comp.get('hits') or [])
    lines = ['【这一轮】', '· 原话：%s' % (question or '')]
    if fixed_text and fixed_text != (question or ''):
        lines.append('· 更正后：%s' % fixed_text)
    hist = list(history or [])[-1:]
    if hist:
        h = hist[-1]
        lines.append('· 上一轮用过：地区=%s ｜ 时间=%s'
                     % (h.get('place') or '无', h.get('time_text') or '无'))
    if comp_text or items:
        lines.append('· 兜底（用户自己没说清才用）：')
        if comp_text and comp_text != fixed_text:
            lines.append('  - 问法：%s' % comp_text)
        for h in items:
            # 命中不止一条时要能分辨：这条方面是为原话里哪个说法配的（2026-11-04）。
            lines.append('  - 方面（原话里的「%s」）：%s'
                         % (h.get('match') or '', h.get('fill')))
    return '\n'.join(lines)


def run(question: str, fixed: dict, comp: dict, scope: dict, history=None,
        ask=None) -> dict:
    """跑第 6 步。返回 {'used': bool, 'text': 整理后的问题 / 空, 'block': 参考资料}。

    `ask(system, user)` 由调用方注入（agent 的一次模型调用）；框空或没有 ask → 不整理。
    user 只有资料、没有格式要求：输出长什么样由框说了算。
    """
    block = raw_block(question, fixed, comp, scope, history)
    out = {'used': False, 'text': '', 'block': block}
    try:
        from app.secretary import prompt_text
        rule = (prompt_text.get('confirm', '') or '').strip()
    except Exception:                              # noqa: BLE001 - 读不到框就不归整
        return out
    if not rule or ask is None:
        return out
    # 只给资料，不给格式：这一步要什么样，全由页面框决定（2026-11-04 Fred）。
    user = block
    try:
        text = str(ask(rule, user) or '').strip()
    except Exception as exc:                       # noqa: BLE001 - 归整失败就原样下传
        print('[confirm] 问题确定失败（按不归整处理）：%s: %s'
              % (type(exc).__name__, str(exc)[:120]), flush=True)
        return out
    if not text:
        return out
    out['used'] = True
    out['text'] = text
    return out

