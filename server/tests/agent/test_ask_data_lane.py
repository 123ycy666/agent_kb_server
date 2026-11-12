# -*- coding: utf-8 -*-
"""两个入口的接线验收（2026-11-12）：`/api/ask` 分线，`/api/ask/data` 只问数。

用假模型把 `agent.ask()` 真跑一遍（不连库、不连模型），验的是：
  · force_data=True  —— 不调 router、不调 preprocess.route、不进 agent_office；
                        工具只有查数那几只；预处理（纠错 / 补问法 / 问题确定）照跑。
  · force_data=False —— 分线与办公线照旧生效。
  · 接口已挂上，且两处调用都把 force_data 传进内核。

运行：py -X utf8 server/tests/agent/test_ask_data_lane.py
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SERVER))

import app.secretary as _pkg                                  # noqa: E402


def _install(name: str, **attrs) -> types.ModuleType:
    m = types.ModuleType('app.secretary.' + name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules['app.secretary.' + name] = m
    setattr(_pkg, name, m)
    return m


_install('prompt_material',
         MARK='\n\n===== 规则材料（本阶段） =====\n',
         SLOTS=('fetch', 'judge', 'answer', 'check'),
         block=lambda slots=None, **kw: '【本阶段规则材料替身】',
         strip_block=lambda s: s,
         sections=lambda *a, **k: [],
         replace_slot=lambda *a, **k: None,
         add_section=lambda *a, **k: 1,
         delete_section=lambda *a, **k: None,
         index_text=lambda *a, **k: '')
_install('prompt_text', get=lambda k, d=None: d, mode_texts=lambda d: d,
         keywords=lambda: {}, all_texts=lambda: {}, TEXT_KEYS=())
_install('qa_log', load_context=lambda *a, **k: [], count_turns=lambda *a, **k: 0,
         save=lambda result, **k: {'session_id': 's_test', 'qa_id': 1})
_install('session_summary', get=lambda *a, **k: {'summary': '', 'upto_seq': 0},
         maybe_enqueue=lambda *a, **k: False)
_install('rule_doc', topics=lambda: [], menu=lambda *a, **k: '', get=lambda *a, **k: '')

import app.secretary.agent as agent                            # noqa: E402
import app.secretary.agent_office as office_mod                 # noqa: E402
import app.secretary.preprocess as _pre_mod                     # noqa: E402
import app.secretary.router as _router_mod                      # noqa: E402

_checks: list[tuple[str, bool, str]] = []


def chk(label: str, cond, extra: str = '') -> None:
    _checks.append((label, bool(cond), extra))


def _fake_fix(q):
    return {'used': False, 'text': q, 'word_fixes': [], 'name_fixes': [], 'names': [],
            'areas': [], 'hint': '', 'unresolved': [], 'ms': 0}


def _fake_exec(calls):
    return [{'name': (c.get('function') or {}).get('name'), 'args': {}, 'ms': 1,
             'result': {'ok': True, 'rows': [{'n': 1}]}} for c in calls]


class FakeModel:
    def __init__(self, script):
        self.script = list(script)

    def __call__(self, messages, model, use_tools=True, max_tokens=None,
                 think=True, tool_choice=None, tools=None):
        item = self.script.pop(0) if self.script else {'content': '（脚本用完了）', 'calls': []}
        return {'choices': [{'message': {'content': item.get('content', ''),
                                         'tool_calls': item.get('calls') or []}}]}


def run(question, script, **kw):
    agent._chat = FakeModel(script)
    return agent.ask(question, model='fake-model', session_id=None, user_id=None, **kw)


def kinds(res):
    return [t.get('kind') for t in res['trace']]


def trace_of(res, kind):
    return [t for t in res['trace'] if t.get('kind') == kind]


DB_TOOLS = {'kb_search', 'list_metrics', 'metric_query', 'run_sql', 'read_result', 'finish'}


def main() -> int:
    agent.name_fix.fix = _fake_fix
    agent._exec_calls = _fake_exec
    agent.gaps.is_gap = lambda *a, **k: False
    agent.gaps.record = lambda *a, **k: None
    agent.semantic.codes_menu = lambda: ''
    agent.semantic.table_list_menu = lambda: 't_budget（替身）'
    agent.config.PAGE_ON = False
    _pre_mod.complete = lambda q: {'changed': False, 'hits': [], 'text': q}

    # 三条要盯的线换成记录器（真实现分别要调模型 / 查库 / 出文件）
    seen = {'router': 0, 'pre': 0, 'office': 0}

    def fake_router(*a, **k):
        seen['router'] += 1
        return {'lane': 'office', 'product': 'docx', 'howto': '', 'need': [], 'why': '替身：办公线'}

    def fake_pre_route(*a, **k):
        seen['pre'] += 1
        return {'use_db': False, 'use_file': True, 'make_file': True, 'reason': '替身'}

    def fake_office(*a, **k):
        seen['office'] += 1
        return {'answer': '走了办公线', 'lane': 'office'}

    _router_mod.route = fake_router
    _pre_mod.route = fake_pre_route
    office_mod.ask_office = fake_office

    # ── 场景 1：只问数 —— 连"要一份 Word"的句子也照样只问数
    seen.update(router=0, pre=0, office=0)
    res = run('帮我写个 word 通知', [{'content': ''}, {'content': '全市预算 123 万元。'}],
              force_data=True)
    chk('场景1 不调 router 分线', seen['router'] == 0, str(seen))
    chk('场景1 不调 preprocess 三个是/否', seen['pre'] == 0, str(seen))
    chk('场景1 不进办公线', seen['office'] == 0, str(seen))
    route_step = trace_of(res, 'route')
    chk('场景1 轨迹留痕 route：forced=True / lane=ask_db',
        bool(route_step) and (route_step[0].get('result') or {}).get('forced') is True,
        str(route_step)[:160])
    tools_step = (trace_of(res, 'tools') or [{}])[0].get('result') or {}
    chk('场景1 工具集只有查数那几只', set(tools_step.get('tools') or ()) == DB_TOOLS,
        str(tools_step))
    chk('场景1 不给 read_file / make_file / run_code',
        not ({'read_file', 'make_file', 'run_code'} & set(tools_step.get('tools') or [])),
        str(tools_step.get('tools')))
    k = kinds(res)
    chk('场景1 预处理没被砍（namefix / intent / confirm 都在）',
        all(x in k for x in ('namefix', 'intent', 'confirm')), str(k))
    intent = (trace_of(res, 'intent') or [{}])[0].get('result') or {}
    chk('场景1 intent 查库=是、不出文件',
        intent.get('use_db') is True and intent.get('make_file') is False, str(intent))
    chk('场景1 有正文', '123' in (res.get('answer') or ''), repr(res.get('answer'))[:60])

    # ── 场景 2：智能体分支 —— 分线照旧，判到办公线就进办公线
    seen.update(router=0, pre=0, office=0)
    res2 = run('帮我写个 word 通知', [{'content': ''}])
    chk('场景2 调了 router 分线', seen['router'] == 1, str(seen))
    chk('场景2 判到办公线就进办公线', seen['office'] == 1, str(seen))
    chk('场景2 办公线的回答原样返回', res2.get('answer') == '走了办公线',
        repr(res2.get('answer'))[:60])

    # ── 场景 3：接口层接线
    from app.api.v1 import agent as api_agent                   # noqa: E402
    routes = set()
    for r in api_agent.router.routes:
        for m in (getattr(r, 'methods', None) or ()):
            routes.add((getattr(r, 'path', ''), m))
    chk('场景3 POST /ask 已注册', ('/ask', 'POST') in routes, str(sorted(routes))[:200])
    chk('场景3 POST /ask/data 已注册', ('/ask/data', 'POST') in routes, str(sorted(routes))[:200])
    src = (SERVER / 'app' / 'api' / 'v1' / 'agent.py').read_text(encoding='utf-8')
    chk('场景3 两处调用都把 force_data 传进内核（stream + 线程池）',
        src.count('force_data=force_data') == 2, str(src.count('force_data=force_data')))
    chk('场景3 只问数接口按 True 调', 'force_data=True' in src, '')

    bad = [c for c in _checks if not c[1]]
    for label, ok, extra in _checks:
        print(('  [OK]   ' if ok else '  [FAIL] ') + label
              + (('   <- ' + extra) if (not ok and extra) else ''))
    print('\n%d 项检查，%d 项失败' % (len(_checks), len(bad)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
