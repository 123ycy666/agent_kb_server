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
    # ★ 2026-11-03：S0 红线不再写死在代码里 —— 传 redlines 才有，不传就没有（空壳）
    g = prompt_stack.build(mode='general', answer_format='作答格式X')
    d = prompt_stack.build(mode='data', answer_format='作答格式X',
                           rules_menu='- 主题A ｜ 说明', priority='优先级X')
    z = prompt_stack.build(mode='data', redlines='红线X')
    g_ids, d_ids = prompt_stack.layer_ids(g), prompt_stack.layer_ids(d)
    chk('general 层（没传红线）：L1/L2/S4', g_ids == ['L1', 'L2', 'S4'], str(g_ids))
    # ★ 2026-11-03 定稿：模式做法里业务三段（data/doc/office）已清空 →
    #   mode='data' 时 L2 不再出现（空就不注入，这是"我填什么就是什么"的直接后果）。
    chk('data 层：业务做法清空后没有 L2', d_ids == ['S1', 'L1', 'S4', 'S6'], str(d_ids))
    chk('传了红线才有 S0', prompt_stack.layer_ids(z)[:1] == ['S0'], str(prompt_stack.layer_ids(z)))
    chk('不传就没有装配说明', '装配说明' not in g['system'])
    chk('不传就没有 S0', 'S0' not in g['system'])
    chk('任何模式都没有 L3 静态口径', 'L3' not in g_ids and 'L3' not in d_ids)
    chk('S1 文本进了 system', '优先级X' in d['system'])
    chk('S6 规则目录进了 system', '主题A' in d['system'])
    chk('S4 作答格式进了 system', '作答格式X' in d['system'])

    # ── 2 代码里的提示词正文 ────────────────────────────────
    chk('四种模式提示词键齐', set(prompts_data.MODE_TEXTS) >= {'data', 'doc', 'general', 'office'},
        str(list(prompts_data.MODE_TEXTS)))
    # ★ 2026-11-03 定稿：只剩 general（基础流程：正常问答 / 正常聊天），业务三段清空
    chk('业务三段（data/doc/office）已清空',
        all(not (prompts_data.MODE_TEXTS.get(k) or '').strip()
            for k in ('data', 'doc', 'office')),
        {k: len(prompts_data.MODE_TEXTS.get(k) or '') for k in prompts_data.MODE_TEXTS})
    chk('general 是基础流程且非空',
        len((prompts_data.MODE_TEXTS.get('general') or '').strip()) > 100,
        len((prompts_data.MODE_TEXTS.get('general') or '')))
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
    sys_text = agent_v2.system('general', agent._system, '2026-10-30',

                               # 2026-11-03：COMMON/CHECK 已下线，S4 用替身正文验装配
                               answer_format='作答格式替身')
    chk('system 装配成功且非空', len(sys_text) > 300, str(len(sys_text)))
    # ★ 2026-11-03：S0 红线 / S1 优先级常量已删（改成页面 key，默认空）
    chk('system 不含 S1（空壳）', 'S1 谁说了算' not in sys_text)
    chk('system 不含 S0（空壳）', 'S0 诚信与安全红线' not in sys_text)
    chk('system 不含装配说明', '以下是内容按优先级' not in sys_text and 'S0 红线 >' not in sys_text)
    chk('system 不含硬编码日期句', '今天的日期是' not in sys_text)
    chk('system 仍含身份（保留）', '随州' in sys_text)
    # ★ 2026-11-03 定稿：数据表目录由 tool_context() 提供，且**任何模式都给**
    #   （工具一律给）。库连不上时它是空的，所以这里验"装配里确实调了它"。
    _src_v2 = io.open(SERVER / 'app' / 'secretary' / 'agent_v2.py', encoding='utf-8').read()
    chk('system 恒带数据表目录（不再按模式开关）', 'head += tool_context()' in _src_v2)
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
