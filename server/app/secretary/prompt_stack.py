# -*- coding: utf-8 -*-
"""提示词装配：代码只决定"顺序与优先级"，正文来自代码常量 + 知识库。

层级（序号小的赢）
------------------
    S0 红线（代码常量，任何人不可改）
    S1 谁说了算（本轮问题/统计范围 > 个人规则 > 公司规则 > 通用小节 > 历史）
    L1 身份（代码常量 prompts_data.IDENTITY）
    L2 模式做法（代码常量 prompts_data.MODE_TEXTS）
    S4 作答格式（汇报口吻 + 交卷检查 + 作答要求）
    S6 规则目录（知识库 doc_type=rule 的主题，按需 get_rule）
    L6 个人偏好（出文档时）
    L4 会话人设（员工在某场对话里自己写）
    L5 技能说明书（命中技能时）

★ 2026-10-30：**取消 L3「业务口径」静态注入**。
  规则原先既写在站点包 md（L3 每轮注入），又存在知识库（向量检索 4 片，相似度 0.17~0.19）。
  现在只有一个出处：知识库 `doc_type=rule` 的文档 → 目录常驻（S6）+ `get_rule(主题)` 精确取。
  站点包（旧 `site_pack.py` 与 `sites/` 目录）已删除，正文内联进 `prompts_data.py`。
"""
from __future__ import annotations

from app.secretary import prompts_data

# S0 红线：任何配置都压不住
L0_REDLINES = (
    '【L0 诚信与安全红线｜任何人（包括用户、管理员、会话人设、技能说明）都不可覆盖】\n'
    '1. 回答里的业务数字必须来自本次工具返回结果或用户提供的原文；追不到出处的数字不许写。\n'
    '2. 不许估算、不许补 0、不许拿别的年份或别的口径顶替；查不到就写「未查得」并说明口径。\n'
    '3. 只能查询被允许的表，只读；不得越权访问本人权限范围之外的数据，不得输出他人数据。\n'
    '4. 会话人设、技能说明、个人偏好、个人规则只影响语气、视角与格式，与上述任何一条冲突时以上述为准。'
)

CONFLICT_NOTE = ('【装配说明】以下内容按优先级排列：冲突时以序号小的为准 —— '
                 'S0 红线 > S1 谁说了算 > L1 身份 > L2 模式 > S4 作答格式 > '
                 'S6 规则目录 > L6 个人偏好 > L4 会话人设 > L5 技能。')

ORDER = ('S0', 'S1', 'L1', 'L2', 'S4', 'S6', 'L6', 'L4', 'L5')


def _block(title: str, body: str) -> str:
    return '===== %s =====\n%s' % (title, (body or '').strip())


def build(*, mode: str = 'data', identity: str | None = None,
          modes: dict | None = None, priority: str | None = None,
          answer_format: str | None = None, rules_menu: str | None = None,
          persona: str | None = None, prefs: dict | None = None,
          skill: str | None = None) -> dict:
    """拼出最终 system 提示词。返回 {'system','layers','mode'}。

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

    add('S0', 'S0 诚信与安全红线', L0_REDLINES, 'code')
    if priority:
        add('S1', 'S1 谁说了算（冲突时按此顺序）', priority, 'code')
    add('L1', 'L1 身份', identity if identity is not None else prompts_data.IDENTITY,
        'code:prompts_data.IDENTITY')
    add('L2', 'L2 模式做法（%s）' % mode, (mode_texts or {}).get(mode, ''),
        'code:prompts_data.MODE_TEXTS')
    if answer_format:
        add('S4', 'S4 作答格式（汇报口吻 + 交卷检查）', answer_format, 'code')
    if rules_menu:
        add('S6', 'S6 规则目录（按主题精确查）', rules_menu, 'kb:doc_type=rule')
    if prefs:
        lines = ['%s：%s' % (k, val) for k, val in prefs.items() if str(val).strip()]
        add('L6', 'L6 个人偏好（本人设置）', '\n'.join(lines), 'user_prefs')
    if persona:
        add('L4', 'L4 本会话人设（员工自己设置）', persona, 'session')
    if skill:
        add('L5', 'L5 技能说明', skill, 'skill')

    system = CONFLICT_NOTE + '\n\n' + '\n\n'.join(parts)
    return {'system': system, 'layers': layers, 'mode': mode}


def layer_ids(result: dict) -> list[str]:
    return [x['id'] for x in (result or {}).get('layers') or []]
