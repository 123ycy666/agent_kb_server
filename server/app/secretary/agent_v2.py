# -*- coding: utf-8 -*-
"""V2 装配入口：判模式、按模式装提示词、按模式决定给不给工具。

为什么单独一个文件（2026-10-21）
-----------------------------
`agent.py` 有 1500 行、主循环与守卫都在里面，直接往里插逻辑风险高。
所以 V2 的**决策**都放这里，`agent.py` 只留三处一行的调用：
    judge_mode()  → 这一问走哪个模式
    system()      → 该模式的 system 提示词（分层装配）
    use_tools()   → 这一轮给不给工具

三条回退保险（任何一条成立就按旧行为跑）
--------------------------------------
1. `SECRETARY_AGENT_V2=0`
2. 装配异常 → 回落 `agent._system`
3. 判模式抛异常 → 落 data

★ 2026-10-30：站点包已删除。提示词正文内联在 `prompts_data.py`，
  规则不再静态注入（只给「目录」+ `get_rule` 按需）。

自检：py -X utf8 server/tests/agent/test_rule_stack.py
"""
from __future__ import annotations

import os
import re
import time

from app.secretary import prompts_data
from app.secretary import project

_CACHE: dict = {}


def enabled() -> bool:
    return (os.environ.get('SECRETARY_AGENT_V2') or '1').strip() not in ('0', 'false', 'False')


def pack() -> dict:
    """兼容旧调用方的"装配素材"视图（**不再是站点包**，内容全在代码里）。"""
    if 'p' not in _CACHE:
        _CACHE['p'] = {
            'site': project.SITE,
            'vars': {'SITE_NAME': project.SITE_NAME, 'SITE_FULL': project.SITE_FULL,
                     'SITE_CODE': project.SITE_CODE},
            'prompts': {'identity': prompts_data.IDENTITY,
                        'answer_data': prompts_data.ANSWER_DATA},
            'modes': dict(prompts_data.MODE_TEXTS),
            'doc_defaults': dict(prompts_data.DOC_DEFAULTS),
        }
    return _CACHE['p']


def judge_mode(question: str, has_files: bool = False) -> str:
    """判模式；关闭或出错都返回 'data'（等于旧行为）。"""
    if not enabled():
        return 'data'
    try:
        from app.secretary import modes
        return modes.judge(question, has_files=has_files)['mode']
    except Exception as exc:                       # noqa: BLE001
        print('[agent_v2] 判模式失败，按 data 处理：%s' % exc)
        return 'data'


def tool_context() -> str:
    """查数要用的目录：业务码表 + 可用数据表清单（前面带两个换行，可直接拼）。

    system 与「核实提醒」共用同一份 —— 提醒时若是纯写作模式起的头，
    这份目录原先根本没进 system，模型即使被解锁工具也不知道能查哪张表。
    """
    parts = []
    try:
        codes = semantic.codes_menu()
        if codes:
            parts.append(codes)
    except Exception:                              # noqa: BLE001
        pass
    try:
        parts.append('【可用数据表（只能查这些，字典外的表一律不可用）】\n'
                     '字段明细请用 describe_table(表名) 现取，不要凭印象写字段。\n'
                     + semantic.table_list_menu())
    except Exception:                              # noqa: BLE001
        pass
    return ('\n\n' + '\n\n'.join(parts)) if parts else ''


def system(mode: str, legacy, today=None, persona: str | None = None,
           prefs: dict | None = None, priority: str | None = None,
           answer_format: str | None = None, personal_rules: str | None = None) -> str:
    """该模式的 system 提示词。

    legacy 是 agent._system（旧装配），用于回退——签名 legacy(today)。
    priority / answer_format / personal_rules：分别对应 S1「谁说了算」、
    S4「作答格式」、S7「我的规则」，由调用方（agent.ask）传入。
    """
    if not enabled():
        return legacy(today)
    try:
        from app.secretary import prompt_stack, semantic, prompt_material, prompt_text
        # 规则材料（2026-11-01）：不再是「目录 + 按需取」，而是按阶段把这一阶段的
        # 必带小节直接拼进 system。查数循环这一段带 fetch + judge。
        # 换，不叠：收尾写正文前由 agent._swap_materials 换成 answer + check。
        # ★ 2026-11-02：清单按**四份**算（index_slots=SLOTS）—— 实测收尾阶段才发现
        #   有 answer 的题型清单，而那时工具已关，那 19 条从来没被读过。现在查数阶段
        #   就能看见"这题该怎么答"的代号，可以提前 get_rule。
        rules_menu = prompt_material.block(('fetch', 'judge'),
                                           index_slots=prompt_material.SLOTS)
        # 身份 / 模式做法允许页面覆盖（prompt_text 表），没有覆盖就用代码里的默认值
        _identity = prompt_text.get('identity', prompts_data.IDENTITY)
        _modes = prompt_text.mode_texts(prompts_data.MODE_TEXTS)
        head = prompt_stack.build(
            mode=mode, identity=_identity, modes=_modes,
            priority=priority,
            answer_format=((answer_format or '') + '\n\n' + prompts_data.ANSWER_DATA).strip(),
            rules_menu=rules_menu or None,
            persona=persona, prefs=prefs)['system']
        if personal_rules:
            head += '\n\n' + personal_rules
        head += ('\n\n今天的日期是 %s。所有「停留天数 / 截至今天」一律按这一天算，'
                 '不要拿数据里的时间当今天。' % (today or time.strftime('%Y-%m-%d')))
        # 通用模式**不塞**业务码表与数据表目录：那是查数专用上下文，
        # 塞进去会把「写个工作总结」拽回业务轨道 —— 这正是要解开的锁。
        # 反过来，判成 general 之后才发现它要查数的（2026-11-02 内网实测），
        # 由 agent 那一侧补救：触发核实提醒时用 tool_context() 把这份目录补进对话。
        if mode in ('data', 'doc'):
            head += tool_context()
        return head
    except Exception as exc:                       # noqa: BLE001
        print('[agent_v2] 装配失败，回落旧装配：%s' % exc)
        return legacy(today)


def use_tools(mode: str) -> bool:
    """通用模式不给工具（它不需要查库，给了只会去查）；其余模式照旧。"""
    return mode != 'general'
