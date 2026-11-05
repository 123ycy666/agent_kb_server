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


def system(mode: str, legacy, today=None, persona: str | None = None,
           prefs: dict | None = None, priority: str | None = None,
           answer_format: str | None = None, personal_rules: str | None = None,
           with_rules: bool = True) -> str:
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
        # ★ 2026-11-05：查数口径/判定标准只在「要查业务库」的轮次注入。
        #   聊天和文件处理轮带着这几万字，既占上下文又干扰回答。
        if not with_rules:
            rules_menu = ''
        # ★ 2026-11-03（Fred）：`prompt_material.SLOTS` 已置空（主分支空壳化），
        #   上面这行于是恒为 ''，system 里不再有任何规则材料段；`get_rule` 也取不到东西。
        # 身份 / 模式做法允许页面覆盖（prompt_text 表），没有覆盖就用代码里的默认值
        _identity = project.fill(prompt_text.get('identity', prompts_data.IDENTITY))
        _modes = prompt_text.mode_texts(prompts_data.MODE_TEXTS)
        head = prompt_stack.build(
            mode=mode, identity=_identity, modes=_modes,
            priority=priority,
            # ★ 2026-11-03：红线也搬到页面（prompt_text.redlines，默认空）
            redlines=(prompt_text.get('redlines', '') or ''),
            # ★ 2026-11-03：S4「作答格式」下线 —— 不再拼 prompts_data.ANSWER_DATA，
            #   answer_format 由调用方传（空串就整段不注入）。
            answer_format=answer_format or None,
            rules_menu=rules_menu or None,
            persona=persona, prefs=prefs)['system']
        if personal_rules:
            head += '\n\n' + personal_rules
        # ★ 2026-11-03（Fred）：硬编码的「今天的日期是…」不再注入 ——
        #   它是"我不填也生效的字"。要日期就在页面上自己写。
        if (os.environ.get('SECRETARY_INJECT_DATE') or '').strip() == '1':
            head += ('\n\n今天的日期是 %s。' % (today or time.strftime('%Y-%m-%d')))
        # ★ 2026-11-03（Fred）：**不注入任何业务目录** —— 表清单 / 字典表都不进提示词，
        #   模型要表结构就用 describe_table(表名) / find_column 现取。
        #   提示词里能出现什么，只由页面上的框决定（身份 / 红线 / 优先级 / 意图 / 问题确定 / 材料）。
        return head
    except Exception as exc:                       # noqa: BLE001
        print('[agent_v2] 装配失败，回落旧装配：%s' % exc)
        return legacy(today)


def use_tools(mode: str) -> bool:
    """通用模式不给工具（它不需要查库，给了只会去查）；其余模式照旧。"""
    return mode != 'general'
