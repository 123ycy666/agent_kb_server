# -*- coding: utf-8 -*-
"""提示词装配：身份 + 材料两段，顺序固定。

正文全部来自页面（`cfg.prompt_text` / `cfg.prompt_material`），代码里不写业务文本。
"""
from __future__ import annotations


def build(*, identity: str = '', materials: str = '') -> str:
    """拼 system：身份在前，材料在后；哪一段空就整段不出现。"""
    parts = [str(p).strip() for p in (identity, materials) if str(p or '').strip()]
    return '\n\n'.join(parts)
