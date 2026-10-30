# -*- coding: utf-8 -*-
"""提示词装配验收（2026-10-30 重构后，**不依赖站点包**）。

钉死的事情
----------
1. 装配分层：S0 红线 / S1 谁说了算 / L1 身份 / L2 模式 / S4 作答格式 / S6 规则目录；
   **没有 L3 静态业务口径**（规则只在知识库，按需 get_rule）。
2. 代码里的提示词正文（`prompts_data`）与项目事实（`project`）就位。
3. 判模式、规则默认值、词表、版式默认值、预设问题、报告默认所都还能取到。
4. 通用模块里不许出现单位名（`agent_v2.py` / `modes.py` / `prompt_stack.py`）。

运行：py -X utf8 server/tests/agent/test_prompt_stack.py
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SERVER))

_checks: list[tuple[str, bool, str]] = []


def chk(label: str, cond: bool, extra: str = '') -> None:
    _checks.append((label, bool(cond), extra))


def main() -> int:
    from app.secretary import (agent, agent_v2, docgen, lexicon_defaults, modes,
                               presets, project, prompt_stack, prompts_data, report,
                               rules_defaults)

    print('提示词装配验收（无站点包）')

    # ── 1 分层：general 与 data 的差异 ───────────────────────
    g = prompt_stack.build(mode='general', answer_format='作答格式X')
    d = prompt_stack.build(mode='data', answer_format='作答格式X',
                           rules_menu='- 主题A ｜ 说明', priority='优先级X')
    g_ids, d_ids = prompt_stack.layer_ids(g), prompt_stack.layer_ids(d)
    chk('general 层：S0/L1/L2/S4', g_ids == ['S0', 'L1', 'L2', 'S4'], str(g_ids))
    chk('data 层：多出 S1 与 S6', d_ids == ['S0', 'S1', 'L1', 'L2', 'S4', 'S6'], str(d_ids))
    chk('任何模式都没有 L3 静态口径', 'L3' not in g_ids and 'L3' not in d_ids)
    chk('S1 文本进了 system', '优先级X' in d['system'])
    chk('S6 规则目录进了 system', '主题A' in d['system'])
    chk('S4 作答格式进了 system', '作答格式X' in d['system'])

    # ── 2 代码里的提示词正文 ────────────────────────────────
    chk('四种模式提示词齐', set(prompts_data.MODE_TEXTS) >= {'data', 'doc', 'general', 'office'},
        str(list(prompts_data.MODE_TEXTS)))
    chk('每份模式提示词非空',
        all(len((t or '').strip()) > 100 for t in prompts_data.MODE_TEXTS.values()))
    chk('身份正文非空且是"助手"', '助手' in prompts_data.IDENTITY, prompts_data.IDENTITY[:40])
    chk('作答要求非空', len(prompts_data.ANSWER_DATA) > 100)
    chk('版式默认值 docx.body_pt = 16', prompts_data.DOC_DEFAULTS['docx']['body_pt'] == 16,
        str(prompts_data.DOC_DEFAULTS.get('docx', {}).get('body_pt')))
    chk('判模式关键词非空', bool(prompts_data.MODE_RULES))
    chk('预设问题 12 条', len(prompts_data.PRESET_ITEMS) == 12,
        str(len(prompts_data.PRESET_ITEMS)))

    # ── 3 判模式 ───────────────────────────────────────────
    cases = [
        ('两水所上个月的工单有多少单', 'data'),
        ('帮我把这份材料写成汇报', 'general'),
        ('什么叫线损', 'general'),
        ('请假流程是谁审批的', 'data'),
        ('做个PPT汇报', 'office'),
    ]
    for q, want in cases:
        got = modes.judge(q)['mode']
        chk('判模式「%s」→ %s' % (q[:12], want), got == want, got)

    # ── 4 项目事实 ─────────────────────────────────────────
    dep = project.department()
    chk('部门锚点有 city_dept_id', bool(dep.get('city_dept_id')), str(dep.get('city_dept_id')))
    cds = dep.get('county_depts') or []
    chk('县公司 4 条', len(cds) == 4, str(len(cds)))
    chk('县公司字段齐', all({'key', 'dept_id', 'role', 'label'} <= set(c) for c in cds),
        str(cds[:1]))
    chk('tool_budget > 0', project.TOOL_BUDGET > 0, str(project.TOOL_BUDGET))
    chk('默认供电所非空', bool(project.DEFAULT_STATION), project.DEFAULT_STATION)
    jobs = (project.JOBS or {}).get('jobs') or []
    chk('定时任务有 dispatch_tick', any(j.get('fn') == 'dispatch_tick' for j in jobs),
        str([j.get('fn') for j in jobs]))

    # ── 5 规则默认值 / 词表 ────────────────────────────────
    alerts = rules_defaults.get('alerts')
    dispatch = rules_defaults.get('dispatch')
    chk('预警默认值非空', bool(alerts), str(list(alerts)[:3]))
    chk('派单默认值非空', bool(dispatch), str(list(dispatch)[:3]))
    chk('不存在的 key 返回空', rules_defaults.get('nope') == {})
    terms = (lexicon_defaults.BUSINESS_TERMS or {}).get('business_terms') or {}
    for key in ('station_hint', 'org_prefixes', 'strip_head'):
        chk('词表含 %s' % key, bool(terms.get(key)), str(terms.get(key))[:40])

    # ── 6 其它模块仍然能取到默认值 ──────────────────────────
    chk('docgen 版式默认值可读', float(docgen._dd('docx').get('body_pt') or 0) == 16,
        str(docgen._dd('docx').get('body_pt')))
    chk('预设问题接口 12 条', len(presets.current()['items']) == 12,
        str(len(presets.current()['items'])))
    chk('报告身份非空', bool(report._l1_identity()), report._l1_identity()[:30])
    chk('报告默认所非空', bool(report._default_station()), report._default_station())

    # ── 7 守卫：通用模块不许写死单位名 ──────────────────────
    for name in ('agent_v2.py', 'modes.py', 'prompt_stack.py'):
        src = io.open(SERVER / 'app' / 'secretary' / name, encoding='utf-8').read()
        chk('%s 不含单位名' % name, '随州' not in src)

    # ── 8 装配器可用（不抛异常）────────────────────────────
    sys_text = agent_v2.system('data', agent._system, '2026-10-30',
                               priority=agent.RULES_PRIORITY,
                               answer_format=(agent.COMMON + agent.CHECK))
    chk('system 装配成功且非空', len(sys_text) > 1000, str(len(sys_text)))
    chk('system 含 S1', 'S1 谁说了算' in sys_text)
    chk('system 含表清单', 'describe_table' in sys_text)
    chk('system 不含全量字段样例', '（例:' not in sys_text)
    chk('system 长度 < 15000', len(sys_text) < 15000, str(len(sys_text)))

    bad = [c for c in _checks if not c[1]]
    for label, ok, extra in _checks:
        print(('  [OK]   ' if ok else '  [FAIL] ') + label
              + (('   ← ' + extra) if (not ok and extra) else ''))
    print('\n%d 项检查，%d 项失败' % (len(_checks), len(bad)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
