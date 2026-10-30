# -*- coding: utf-8 -*-
"""V2 装配入口：判模式、按模式装提示词、按模式决定给不给工具。

为什么单独一个文件（2026-10-21）
------------------------------
`agent.py` 有 1500 行、主循环与守卫都在里面，直接往里插逻辑风险高。
所以 V2 的**决策**都放这里，`agent.py` 只留三处一行的调用：
    judge_mode()  → 这一问走哪个模式
    system()      → 该模式的 system 提示词（七层装配）
    use_tools()   → 这一轮给不给工具

三条回退保险（任何一条成立就按旧行为跑）
--------------------------------------
1. `SECRETARY_AGENT_V2=0`
2. 站点包装载失败 / 包不完整
3. 判模式抛异常 → 落 data

自检：py -X utf8 server/tests/agent/test_prompt_stack.py
"""
from __future__ import annotations

import os
import re
import time

_CACHE: dict = {}


def enabled() -> bool:
    return (os.environ.get('SECRETARY_AGENT_V2') or '1').strip() not in ('0', 'false', 'False')


def pack() -> dict:
    """站点包（缓存一次；读不到返回空 dict）。"""
    if 'p' not in _CACHE:
        try:
            from app.secretary import site_pack
            _CACHE['p'] = site_pack.load(strict=False)
        except Exception as exc:                   # noqa: BLE001
            print('[agent_v2] 站点包装载失败，回落旧装配：%s' % exc)
            _CACHE['p'] = {}
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
           answer_format: str | None = None, personal_rules: str | None = None) -> str:
    """该模式的 system 提示词。

    legacy 是 agent._system（旧装配），用于回退——签名 legacy(today)。
    priority / answer_format / personal_rules：分别对应 S1「谁说了算」、
    S4「作答格式」、S7「我的规则」，由调用方（agent.ask）传入，避免这里反向依赖 agent。
    """
    p = pack()
    if not enabled() or not p or not p.get('prompts'):
        return legacy(today)
    try:
        from app.secretary import prompt_stack, semantic, rule_doc
        # 规则文档（知识库 type=rule）一旦存在，就不再静态注入站点包的 L3：
        # 同一套规则只能有一个出处（2026-10-30）。没有规则文档时退回静态 L3，
        # 保证"还没导入规则"的环境行为和以前一致。
        rules_menu = ''
        try:
            rules_menu = rule_doc.menu()
        except Exception:                          # noqa: BLE001 - 读不到按无规则
            rules_menu = ''
        head = prompt_stack.build(p, mode=mode, persona=persona, prefs=prefs,
                                  priority=priority,
                                  answer_format=answer_format,
                                  rules_menu=rules_menu or None,
                                  skip_business=bool(rules_menu))['system']
        if personal_rules:
            head += '\n\n' + personal_rules
        head += ('\n\n今天的日期是 %s。所有「停留天数 / 截至今天」一律按这一天算，'
                 '不要拿数据里的时间当今天。' % (today or time.strftime('%Y-%m-%d')))
        # 通用模式**不塞**业务码表与数据表目录：那是查数专用上下文，
        # 塞进去会把「写个工作总结」拽回业务轨道 —— 这正是要解开的锁。
        if mode in ('data', 'doc'):
            try:
                codes = semantic.codes_menu()
                if codes:
                    head += '\n\n' + codes
            except Exception:                      # noqa: BLE001
                pass
            try:
                # ★ 2026-10-30：常驻只给「表名 + 中文名 + 一句话」（约 1.5K）。
                #   字段明细走 describe_table 工具按需取（原 table_menu 26 张表 = 22.6K，
                #   占 system 的 72%；A/B 实测只给清单时选表全对）。
                head += ('\n\n【可用数据表（只能查这些，字典外的表一律不可用）】\n'
                         '字段明细请用 describe_table(表名) 现取，不要凭印象写字段。\n'
                         + semantic.table_list_menu())
            except Exception:                      # noqa: BLE001
                pass
        return head
    except Exception as exc:                       # noqa: BLE001
        print('[agent_v2] V2 装配失败，回落旧装配：%s' % exc)
        return legacy(today)


def use_tools(mode: str) -> bool:
    """通用模式不给工具（它不需要查库，给了只会去查）；其余模式照旧。"""
    return mode != 'general'


# ── 指标口径预热（2026-10-21）────────────────────────────────────────
# 实测：同一问题模型每轮自己写 SQL，执行率会漂（22.79% vs 0.00%）。
# 提示词"必须用某工具"只是高概率；这里改成**代码先按固定口径算好再交给模型**，
# 模型只负责措辞，数字不由它决定。
_METRIC_HINTS = ('执行率', '预算执行', '预算花', '还剩多少', '花了多少', '预算用了')
_STATION_RE = re.compile(r'([\u4e00-\u9fa5]{2,8}(?:供电所|供电服务站|县供电公司|市供电公司))')
_CACHE: dict = {}


def prefetch_metric(question: str, mode: str = 'data') -> str:
    """命中核心指标时，按固定口径先查好，返回给模型看的 markdown；不命中返回空串。"""
    if mode != 'data':
        return ''
    q = question or ''
    if not any(h in q for h in _METRIC_HINTS):
        return ''
    m = _STATION_RE.search(q)
    if not m:
        return ''
    station = m.group(1)
    key = ('budget_execution', station)
    hit = _CACHE.get(key)
    if hit and (time.time() - hit[0]) < 60:
        return hit[1]
    try:
        from app.secretary import metrics
        res = metrics.handle({'station': station})
    except Exception as exc:                       # noqa: BLE001  预取失败不影响正常问答
        print('[agent_v2] 指标预热失败（不影响回答）：%s' % str(exc)[:120])
        return ''
    md = res.get('markdown') or ''
    if md:
        _CACHE[key] = (time.time(), md)
    return md

