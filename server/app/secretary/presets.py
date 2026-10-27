# -*- coding: utf-8 -*-
"""预设问题清单 —— 问答页「点一下就能问」的入口。

为什么要有它（2026-10-20 新增）
-------------------------------
空输入框最劝退：新用户不知道这个系统能答什么、该怎么描述。
给一批**真实能跑通**的示例，比写一段使用说明有效得多。

两条硬约束（都是被用户骂出来的）
--------------------------------
① **必须是业务问题**：这个系统管的是缺陷治理工单 / 项目预算 / 线损指标。
   之前放「年假有几天」「病假工资怎么算」这种 HR 考勤题，用户第一眼
   就以为打开的是个内部制度问答机器人 —— 跟系统的定位完全不符。
② **必须简单**：单事实、单指标、一次检索或一条 SQL 就能答。
   放「今年存在哪些问题、下一步怎么改进」这类分析题，单条要跑 20~40 秒，
   用户点一个等半天会以为系统卡死。预设是用来「快速验证能用」的，不是秀上限。

维护约定
--------
- 每条都必须是**当前环境真有数据、能答出来**的问题。
- 不写死年份/月份（"上个月""今年"由代码按今天算）。
- 分组顺序 = 业务主线顺序（工单 → 预算 → 指标）。
"""
from __future__ import annotations

PRESETS: list[dict] = [
    # ── 缺陷治理工单（系统主线）──────────────────────────────
    {'id': 'b_wo_open', 'group': '缺陷工单', 'text': '现在还有多少条缺陷工单没闭环？'},
    {'id': 'b_wo_new', 'group': '缺陷工单', 'text': '上个月新增了多少条缺陷工单？'},
    {'id': 'b_wo_top', 'group': '缺陷工单', 'text': '哪个供电所的缺陷工单最多？'},
    {'id': 'b_wo_node', 'group': '缺陷工单', 'text': '有多少条工单卡在审核环节？'},
    {'id': 'b_wo_flow', 'group': '缺陷工单', 'text': '缺陷工单审批要经过几个节点？'},

    # ── 项目与预算 ─────────────────────────────────────────
    {'id': 'b_bd_amt', 'group': '项目预算', 'text': '今年项目执行金额是多少？'},
    {'id': 'b_bd_rate', 'group': '项目预算', 'text': '预算执行率最低的是哪个单位？'},
    {'id': 'b_bd_cnt', 'group': '项目预算', 'text': '今年立项了多少个项目？'},
    {'id': 'b_bd_over', 'group': '项目预算', 'text': '有没有超预算的项目？'},

    # ── 线损与经营指标 ─────────────────────────────────────
    {'id': 'b_ls_rate', 'group': '线损指标', 'text': '上个月全市线损率是多少？'},
    {'id': 'b_ls_worst', 'group': '线损指标', 'text': '哪个供电所的线损率最高？'},
    {'id': 'b_ls_sale', 'group': '线损指标', 'text': '今年售电量是多少？'},
]

GROUPS = ['缺陷工单', '项目预算', '线损指标']


def payload() -> dict:
    """给前端的结构（附带分组顺序，前端不用自己猜）。"""
    return {'total': len(PRESETS), 'groups': GROUPS, 'items': PRESETS}


# ── Site pack override (P3, 2026-10-21): config/sites/<SITE>/presets.yml ──
def _apply_pack_presets() -> None:
    try:
        from app.secretary import site_pack
        cfg = ((site_pack.load(strict=False) or {}).get("presets") or {}).get("presets") or {}
    except Exception:                              # noqa: BLE001
        return
    groups = cfg.get("GROUPS")
    if groups:
        globals()["GROUPS"] = [tuple(g) if isinstance(g, list) else g for g in groups]
    items = cfg.get("PRESETS")
    if items:
        globals()["PRESETS"] = list(items)


_apply_pack_presets()
