# -*- coding: utf-8 -*-
"""问数线 system 装配：身份 + 查数口径 + 作答规则。

三段全部来自页面：`cfg.prompt_text.identity`、`cfg.prompt_material` 的 fetch 与 answer。
代码里不再有模式、红线、优先级、人设、偏好、工具说明。
"""
from __future__ import annotations


def system(today: str | None = None) -> str:
    """问数线这一轮的 system。today 不再进 system（在 user 消息里给）。"""
    from app.secretary import project, prompt_material, prompt_stack, prompt_text, prompts_data
    identity = project.fill(prompt_text.get('identity', prompts_data.IDENTITY))
    materials = prompt_material.block(('fetch', 'answer'))
    return prompt_stack.build(identity=identity, materials=materials)
