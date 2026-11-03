# -*- coding: utf-8 -*-
"""提示词装配：代码只决定"顺序"，正文来自页面（`prompt_text`）—— 不填就没有。

层级（序号小的赢）
------------------
    S0 红线（页面可填，默认空）
    S1 谁说了算（页面可填，默认空）
    L1 身份（页面可填，默认 = prompts_data.IDENTITY）
    L2 模式做法（页面可填，默认 = prompts_data.MODE_TEXTS）
    L6 个人偏好（出文档时）
    L4 会话人设（员工在某场对话里自己写）
    L5 技能说明书（命中技能时）

★ 2026-11-03（Fred）：主分支要的是"我填什么就是什么"，所以代码里不再硬塞业务文本：
  · **删掉**：装配说明（"以下内容按优先级排列…"）、L0 红线常量、S1 优先级常量。
    这两段原文备份在 `docs/08-历史归档/2026-11-03-删除的提示词规则/`。
    要恢复就在页面上往 `redlines` / `priority` 两个 key 里写 —— 写了才注入，不写就没有。
  · **删掉**：S4 作答格式（汇报口吻 + 交卷检查）、S6 规则材料（主分支 SLOTS 为空）。
  · 保留：身份、模式做法、个人偏好、会话人设、技能说明（都是会话骨架的一部分）。

★ 2026-10-30：取消 L3「业务口径」静态注入；规则唯一出处是 online 分支的 `prompt_material`。
"""
from __future__ import annotations

from app.secretary import prompts_data

# 装配说明：2026-11-03 起不再注入（它就是"其他提示词干扰"的一部分）。
CONFLICT_NOTE = ''

# 现在可能出现的层（S0/S4/S6 只在页面/分支给了内容时才有）
ORDER = ('S0', 'S1', 'L1', 'L2', 'L6', 'L4', 'L5')


def _block(title: str, body: str) -> str:
    return '===== %s =====\n%s' % (title, (body or '').strip())


def build(*, mode: str = 'data', identity: str | None = None,
          modes: dict | None = None, priority: str | None = None,
          redlines: str | None = None,
          answer_format: str | None = None, rules_menu: str | None = None,
          persona: str | None = None, prefs: dict | None = None,
          skill: str | None = None) -> dict:
    """拼出最终 system 提示词。返回 {'system','layers','mode'}。

    正文参数**传空就不注入**：空壳部署时 S0/S1/S4/S6 一段都不出现，
    system 里只剩身份 + 模式做法（这两份也在页面上，改了就生效）。
    layers 供日志与验收用（哪几段进了 system、各多少字、来源是什么）。
    """
    layers: list[dict] = []
    parts: list[str] = []
    mode_texts = modes if modes is not None else prompts_data.MODE_TEXTS

    def add(lid: str, title: str, text: str, source: str) -> None:
        t = (text or '').strip()
        if not t:
            return
        layers.append({'id': lid, 'title': title, 'chars': len(t), 'source': source})
        parts.append(_block(title, t))

    # S0 / S1：页面填了才注入（默认空 = 空壳）
    if redlines:
        add('S0', 'S0 诚信与安全红线', redlines, 'prompt_text:redlines')
    if priority:
        add('S1', 'S1 谁说了算（冲突时按此顺序）', priority, 'prompt_text:priority')
    add('L1', 'L1 身份', identity if identity is not None else prompts_data.IDENTITY,
        'code:prompts_data.IDENTITY')
    add('L2', 'L2 模式做法（%s）' % mode, (mode_texts or {}).get(mode, ''),
        'code:prompts_data.MODE_TEXTS')
    # S4/S6 2026-11-03 下线；入参留着，online 分支或将来要恢复时不用改调用方
    if answer_format:
        add('S4', 'S4 作答格式', answer_format, 'code')
    if rules_menu:
        add('S6', 'S6 规则材料（本阶段注入）', rules_menu, 'table:prompt_material')
    if prefs:
        lines = ['%s：%s' % (k, val) for k, val in prefs.items() if str(val).strip()]
        add('L6', 'L6 个人偏好（本人设置）', '\n'.join(lines), 'user_prefs')
    if persona:
        add('L4', 'L4 本会话人设（员工自己设置）', persona, 'session')
    if skill:
        add('L5', 'L5 技能说明', skill, 'skill')

    system = '\n\n'.join(parts)
    return {'system': system, 'layers': layers, 'mode': mode}


def layer_ids(result: dict) -> list[str]:
    return [x['id'] for x in (result or {}).get('layers') or []]
