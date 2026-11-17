# -*- coding: utf-8 -*-
"""工具结果交付层验收（不连库、不连模型）。

判据见 docs/04-计划/2026-11-08-工具结果交付层-设计.md §7/§8：
  1. 超限不砍内容：数组换成「前 k 条 + total + ref + next_offset」，summary/row_count 保留
  2. 分页取完能拼回原始数组（逐项相等）
  3. 输出永远合法 JSON 且不超字数上限
  4. 落盘失败不崩，骨架带 truncated
  5. ref 只接受 r[0-9a-f]{12}，路径穿越一律拒
  6. 事故样本（5 条 × 47 行）不再出现 "results": []

跑法： py -X utf8 tests/agent/test_tool_result.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, SERVER)
sys.stdout.reconfigure(encoding='utf-8')

from app.secretary import agent, config, tools_result   # noqa: E402

RESULTS = []
TMP = Path(SERVER) / 'tests' / 'output' / '_spot' / 'tool_result'
TMP.mkdir(parents=True, exist_ok=True)
config.TOOL_RESULT_DIR = str(TMP / 'store')
CAP = int(config.TOOL_RESULT_CHARS)


def check(name, ok, detail=''):
    RESULTS.append({'case': name, 'ok': bool(ok), 'detail': str(detail)[:300]})
    print(('  [OK]   ' if ok else '  [FAIL] ') + name + (('   ' + str(detail)[:160]) if detail else ''))
    return ok


def rows_of(n, tag='所'):
    return [{'power_station_name': '第%d%s' % (i, tag), 'budget_amount': str(400000 + i),
             'note_col': 'x' * 20} for i in range(n)]


def single(n=300):
    return {'columns': ['power_station_name', 'budget_amount'],
            'rows': rows_of(n), 'row_count': n,
            'summary': {'budget_amount': {'sum': 19100000.0, 'mean': 63666.67,
                                          'min': 400000, 'max': 400299, 'n': n}}}


def multi(nq=5, nrow=47):
    return {'multi': True, 'count': nq,
            'results': [dict(single(nrow), sql='SELECT … FROM t_power_ai_company_budget_result')
                        for _ in range(nq)]}


def dump_ok(text):
    try:
        return json.loads(text), True
    except Exception as exc:                       # noqa: BLE001
        return {'_json_error': str(exc)}, False


def invariant(parsed, path=''):
    """任何位置：要了 total 就必须能取回（ref）或明确 truncated；被裁空不许静默。"""
    bad = []
    if isinstance(parsed, dict):
        if 'total' in parsed and parsed.get('total') and 'items' in parsed:
            if not parsed.get('ref') and not parsed.get('truncated'):
                bad.append(path or '<root>')
        for k, v in parsed.items():
            bad += invariant(v, '%s.%s' % (path, k))
    elif isinstance(parsed, list):
        for i, v in enumerate(parsed):
            bad += invariant(v, '%s[%d]' % (path, i))
    return bad


def walk_refs(parsed, out=None):
    """收集骨架里所有 (ref, 路径)。"""
    out = [] if out is None else out
    if isinstance(parsed, dict):
        if parsed.get('ref') and 'items' in parsed:
            out.append((parsed['ref'], parsed.get('total'), len(parsed['items'])))
        for v in parsed.values():
            walk_refs(v, out)
    elif isinstance(parsed, list):
        for v in parsed:
            walk_refs(v, out)
    return out


def pull(ref):
    """按 next_offset 逐页取完，返回 (行列表, 页数)。"""
    rows, off, pages = [], 0, 0
    while True:
        r = tools_result.read_result(ref, offset=off)
        if r.get('error'):
            return rows, -1
        rows += r['rows']
        pages += 1
        if r['next_offset'] is None:
            return rows, pages
        off = r['next_offset']


def main() -> int:
    print('工具结果交付层验收（上限 %d 字）' % CAP)

    # ── 1 单条大结果：summary / row_count 保留，明细带 ref ─────────────
    t = tools_result.deliver('run_sql', single(300))
    p, ok = dump_ok(t)
    check('1a 合法 JSON 且不超上限', ok and len(t) <= CAP, '%d 字' % len(t))
    check('1b summary 保留', float(p['summary']['budget_amount']['sum']) == 19100000.0)
    check('1c row_count 保留', p.get('row_count') == 300, p.get('row_count'))
    check('1d 明细带 ref 与总数', bool(p.get('rows', {}).get('ref')) and p['rows']['total'] == 300,
          p.get('rows', {}).get('total'))
    check('1e 给的明细少于 300 但不为 0', 0 < len(p['rows']['items']) < 300, len(p['rows']['items']))
    check('1f note 指向 summary 与 read_result',
          'summary' in p['rows'].get('note', '') and 'read_result' in p['rows'].get('note', ''))

    # ── 2 事故样本：5 条 × 47 行 ───────────────────────────────────────
    src = multi()
    t2 = tools_result.deliver('run_sql', src, session_id='s_accident')
    p2, ok2 = dump_ok(t2)
    check('2a 合法 JSON 且不超上限', ok2 and len(t2) <= CAP, '%d 字' % len(t2))
    check('2b results 不许被砍成空', isinstance(p2.get('results'), list) and len(p2['results']) == 5,
          len(p2.get('results') or []))
    check('2c 5 条都还在且各带 summary',
          all('summary' in (r or {}) for r in p2['results']))
    n_ref = len(walk_refs(p2))
    check('2d 每条明细各有自己的 ref', n_ref >= 5, n_ref)
    check('2e 不出现空数组配 note 的假空', 'results": []' not in t2)
    check('2f 不变量：要了 total 就能取回', invariant(p2) == [], invariant(p2))

    # ── 3 空数组保持空，不加 ref ───────────────────────────────────────
    t3 = tools_result.deliver('run_sql', single(0))
    p3, _ = dump_ok(t3)
    check('3 空结果原样（rows=[]，无 ref）', p3.get('rows') == [] and 'ref' not in p3, t3[:100])

    # ── 4 四层嵌套 ────────────────────────────────────────────────────
    deep = {'a': {'b': {'c': [{'d': rows_of(200)}]}}}
    t4 = tools_result.deliver('run_sql', deep)
    p4, ok4 = dump_ok(t4)
    check('4a 嵌套仍合法且不超限', ok4 and len(t4) <= CAP, '%d 字' % len(t4))
    check('4b 嵌套里的数组被换掉时带 ref/truncated',
          not invariant(p4), invariant(p4) or 'ok')

    # ── 5 单个 20 万字字符串 ──────────────────────────────────────────
    t5 = tools_result.deliver('run_sql', {'text': 'x' * 200000})
    p5, ok5 = dump_ok(t5)
    check('5a 超长字符串合法且不超限', ok5 and len(t5) <= CAP, '%d 字' % len(t5))
    check('5b 明确标注截断', 'truncated' in p5 or '已截断' in str(p5), str(p5)[:100])

    # ── 6 全是小字段的超大 dict ───────────────────────────────────────
    t6 = tools_result.deliver('run_sql', {'k%d' % i: 'v' * 100 for i in range(500)})
    p6, ok6 = dump_ok(t6)
    check('6a 合法且不超限', ok6 and len(t6) <= CAP, '%d 字' % len(t6))
    check('6b 显式省略而不是假空', p6.get('truncated') is True, p6.get('note', '')[:80])

    # ── 7 上限压到 300 字 ─────────────────────────────────────────────
    t7 = tools_result.deliver('run_sql', multi(), cap=300)
    p7, ok7 = dump_ok(t7)
    check('7a 极小上限仍合法且不超', ok7 and len(t7) <= 300, '%d 字' % len(t7))
    check('7b 极小上限下仍能取回或明确截断',
          not invariant(p7), invariant(p7) or str(p7)[:120])

    # ── 8 ref 只认白名单（路径穿越）──────────────────────────────────
    for bad in ('', '../etc/passwd', 'r' + 'a' * 200, 'rZZZZZZZZZZZZ', 'r../../x'):
        r = tools_result.read_result(bad)
        check('8 ref 拒收 %r' % bad[:16], 'error' in r, r.get('error', ''))

    # ── 9 落盘不可写：不崩，带 truncated ──────────────────────────────
    blocker = TMP / 'blocker'
    blocker.write_text('x', encoding='utf-8')
    keep = config.TOOL_RESULT_DIR
    config.TOOL_RESULT_DIR = str(blocker / 'cannot' / 'mkdir')
    try:
        t9 = tools_result.deliver('run_sql', single(300))
        p9, ok9 = dump_ok(t9)
        check('9a 落盘失败不崩', ok9, t9[:60])
        check('9b 明确 truncated', p9.get('rows', {}).get('truncated') is True
              or p9.get('truncated') is True, str(p9)[:120])
    finally:
        config.TOOL_RESULT_DIR = keep

    # ── 10 分页取完 == 原始数组（单条 + 数组内某条）───────────────────
    t10 = tools_result.deliver('run_sql', single(300))
    p10, _ = dump_ok(t10)
    rows_back, pages = pull(p10['rows']['ref'])
    check('10a 单条结果能整份拼回', rows_back == rows_of(300) and pages > 1,
          '%d 行 / %d 页' % (len(rows_back), pages))
    t10b = tools_result.deliver('run_sql', multi(), session_id='s_page')
    p10b, _ = dump_ok(t10b)
    ref0 = p10b['results'][0]['rows']['ref']
    back0, _ = pull(ref0)
    check('10b 数组里第 1 条能整份拼回', back0 == src['results'][0]['rows'], '%d 行' % len(back0))

    # ── 11 中文 / emoji / 引号 / 换行 ─────────────────────────────────
    tricky = {'rows': [{'n': '随县"供电所\n😀 %d' % i} for i in range(300)]}
    t11 = tools_result.deliver('run_sql', tricky, cap=2000)
    p11, ok11 = dump_ok(t11)
    ok11b = False
    if ok11 and isinstance(p11.get('rows'), dict):
        ref11 = p11['rows'].get('ref')
        if ref11:
            got, _ = pull(ref11)
            ok11b = got == tricky['rows']
    check('11 中文/emoji/引号不串行且能原样取回', ok11 and ok11b, '%d 字' % len(t11))

    # ── 12 session_id 为空也读得回 ────────────────────────────────────
    t12 = tools_result.deliver('run_sql', single(300), session_id=None)
    p12, _ = dump_ok(t12)
    got12, _ = pull(p12['rows']['ref'])
    check('12 无 session 也能取回', got12 == rows_of(300), '%d 行' % len(got12))

    # ── 13 接线：工具表里有 read_result，回填走 deliver ───────────────
    names = [t['function']['name'] for t in agent.TOOLS]
    src_agent = open(os.path.join(SERVER, 'app', 'secretary', 'agent.py'), encoding='utf-8').read()
    check('13a 工具表有 read_result', 'read_result' in names, names)
    check('13b execute 认识 read_result',
          'read_result' in src_agent and "name == 'read_result'" in src_agent)
    check('13c 回填用 deliver', 'tools_result.deliver(o[\'name\'], o[\'result\']' in src_agent)
    check('13d 不再有旧的按字符砍逻辑', 'list_key' not in src_agent and 'build(mid)' not in src_agent)
    rr = agent.execute('read_result', {'ref': p12['rows']['ref'], 'offset': 0, 'limit': 2})
    check('13e execute 能取回 2 条', len(rr.get('rows') or []) == 2, len(rr.get('rows') or []))

    out = Path(SERVER) / 'tests' / 'output' / 'test_results' / 'tool_result_test.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({'cap': CAP, 'cases': RESULTS}, ensure_ascii=False, indent=1),
                   encoding='utf-8')
    bad = [r for r in RESULTS if not r['ok']]
    print('\n%d 项检查，%d 项失败' % (len(RESULTS), len(bad)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
