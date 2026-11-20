# -*- coding: utf-8 -*-
"""项目事实：现场名 / 全称 / 层级 —— 2026-11-19 起**全部存当前库**（`cfg.site_info`）。

为什么改：一个现场 = 一个库，同一份代码服务多个现场，代码里不能出现任何具体单位名（NFR-004）。
这里只留读取入口与占位符替换；具体值在库里，页面/现场配置可改，切现场跟着换。

读不到就是空串 —— 不回落任何现场的内容（FR-MIGR-001）。
"""
from __future__ import annotations

from app.secretary import site_info


def _name() -> str:
    return site_info.get("site_name")


def _full() -> str:
    return site_info.get("site_full")


def fill(text: str) -> str:
    """把提示词正文里的 {SITE_NAME} / {SITE_FULL} / {SITE_CODE} 换成当前现场的值。

    身份正文的原文就是带占位符的（存在 `cfg.prompt_text` 里），装配与页面展示都走这里。
    """
    s = str(text or "")
    if "{" not in s:
        return s
    return (s.replace("{SITE_FULL}", _full())
             .replace("{SITE_NAME}", _name())
             .replace("{SITE_CODE}", site_info.get("site_code")))


def department() -> dict:
    """部门锚点 + 县公司清单（原 scopes.yml 的 department 节，现在在库里）。"""
    return {
        "city_dept_id": site_info.get_int("city_dept_id", 1),
        "city_label": site_info.get("city_label"),
        "county_roles": site_info.get_list("county_roles"),
        "city_names": site_info.get_list("city_names"),
        "county_depts": site_info.get_list("county_depts"),
    }


def default_station() -> str:
    return site_info.get("default_station")


FEATURES = {'general': True, 'data': True, 'doc': True, 'office': True}
TOOL_BUDGET = 12
