# -*- coding: utf-8 -*-
"""第 6 步「问题确定」（2026-11-03 定稿，Fred）。

这一步是**信息处理**：把前面几步的产物（原话 / 更正后 / 补后 / 补全表命中 /
时间地点补前补后 / 最近几轮问答）揉成**一份定稿**，第 7 步只拿这份定稿干活。

旧做法（已废）：把上面这一堆原样灌给主循环，让它一边查数一边自己分辨
"这个补全沾不沾边""时间该用上下文还是用默认"。取舍和责任散在两处，改一处不管用。

判据写在页面框 `confirm` 里（Fred 自己写，随时改）：
    没有上下文 → 补出来的就是对；
    有上下文、本轮没说时间地点、补的是默认值、上一轮明确用过 → 用上下文里的；
    补全表命中的不沾边 → 丢掉；补的"情况怎么样"不合原意 → 用原问题。

框空着 = 不归整，把参考资料原样交给第 7 步（`run` 返回 used=False，`raw_block` 拼好）。
"""
from __future__ import annotations

_CONFIRM_DEFAULT_LINES = ('要什么', '时间', '范围', '其它要求')


def _scope_bits(scope: dict) -> tuple[str, str, str, str]:
    """(时间补前, 时间补后, 地点补前, 地点补后) —— 补前的没有就写「你这次没说」。"""
    sc = scope or {}
    t = sc.get('time') or {}
    t_after = str(t.get('time_text') or '')
    t_before = '（这次没说）' if t.get('is_default') else t_after
    p_after = str(sc.get('place') or '')
    p_before = '（这次没说）' if sc.get('place_default') else p_after
    return t_before, t_after, p_before, p_after


def raw_block(question: str, fixed: dict, comp: dict, scope: dict, history=None) -> str:
    """不走模型归整时，直接交给第 7 步的参考资料（也是 `run` 的输入正文）。"""
    t_before, t_after, p_before, p_after = _scope_bits(scope)
    fixed_text = str((fixed or {}).get('text') or question or '')
    comp_text = str((comp or {}).get('text') or '')
    items = [x for x in ((comp or {}).get('hits') or [])]
    lines = ['【这次要办的事（参考资料）】',
             '· 原话：%s' % (question or ''),
             '· 更正后：%s' % fixed_text]
    if comp_text and comp_text != fixed_text:
        lines.append('· 补问法后：%s' % comp_text)
    if items:
        lines.append('· 补全表命中的条目（不一定适用）：')
        for h in items:
            lines.append('  - %s ｜ %s → %s'
                         % ('归一' if h.get('kind') == 'norm' else '补缺',
                            h.get('match'), h.get('fill')))
    else:
        lines.append('· 补全表命中的条目：无')
    lines.append('· 时间：这轮说的=「%s」，补出来的=「%s」' % (t_before, t_after))
    lines.append('· 范围：这轮说的=「%s」，补出来的=「%s」' % (p_before, p_after))
    hist = list(history or [])[-3:]
    if hist:
        lines.append('· 最近几轮（上一轮实际用的时间和范围在里面）：')
        for h in hist:
            lines.append('  - 用户：%s ｜ 当时口径：%s / %s ｜ 系统答：%s'
                         % (h.get('raw_question') or '', h.get('time_text') or '-',
                            h.get('place') or '-',
                            str(h.get('answer') or '').replace('\n', ' ')[:160]))
    else:
        lines.append('· 最近几轮：没有（这是第一轮或新会话）')
    return '\n'.join(lines)


def run(question: str, fixed: dict, comp: dict, scope: dict, history=None,
        ask=None) -> dict:
    """跑第 6 步。返回 {'used': bool, 'text': 定稿 / 空, 'block': 参考资料}。

    `ask(system, user)` 由调用方注入（agent 的一次模型调用）；框空或没有 ask → 不归整。
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
    user = ('%s\n\n请按上面这份资料，输出这四项，每项一行，不要解释：\n'
            '要什么：\n时间：\n范围：\n其它要求：' % block)
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


def lines(text: str) -> dict:
    """把定稿拆成四项（页面 / 排障用；缺的项留空）。"""
    out = {k: '' for k in _CONFIRM_DEFAULT_LINES}
    for ln in str(text or '').splitlines():
        s = ln.strip().lstrip('-·* ').strip()
        for k in _CONFIRM_DEFAULT_LINES:
            for sep in ('：', ':'):
                head = k + sep
                if s.startswith(head):
                    out[k] = s[len(head):].strip()
    return out
