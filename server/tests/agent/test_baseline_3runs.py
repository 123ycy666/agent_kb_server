# -*- coding: utf-8 -*-
"""基线：同一道数据题跑 3 次，记四个数（2026-11-05 工具化改造对照用）。

跑法：py -X utf8 server/tests/agent/test_baseline_3runs.py [--runs=3] [--q=...]
产出：output/2026-11-05-基线-3次.json

记的数（第七节验收判据）：
    轮数      = 主循环轮数（trace 里最大的 round）+ 模型调用总数
    总耗时    = ask() 墙钟
    回答字数  = 非空白字符数
    数字对账  = 回答里 >=10 万元的金额，能否在本轮 SQL 结果（行值 / summary / 两两和差）里找到
    格式合规  = 由页面「作答规则」原文逐条转成的可正则判分约束（见 RULES）

只读取。不改 agent 一行；_chat 包一层记时记提示词字数。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'server'))
os.chdir(ROOT / 'server')

from app.core.deps import CurrentActor                      # noqa: E402
from app.secretary import agent, kb_adapter                   # noqa: E402

OUT = ROOT / 'output'
UID = 1
QUESTION = '今年预算执行得怎么样？'
CALLS: list = []


def install_timer() -> None:
    orig = agent._chat

    def timed(msgs, model, **kw):
        t0 = time.time()
        sys_txt = str((msgs[0] or {}).get('content') or '') if msgs else ''
        r = orig(msgs, model, **kw)
        out = ''
        try:
            out = str(r['choices'][0]['message'].get('content') or '')
        except Exception:                                  # noqa: BLE001
            pass
        CALLS.append({'ms': int((time.time() - t0) * 1000),
                      'use_tools': bool(kw.get('use_tools')),
                      'max_tokens': kw.get('max_tokens'),
                      'system_chars': len(sys_txt),
                      'has_caliber': '===== 规则材料' in sys_txt,
                      'prompt_chars': sum(len(str(m.get('content') or '')) for m in msgs),
                      'out_chars': len(out)})
        return r

    agent._chat = timed


# ── 格式合规：页面「作答规则」原文（一、回答规则）逐条转成判分 ──────────
# 原文要点：纯文本、一两句话精简、不分条（问情况不要编号）、
#           不出现 ** # | > 反引号、不出现过程话术、不提 SQL/表名/字段/数据库。
_PROCS = [
    (r'我已经?(?:获得|拿到|掌握)', '过程话术「我已经获得」'),
    (r'让我(?:来)?(?:组织|整理|梳理|汇总|回答)', '过程话术「让我…」'),
    (r'接下来我', '过程话术「接下来我」'),
    (r'根据(?:查询|检索|SQL)结果', '过程话术「根据查询结果」'),
    (r'\bI have\b|\bLet me\b|\bNow I\b', '英文思考句'),
]
_DBWORDS = [
    (r'\bSELECT\b|\bSQL\b', '提 SQL'),
    (r'\bt_[a-z][a-z0-9_]+', '提表名'),
    (r'字段|数据库|数据表|知识库检索', '提字段/库/工具过程'),
]
_BANNED = [('**', 'Markdown 粗体'), ('#', '#'), ('|', '竖线'), ('>', '引用符'), ('`', '反引号')]


def _nonspace(s: str) -> int:
    return len(re.sub(r'\s', '', s or ''))


def _sentences(s: str) -> int:
    return len([x for x in re.split(r'[。！？!?]', s or '') if re.sub(r'\s', '', x)])


def format_check(ans: str) -> dict:
    """逐条判，返回 {id: 通过?} + 违规说明。"""
    rows = {}
    rows['F1_字数≤120'] = _nonspace(ans) <= 120
    rows['F2_句数≤3'] = _sentences(ans) <= 3
    rows['F3_无禁符'] = not [w for w, _ in _BANNED if w in (ans or '')]
    rows['F4_无过程话术'] = not [p for p, _ in _PROCS if re.search(p, ans or '')]
    rows['F5_不提库表字段'] = not [p for p, _ in _DBWORDS if re.search(p, ans or '')]
    rows['F6_不分条编号'] = not re.search(r'^\s*(?:\d+[.、)）]|[①②③④⑤])', ans or '', re.M)
    rows['F7_含金额数字'] = bool(re.search(r'\d+(?:\.\d+)?\s*万元', ans or ''))
    why = []
    for w, name in _BANNED:
        if w in (ans or ''):
            why.append('出现%s' % name)
    for p, name in _PROCS + _DBWORDS:
        if re.search(p, ans or ''):
            why.append(name)
    if not rows['F1_字数≤120']:
        why.append('非空白 %d 字 > 120' % _nonspace(ans))
    if not rows['F2_句数≤3']:
        why.append('%d 句 > 3' % _sentences(ans))
    if not rows['F6_不分条编号']:
        why.append('出现分条编号')
    if not rows['F7_含金额数字']:
        why.append('没有「X 万元」金额')
    return {'checks': rows, 'passed': sum(1 for v in rows.values() if v),
            'total': len(rows), 'why': why}


# ── 数字对账：独立实现（不调 agent 的函数），池子 = 本轮 SQL 原始值的派生 ──
_MONEY = re.compile(r'(\d[\d,]*(?:\.\d+)?)\s*万元')


def _num(v, vals: set, rawseen: list) -> None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return
    rawseen.append(x)
    vals.add(round(x, 2))
    vals.add(round(x / 10000.0, 2))


def _collect(res, vals: set, rawseen: list) -> None:
    """一条工具结果里的数值：兼容 run_sql 数组并发返回的 {'multi':True,'results':[…]}。"""
    if isinstance(res, list):
        for x in res:
            _collect(x, vals, rawseen)
        return
    if not isinstance(res, dict):
        return
    if res.get('multi') and isinstance(res.get('results'), list):
        _collect(res['results'], vals, rawseen)
    for row in (res.get('rows') or []):
        if isinstance(row, dict):
            for v in row.values():
                _num(v, vals, rawseen)
    for col in (res.get('summary') or {}).values():
        if isinstance(col, dict):
            for k in ('sum', 'mean', 'min', 'max'):
                _num(col.get(k), vals, rawseen)


def sql_pool(trace) -> tuple[set, list]:
    """本轮 run_sql 结果里出现过的数：行值、summary，以及两两和/差。

    行值是 Decimal（MySQL DECIMAL 列），一律 float() 试一次，不按类型挑。
    """
    vals = set()
    rawseen = []
    for t in (trace or []):
        if not isinstance(t, dict) or t.get('kind') != 'tool' or t.get('tool') != 'run_sql':
            continue
        _collect(t.get('result') or {}, vals, rawseen)
    base = sorted(v for v in vals if abs(v) < 1e7)
    out = set(vals)
    for i, a in enumerate(base):
        for b in base[i + 1:]:
            out.add(round(a + b, 2))
            out.add(round(abs(a - b), 2))
    return out, sorted(round(x, 2) for x in rawseen)


def reconcile(ans: str, pool: set, floor=10.0) -> dict:
    bad = []
    checked = 0
    for m in _MONEY.finditer(ans or ''):
        try:
            x = float(m.group(1).replace(',', ''))
        except ValueError:
            continue
        if x < floor:
            continue
        checked += 1
        if any(abs(x - s) <= max(0.06, abs(s) * 0.002) for s in pool):
            continue
        if x not in bad:
            bad.append(x)
    return {'checked': checked, 'bad': bad, 'pass': not bad}


def run_one(n: int) -> dict:
    actor = CurrentActor(type='user', user_id=UID, username='admin',
                         display_name='管理员', role_codes=['admin'])
    kb_adapter.set_actor(actor)
    kb_adapter.set_scope(None)
    CALLS.clear()
    t0 = time.time()
    try:
        r = agent.ask(QUESTION, user_id=UID, user_code='admin',
                      session_id='base_%d_%d' % (n, int(time.time())),
                      history_turns=0, doc_context=None, doc_files=None, max_steps=None)
    except Exception:                                      # noqa: BLE001
        return {'n': n, 'error': traceback.format_exc()[-1200:]}
    elapsed = round(time.time() - t0, 1)
    tr = r.get('trace') or []
    ans = r.get('answer') or ''
    sysinfo = next((t.get('result') for t in tr if t.get('kind') == 'system'), {})
    rounds = max([t.get('round') or 0 for t in tr] or [0])
    pools, pool_raw = sql_pool(tr)
    # 每次 run_sql 里装了几条 SQL（看它有没有按数组并发发）
    sqls = []
    for _t in tr:
        if isinstance(_t, dict) and _t.get('kind') == 'tool' and _t.get('tool') == 'run_sql':
            _s = (_t.get('args') or {}).get('sql')
            if isinstance(_s, str) and _s.strip().startswith('['):
                try:
                    _s = json.loads(_s)
                except Exception:                      # noqa: BLE001
                    pass
            sqls.append(len(_s) if isinstance(_s, list) else 1)
    rec = reconcile(ans, pools)
    fmt = format_check(ans)
    guards = {k: [t for t in tr if t.get('kind') == k]
              for k in ('number_guard', 'coef_guard')}
    return {
        'n': n, 'question': QUESTION, 'elapsed_s': elapsed,
        'steps': r.get('steps'), 'rounds': rounds,
        'llm_calls': len(CALLS),
        'tool_calls': len([t for t in tr if t.get('kind') == 'tool']),
        'sql_calls': r.get('sql_query_count'),
        'system_chars': sysinfo.get('chars'),
        'has_caliber_in_system': sysinfo.get('has_caliber'),
        'final_call': CALLS[-1] if CALLS else {},
        'calls': list(CALLS),
        'answer_chars': len(ans), 'answer_nonspace': _nonspace(ans),
        'sql_batches': sqls, 'sql_max_batch': max(sqls) if sqls else 0,
        'sql_parallel_calls': len([n for n in sqls if n > 1]),
        'answer': ans,
        'reconcile': rec,
        'pool_raw': pool_raw,
        'format': fmt,
        'guards': {k: [{'kept': g.get('kept'), 'before': g.get('before'),
                        'after': g.get('after')} for g in v] for k, v in guards.items()},
        'trace_kinds': [t.get('kind') for t in tr],
        'timings': r.get('timings'),
    }


def main() -> int:
    runs = 3
    question = QUESTION
    tag = ''
    for a in sys.argv[1:]:
        if a.startswith('--runs='):
            runs = int(a.split('=', 1)[1])
        elif a.startswith('--q='):
            question = a.split('=', 1)[1]
        elif a.startswith('--tag='):
            tag = a.split('=', 1)[1]
    globals()['QUESTION'] = question
    OUT.mkdir(exist_ok=True)
    install_timer()
    rows = []
    for n in range(1, runs + 1):
        print('[run %d/%d] %s' % (n, runs, question), flush=True)
        got = run_one(n)
        rows.append(got)
        if got.get('error'):
            print('   -> ERROR', got['error'][-300:], flush=True)
            continue
        print('   -> 轮数=%s 耗时=%ss 字数=%s 对账=%s 格式=%s/%s'
              % (got['rounds'], got['elapsed_s'], got['answer_nonspace'],
                 'PASS' if got['reconcile']['pass'] else 'FAIL %s' % got['reconcile']['bad'],
                 got['format']['passed'], got['format']['total']), flush=True)
    ok = [r for r in rows if not r.get('error')]
    agg = {}
    if ok:
        agg = {
            'runs': len(ok),
            'rounds_median': sorted(r['rounds'] for r in ok)[len(ok) // 2],
            'elapsed_median_s': sorted(r['elapsed_s'] for r in ok)[len(ok) // 2],
            'answer_nonspace_median': sorted(r['answer_nonspace'] for r in ok)[len(ok) // 2],
            'reconcile_pass_rate': round(sum(1 for r in ok if r['reconcile']['pass']) / len(ok), 2),
            'format_pass_rate': round(sum(r['format']['passed'] for r in ok)
                                      / sum(r['format']['total'] for r in ok), 2),
            'format_all_pass_runs': sum(1 for r in ok if r['format']['passed'] == r['format']['total']),
        }
    out = {'question': question, 'when': time.strftime('%Y-%m-%d %H:%M:%S'), 'tag': tag,
           'code': 'current working tree (feat/tools-routing, 简洁闸 未提交已回退)',
           'agg': agg, 'runs': rows}
    p = OUT / ('2026-11-05-基线-3次%s.json' % ('-' + tag if tag else ''))
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print('[done] 汇总', json.dumps(agg, ensure_ascii=False), flush=True)
    print('[done] 明细已写', p, flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
