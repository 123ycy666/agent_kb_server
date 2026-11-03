# -*- coding: utf-8 -*-
"""非必带规则「取得到」验收（2026-11-02，不需要数据库、不需要模型）。

钉死的事情
----------
1. **代号解析**：`20.2 xxx`→`20.2`、`六、xxx`→`六`、`十九、xxx`→`十九`，无代号→''。
2. **清单给代号不给长标题**：注入块末尾的「没带上的规则」每一行都是 `20.2 ｜ 短名`。
3. **清单跨四份**：查数阶段的注入块里也能看到 answer 的题型代号（原来只有收尾才有，
   而收尾时工具已关 —— 那 19 条从来没被读过）。
4. **get_rule 四级匹配**：代号 → 全名 → 子串 → 打分；都不中给 5 条候选代号。
5. **收尾阶段只开放 get_rule**：物理上不能借机查数；
   模型取一次规则后要能出正文；只给一轮，不死循环。

运行：py -X utf8 server/tests/agent/test_rule_take.py
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SERVER))

# 提示词覆盖 / 码表都走替身，避免连库（agent_v2 装配时会用到）
for _n, _attrs in (('prompt_text', {'get': lambda k, d=None: d,
                                    'mode_texts': lambda d: d,
                                    'keywords': lambda: {},
                                    'all_texts': lambda: [],
                                    'TEXT_KEYS': ()}),):
    _m = types.ModuleType('app.secretary.' + _n)
    for _k, _v in _attrs.items():
        setattr(_m, _k, _v)
    sys.modules['app.secretary.' + _n] = _m

from app.secretary import prompt_material as pm      # noqa: E402
from app.secretary import rule_doc                   # noqa: E402

# ★ 2026-11-03：主分支空壳把 pm.SLOTS 置空（不注入规则材料）。本文件验的是
#   「清单给代号 / 按代号取原文」这套机制，本地把四份开回来再测。
pm.SLOTS = ('fetch', 'judge', 'answer', 'check')

_checks: list[tuple[str, bool, str]] = []


def chk(label: str, cond: bool, extra: str = '') -> None:
    _checks.append((label, bool(cond), str(extra)[:200]))


S20_2 = '20.2 审批待办 / 审批积压类（「我这儿多少待审批」「哪些卡住了」「先办哪几张」）'
S20_6 = '20.6 金额合理性（离群）类（「哪些申请金额明显不合理」「这单金额合理吗」）'
S20_11 = '20.11 预测 / 外推类（「投完指标能到啥样」「年底能到多少」「能不能达标」）'
S8 = '八、审批停留'
S19 = '十九、三条缺省约定（问题里没说的，一律按这三条补）'
S22_4 = '22.4 精炼（Fred 明确要求）'

# 让 sections() 返回假数据：四份、必带与非必带都有
_FAKE = {
    'fetch': [('一、前置：所有统计统一执行', '前置正文', True),
              (S19, '缺省约定正文', False)],
    'judge': [(S8, '审批停留正文', True)],
    'answer': [(S20_2, '审批待办正文：先按停留最久、金额最大排序', False),
               (S20_6, '金额离群正文：偏离同类均值 3 倍以上才算', False),
               (S20_11, '预测正文：按当前速度外推 + 缺口量化', False),
               (S22_4, '精炼正文', True)],
    'check': [('十四、自查清单', '清单正文', True)],
}


def _install_fake():
    def sections(slot):
        slot = (slot or '').strip()
        rows = _FAKE.get(slot) or []
        return [{'id': i + 1, 'slot': slot, 'section': sec, 'body': body,
                 'sort_no': i, 'version_no': 1, 'pinned': pin}
                for i, (sec, body, pin) in enumerate(rows)]
    pm.sections = sections
    pm.clear_cache = lambda: None


class FakeModel:
    """按脚本回话，记下每次给的工具表。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    def __call__(self, messages, model, use_tools=True, max_tokens=4000,
                 think=True, tool_choice=None, tools=None):
        self.calls.append({'use_tools': bool(use_tools),
                           'tools': [((t.get('function') or {}).get('name'))
                                     for t in (tools or [])]})
        item = self.script.pop(0) if self.script else {'content': '（脚本用完了）'}
        return {'choices': [{'message': {'content': item.get('content', ''),
                                         'tool_calls': item.get('calls') or []}}]}


