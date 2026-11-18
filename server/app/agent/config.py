# -*- coding: utf-8 -*-
"""智能体运行参数。全部可被 /runtime 与环境变量覆盖，代码里不写死业务值。"""
from __future__ import annotations

import os

from app.secretary import config as CFG


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except Exception:                              # noqa: BLE001
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except Exception:                              # noqa: BLE001
        return default


def llm() -> tuple[str, str, str]:
    """(base_url, api_key, model)。runtime_cfg 里改过模型时以它为准。"""
    try:
        from app.secretary import runtime_cfg
        runtime_cfg.refresh()
    except Exception:                              # noqa: BLE001
        pass
    model = (os.environ.get('SECRETARY_AGENT_MODEL') or CFG.MODEL or '').strip()
    return CFG.LLM_BASE, CFG.LLM_KEY, model


MAX_TURNS = _int('SECRETARY_AGENT_MAX_TURNS', 30)
CONTEXT_BUDGET = _int('SECRETARY_AGENT_CONTEXT_BUDGET', 60000)     # token 估算
COMPACT_AT = _float('SECRETARY_AGENT_COMPACT_AT', 0.6)
KEEP_RECENT = _int('SECRETARY_AGENT_KEEP_RECENT', 12)
TOOL_CAP = _int('SECRETARY_AGENT_TOOL_CAP', 8000)                  # 单次工具结果字数
CATALOG_MAX = _int('SECRETARY_AGENT_CATALOG_MAX', 30)
ABOUT_MAX = _int('SECRETARY_AGENT_ABOUT_MAX', 5)
