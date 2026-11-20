# -*- coding: utf-8 -*-
"""问数线两段 system 装配：查数段（身份 + 查数口径）、整理段（作答规则）。

正文全部来自页面：`cfg.prompt_text.identity`、`cfg.prompt_material` 的 fetch 与 answer。
**作答规则不进查数循环** —— 它排在 system 末尾，模型读到最后一段（「生成给前端展示的
中文回答」「与业务无关也可以直接用简短通用回复」）就不查库、直接写答案（2026-11-19 复现）。
所以查数段只装 fetch，作答规则在循环结束后单独调一次模型时用。
代码里不再有模式、红线、优先级、人设、偏好、工具说明。
"""
from __future__ import annotations


def system(today: str | None = None) -> str:
    """查数段的 system。today 不再进 system（在 user 消息里给）。"""
    from app.secretary import project, prompt_material, prompt_stack, prompt_text, prompts_data
    identity = project.fill(prompt_text.get('identity', prompts_data.IDENTITY_FALLBACK))
    materials = prompt_material.block(('fetch',), index_slots=('fetch', 'answer'))
    return prompt_stack.build(identity=identity, materials=materials)


def answer_rules() -> str:
    """整理段的 system：只放作答规则，不带规则清单。空串 = 没有这一段。"""
    from app.secretary import prompt_material
    return prompt_material.block(('answer',), with_index=False)
