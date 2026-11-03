# -*- coding: utf-8 -*-
"""循环接线验收：用**假模型**把 `agent.ask()` 整个循环跑完（2026-11-02）。

为什么要有这一层
----------------
`test_evidence_gate.py` 只验纯逻辑（decision 对不对）。内网出问题的地方是**接线**：
提醒那一轮到底给没给工具、会不会死循环、空正文怎么办、拦住之后回的是什么。
这些只有把 `ask()` 真跑一遍才看得见。所以这里把数据库、模型、工具执行全部换成替身，
drive 的就是线上那份循环代码本身。

不需要数据库、不需要模型端点。运行：py -X utf8 server/tests/agent/test_loop_gate.py
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SERVER))

import app.secretary as _pkg                                  # noqa: E402


def _install(name: str, **attrs) -> types.ModuleType:
    """把 app.secretary.<name> 换成一个替身模块（agent.py 里都是函数内 import，赶得上）。"""
    m = types.ModuleType('app.secretary.' + name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules['app.secretary.' + name] = m
    setattr(_pkg, name, m)
    return m


# 规则材料（真实现要查库）：换阶段 / 取本阶段材料都走替身
_install('prompt_material',
         MARK='\n\n===== 规则材料（本阶段） =====\n',
         # 2026-11-03：agent_v2 装配要读 SLOTS（主分支空壳下是 ()），替身也得给一个
         SLOTS=('fetch', 'judge', 'answer', 'check'),
         block=lambda slots=None, **kw: '【本阶段规则材料替身】',
         strip_block=lambda s: s,
         sections=lambda *a, **k: [],
         replace_slot=lambda *a, **k: None,
         add_section=lambda *a, **k: 1,
         delete_section=lambda *a, **k: None,
         index_text=lambda *a, **k: '')
# 页面可改的提示词正文（真实现要查库）
_install('prompt_text', get=lambda k, d=None: d, mode_texts=lambda d: d,
         keywords=lambda: {}, all_texts=lambda: {}, TEXT_KEYS=())
# 问答落库 / 多轮上下文（真实现要查库）
_install('qa_log', load_context=lambda *a, **k: [], count_turns=lambda *a, **k: 0,
         save=lambda result, **k: {'session_id': 's_test', 'qa_id': 1})
_install('session_summary', get=lambda *a, **k: {'summary': '', 'upto_seq': 0},
         maybe_enqueue=lambda *a, **k: False)
_install('rule_doc', topics=lambda: [], menu=lambda *a, **k: '', get=lambda *a, **k: '')

import app.secretary.agent as agent                            # noqa: E402
import app.secretary.preprocess as _pre_mod                    # noqa: E402
from app.secretary import evidence_gate as eg                  # noqa: E402

# 补全表要查库；粗闸（要不要动原话）保持真的，只把"查表补全"换掉
_pre_mod.complete = lambda q: {'changed': False, 'hits': [], 'text': q}

_checks: list[tuple[str, bool, str]] = []


def chk(label: str, cond: bool, extra: str = '') -> None:
    _checks.append((label, bool(cond), extra))


# ── 替身：名字归一 / 时间地点 / 码表 / 工具执行 ─────────────────────────
def _fake_fix(q):
    return {'used': False, 'text': q, 'word_fixes': [], 'name_fixes': [], 'names': [],
            'areas': [], 'hint': '', 'unresolved': [], 'ms': 0}


def _fake_describe(question, fixed=None, prev=None, today=None, force_place=None):
    return {'question': question,
            'time': {'matched': '', 'time_text': '2025年', 'period_type': 'year',
                     'period_key': '2025', 'is_default': True, 'note': '替身口径'},
            'place': '全市', 'place_default': True,
            'inherited': {'time': False, 'place': False},
            'scope_text': '期间：2025年（全年）\n范围：全市'}


def _fake_exec(calls):
    out = []
    for c in calls:
        out.append({'name': (c.get('function') or {}).get('name'),
                    'args': {}, 'ms': 1,
                    'result': {'ok': True, 'rows': [{'n': 1}]}})
    return out


class FakeModel:
    """按脚本回话，并记下每次调用「有没有给工具」。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    def __call__(self, messages, model, use_tools=True, max_tokens=4000,
                 think=True, tool_choice=None):
        self.calls.append({'use_tools': bool(use_tools), 'msgs': len(messages)})
        item = self.script.pop(0) if self.script else {'content': '（脚本用完了）', 'calls': []}
        return {'choices': [{'message': {'content': item.get('content', ''),
                                         'tool_calls': item.get('calls') or []}}]}


def _call(name='run_sql', cid='c1'):
    return [{'id': cid, 'type': 'function',
             'function': {'name': name, 'arguments': '{"sql":"select 1"}'}}]


def run(question, script, **kw):
    fm = FakeModel(script)
    agent._chat = fm
    res = agent.ask(question, model='fake-model', session_id=None, user_id=None, **kw)
    return res, fm


def kinds(res):
    return [t.get('kind') for t in res['trace']]