def _call(name, args, cid='c1'):
    return [{'id': cid, 'type': 'function',
             'function': {'name': name, 'arguments': json.dumps(args, ensure_ascii=False)}}]


def main() -> int:
    print('非必带规则「取得到」验收（不连库）')
    _install_fake()

    # ── 1 代号解析
    cases = [(S20_2, '20.2'), (S20_11, '20.11'), (S8, '八'), (S19, '十九'),
             (S22_4, '22.4'), ('0.6 拿到问题先拆（单点 / 多点）', '0.6'),
             ('没有代号的一节', ''), ('', ''), ('   ', '')]
    for sec, want in cases:
        got = pm.code_of(sec)
        chk('代号解析「%s」→ %r' % (sec[:14], want), got == want, got)

    # ── 2 短名 / 标签
    chk('短名去掉括号举例', pm.short_name(S20_2) == '审批待办 / 审批积压类', pm.short_name(S20_2))
    chk('短名去掉代号', not pm.short_name(S8).startswith('八'), pm.short_name(S8))
    chk('标签形如「代号 ｜ 短名」', pm.labeled(S20_6).startswith('20.6 ｜ '), pm.labeled(S20_6))

    # ── 3 清单：给代号、不给长标题、跨四份
    idx = pm.index_text(pm.SLOTS)
    chk('清单含 answer 的代号 20.2', '20.2 ｜' in idx, idx[:80])
    chk('清单含 fetch 的代号 十九', '十九 ｜' in idx, idx[:120])
    chk('清单不含 40 字长标题（括号举例被去掉）',
         '「我这儿多少待审批」' not in idx, idx[:80])
    nop = sum(1 for s in pm.SLOTS for r in _FAKE[s] if not r[2])
    listed = len([ln for ln in idx.splitlines() if ln.startswith('- ')])
    chk('清单条数 = 非必带条数', listed == nop, '%d vs %d' % (listed, nop))
    chk('清单够短（< 500 字）', len(idx) < 500, len(idx))
    blk = pm.block(('fetch', 'judge'), index_slots=pm.SLOTS)
    chk('查数阶段的注入块里就有 answer 的代号（原来只有收尾才有）', '20.2 ｜' in blk)
    chk('查数阶段只带 fetch/judge 的必带正文，不夹 answer 正文',
         '审批待办正文' not in blk, blk[:100])

    # ── 4 get_rule 四级匹配
    r = rule_doc.get('20.2')
    chk('按代号取到 20.2', r.get('text') == '审批待办正文：先按停留最久、金额最大排序'
        and r.get('matched_by') == 'code', r)
    r = rule_doc.get(S20_6)
    chk('按全名取到 20.6', r.get('matched_by') == 'exact' and '金额离群正文' in (r.get('text') or ''), r)
    r = rule_doc.get('审批待办')
    chk('按短名子串取到 20.2', '审批待办正文' in (r.get('text') or '')
        and r.get('matched_by') == 'substr', r)
    r = rule_doc.get('哪些卡住了')
    chk('按标题举例里的问法也能取到', '审批待办正文' in (r.get('text') or ''), r)
    r = rule_doc.get('年底大概能到多少')
    chk('按改写过的说法（打分兜底）取到 20.11',
        '预测正文' in (r.get('text') or ''), r)
    r = rule_doc.get('完全不相干的一句废话')
    chk('取不到时明确报没有这条', 'error' in r, r.get('error'))
    chk('取不到时给 5 条候选且带代号',
        len(r.get('candidates') or []) == 5
        and all(c.get('code') for c in r['candidates']), r.get('candidates'))
    chk('候选里排第一的就是该用的那条（瞎编的说法也指向 20.x）',
        (r.get('candidates') or [{}])[0].get('code') in
        ('20.2', '20.6', '20.11', '22.4', '八', '十九', '一', '十四'),
        r.get('candidates'))
    r = rule_doc.get('')
    chk('空 topic 给出用法提示', 'error' in r and 'candidates' in r, r)

    # ── 5 收尾阶段：只开放 get_rule，取一轮就出正文
    import app.secretary.agent as agent
    fm = FakeModel([
        {'content': '', 'calls': _call('get_rule', {'topic': '20.2'})},
        {'content': '取到规则之后的正文'},
    ])
    agent._chat = fm
    trace: list = []
    out = agent._final_answer([{'role': 'user', 'content': '哪些卡住了'}], 'fake',
                              allow_rule=True, trace=trace)
    chk('收尾：拿到正文', out == '取到规则之后的正文', repr(out))
    chk('收尾：第一轮只开放 get_rule（没有 run_sql）',
        fm.calls[0]['tools'] == ['get_rule'], fm.calls[0])
    chk('收尾：第二轮关掉工具写正文', fm.calls[1]['use_tools'] is False, fm.calls[1])
    chk('收尾：trace 记下这次取用',
        any(t.get('tool') == 'get_rule' and (t.get('result') or {}).get('ok') for t in trace),
        trace)
    chk('收尾：取到的是 20.2', (trace[0].get('result') or {}).get('code') == '20.2', trace[0])

    # 不取规则也照常出正文
    fm = FakeModel([{'content': '', 'calls': []}, {'content': '直接写的正文'}])
    agent._chat = fm
    out = agent._final_answer([{'role': 'user', 'content': 'x'}], 'fake', allow_rule=True)
    chk('收尾：模型不取规则时一次出正文', out == '直接写的正文', repr(out))

    # 连调两次也不会死循环（只给一轮）
    fm = FakeModel([{'content': '', 'calls': _call('get_rule', {'topic': '20.6'})},
                    {'content': '正文'}])
    agent._chat = fm
    out = agent._final_answer([{'role': 'user', 'content': 'x'}], 'fake', allow_rule=True)
    chk('收尾：最多一轮工具，之后强制写正文', len(fm.calls) == 2 and out == '正文',
        '%d 次调用' % len(fm.calls))

    # 关掉 allow_rule 时完全不给工具（兼容旧调用）
    fm = FakeModel([{'content': '旧路径正文'}])
    agent._chat = fm
    out = agent._final_answer([{'role': 'user', 'content': 'x'}], 'fake')
    chk('收尾：allow_rule=False 时一次都不调工具',
        len(fm.calls) == 1 and fm.calls[0]['use_tools'] is False and out == '旧路径正文',
        fm.calls)

    # ── 6 接线：循环阶段清单跨四份、收尾传了 allow_rule
    src = (SERVER / 'app' / 'secretary' / 'agent_v2.py').read_text(encoding='utf-8')
    chk('agent_v2 装配时清单按四份算', 'index_slots=prompt_material.SLOTS' in src)
    asrc = (SERVER / 'app' / 'secretary' / 'agent.py').read_text(encoding='utf-8')
    chk('agent 收尾调用启用了 allow_rule',
        asrc.count('allow_rule=True') >= 5, asrc.count('allow_rule=True'))
    chk('_chat 支持工具子集', 'tools=None' in asrc and "body['tools'] = tools if tools else TOOLS" in asrc)

    bad = [c for c in _checks if not c[1]]
    for label, ok, extra in _checks:
        print(('  [OK]   ' if ok else '  [FAIL] ') + label
              + (('   ← ' + extra) if (not ok and extra) else ''))
    print('\n%d 项检查，%d 项失败' % (len(_checks), len(bad)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