def main() -> int:
    print('循环接线验收（假模型，不连库不连模型）')

    # 替身装上（越早越好：都在问句处理之前调用）
    agent.name_fix.fix = _fake_fix
    agent.time_scope.describe = _fake_describe
    agent._exec_calls = _fake_exec
    agent.gaps.is_gap = lambda *a, **k: False
    agent.gaps.record = lambda *a, **k: None
    agent.semantic.codes_menu = lambda: ''
    agent.semantic.table_list_menu = lambda: 't_budget（替身）'
    agent.agent_v2.tool_context = lambda: '\n\n【可用数据表】t_budget（替身）'
    agent.config.PAGE_ON = False
    agent.config.NUMBER_GUARD_ON = False

    # ── 场景 1：模型直接给正文 → 一次收工，不催、不拦（2026-11-03 守卫全关）
    res, fm = run('2025年全市情况', [{'content': '2025年全市执行率105.64%。'}])
    chk('场景1 一次就收（不再催"先核实"）', len(fm.calls) == 1, str(len(fm.calls)))
    chk('场景1 没有 nudge', 'nudge' not in kinds(res), str(kinds(res)))
    chk('场景1 正文就是模型给的', '105.64' in res['answer'], res['answer'][:40])
    chk('场景1 没查库也放行（unverified=False）', res['unverified'] is False,
        str(res['unverified']))
    chk('场景1 闸门留痕恒空（守卫已关）', res['evidence_gate'] == [], str(res['evidence_gate']))

    # ── 场景 2：工具一律给 —— 不再按"词表判模式"开关工具
    for q in ('你好', '帮我写个通知', '全市情况怎么样'):
        res, fm = run(q, [{'content': '好的。'}])
        chk('场景2 工具一律给：%s' % q, fm.calls[0]['use_tools'] is True,
            str(fm.calls[0]['use_tools']))

    # ── 场景 3：模型查一次数、然后不再发工具 → 另起一次写正文
    res, fm = run('全市情况怎么样', [
        {'content': '', 'calls': _call()},
        {'content': ''},
        {'content': '查完之后的正文。'},
    ])
    chk('场景3 确实查了一次', res['db_query_count'] == 1, str(res['db_query_count']))
    chk('场景3 正文来自查完之后那次调用', res['answer'] == '查完之后的正文。',
        repr(res['answer']))

    # ── 场景 4：空正文 → 不许把空白交出去
    res, fm = run('全市情况怎么样', [
        {'content': ''},
        {'content': '补出来的正文。'},
    ])
    chk('场景4 空正文被补上了', res['answer'] == '补出来的正文。', repr(res['answer']))
    chk('场景4 补正文那次没给工具', fm.calls[-1]['use_tools'] is False, str(fm.calls))

    # ── 场景 5：模型自己调 finish 收尾 → 照旧另起一次写正文
    res, fm = run('全市情况怎么样', [
        {'content': '', 'calls': _call()},
        {'content': '', 'calls': _call('finish', 'f1')},
        {'content': '收尾后的正文。'},
    ])
    chk('场景5 finish 之后能拿到正文', res['answer'] == '收尾后的正文。', repr(res['answer']))
    chk('场景5 没被拦', res['unverified'] is False, str(res['unverified']))

    # ── 场景 6：写材料里的数字照样出得去（不再有"数字必须有出处"的提醒）
    res, fm = run('帮我写个通知', [{'content': '各单位要落实 3 项要求、覆盖 12 个班组。'}])
    chk('场景6 写材料里的数字不被拦', res['unverified'] is False, str(res['unverified']))
    chk('场景6 通知正文照常给出', '3 项要求' in res['answer'], repr(res['answer'][:30]))
    chk('场景6 没有提醒', 'nudge' not in kinds(res), str(kinds(res)))

    # ── 场景 7：八步的痕迹都在（第 1 步 intent、第 6 步 confirm 各一条）
    k = kinds(res)
    chk('场景7 trace 有 intent（第 1 步）', 'intent' in k, str(k))
    chk('场景7 trace 有 scope（第 5 步）', 'scope' in k, str(k))
    chk('场景7 trace 有 confirm（第 6 步）', 'confirm' in k, str(k))
    chk('场景7 trace 有 answer（第 8 步）', k[-1] == 'answer', str(k))

    # ── 场景 8：步数上限 = 30（只防死循环），到顶强制出正文
    chk('场景8 默认步数上限 30', agent.config.MAX_STEPS == 30, str(agent.config.MAX_STEPS))
    res, fm = run('全市情况怎么样', [
        {'content': '', 'calls': _call()},
        {'content': '', 'calls': _call()},
        {'content': '到顶之后的正文。'},
    ], max_steps=1)
    chk('场景8 到顶强制出正文', '到顶之后的正文。' in res['answer'], repr(res['answer']))
    chk('场景8 到顶有留痕', 'final' in kinds(res), str(kinds(res)))

    # ── 场景 9：极端输入（空串 / 单个标点 / 超长）不许崩，也不许空转
    for q in ('', '。', '全市' * 500):
        try:
            res, fm = run(q, [{'content': '请把问题说清楚一些。'}])
            ok, extra = True, '%s 次调用' % len(fm.calls)
        except Exception as exc:                        # noqa: BLE001
            ok, extra = False, '%s: %s' % (type(exc).__name__, str(exc)[:60])
        chk('场景9 极端输入不崩：%r' % (q[:8]), ok, extra)
        if ok:
            chk('场景9 极端输入有返回：%r' % (q[:8]), bool(res['answer']) or res['unverified'],
                repr(res['answer'][:20]))

    bad = [c for c in _checks if not c[1]]
    for label, ok, extra in _checks:
        print(('  [OK]   ' if ok else '  [FAIL] ') + label
              + (('   ← ' + extra) if (not ok and extra) else ''))
    print('\n%d 项检查，%d 项失败' % (len(_checks), len(bad)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
