# -*- coding: utf-8 -*-
"""内置评测：真实跑模型，按判据给 pass/fail，报告落 output/test_results。

用法（在 server/ 下）：
    python -m app.agent.eval
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path

from app.agent import runtime

USER = 1
OUT = Path(__file__).resolve().parents[3] / 'output' / 'test_results' / '2026-11-17-干活线智能体'

CASES = [
    {
        'id': 'doc_notice',
        'text': '写个通知，明天上午九点开项目部例会，出一份 word',
        'want_tools': ['make_file'],
        'want_artifact': 'docx',
    },
    {
        'id': 'doc_report',
        'text': '写一份本周工作汇报，围绕缺陷治理，出一份 word',
        'want_tools': ['make_file'],
        'want_artifact': 'docx',
    },
    {
        'id': 'data_missing',
        'text': '上个月全市欠费多少钱？按县公司拆开',
        'want_tools': ['read_skill'],
        'want_any': ['db_schema', 'query_data', 'search_kb'],
    },
    {
        'id': 'chat',
        'text': '用一句话说你能干什么',
        'forbid_tools': ['make_file'],
    },
]

MULTI = {
    'id': 'multi_edit',
    'texts': [
        '写个通知，明天上午九点开项目部例会，出一份 word',
        '地点改成三楼会议室，其他不动，重新出一份',
    ],
    'want_tools_2': ['read_file', 'make_file'],
    'want_artifact': 'docx',
}


async def _run(text: str, session_id: str | None = None) -> list[dict]:
    out = []
    async for ev in runtime.run_stream(user_id=USER, text=text, session_id=session_id):
        out.append(ev)
    return out


def _view(events: list[dict]) -> dict:
    tools = [e.get('name') for e in events if e.get('type') == 'tool_call']
    done = next((e for e in events if e.get('type') == 'done'), {}) or {}
    return {'tools': tools, 'done': done,
            'artifacts': done.get('artifacts') or [],
            'answer': done.get('answer') or '',
            'ms': done.get('ms'), 'tokens': done.get('tokens')}


def _check(case: dict, v: dict) -> list[str]:
    bad = []
    for t in case.get('want_tools') or []:
        if t not in v['tools']:
            bad.append('缺工具 %s（实际 %s）' % (t, v['tools']))
    any_list = case.get('want_any') or []
    if any_list and not any(t in v['tools'] for t in any_list):
        bad.append('这几个里一个都没调：%s（实际 %s）' % (any_list, v['tools']))
    for t in case.get('forbid_tools') or []:
        if t in v['tools']:
            bad.append('不该调 %s' % t)
    kind = case.get('want_artifact')
    if kind and not any((a.get('kind') == kind) for a in v['artifacts']):
        bad.append('没有 %s 产物' % kind)
    if not v['answer'].strip():
        bad.append('没有回答')
    return bad


async def main() -> int:
    rows, details = [], []
    t0 = time.time()
    for case in CASES:
        events = await _run(case['text'])
        v = _view(events)
        bad = _check(case, v)
        rows.append((case['id'], 'PASS' if not bad else 'FAIL', v, bad))
        details.append({'id': case['id'], 'text': case['text'], 'check': bad,
                        'tools': v['tools'], 'artifacts': v['artifacts'],
                        'answer': v['answer'][:800], 'ms': v['ms'], 'tokens': v['tokens']})

    # 多轮：第二轮必须读上一轮的文件再改
    ev1 = await _run(MULTI['texts'][0])
    v1 = _view(ev1)
    sid = v1['done'].get('session_id')
    ev2 = await _run(MULTI['texts'][1], sid)
    v2 = _view(ev2)
    bad = []
    for t in MULTI['want_tools_2']:
        if t not in v2['tools']:
            bad.append('第二轮缺工具 %s（实际 %s）' % (t, v2['tools']))
    if not any(a.get('kind') == MULTI['want_artifact'] for a in v1['artifacts'] + v2['artifacts']):
        bad.append('多轮没有 docx 产物')
    rows.append((MULTI['id'], 'PASS' if not bad else 'FAIL', v2, bad))
    details.append({'id': MULTI['id'], 'text': ' / '.join(MULTI['texts']), 'check': bad,
                    'tools_1': v1['tools'], 'tools_2': v2['tools'],
                    'artifacts': v1['artifacts'] + v2['artifacts'],
                    'answer': v2['answer'][:800], 'session_id': sid})

    passed = sum(1 for r in rows if r[1] == 'PASS')
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M')
    md = OUT / ('eval-%s.md' % stamp)
    lines = ['# 干活线智能体 评测（%s）' % datetime.now().strftime('%Y-%m-%d %H:%M'),
             '', '用例 %d，通过 %d，用时 %.0fs' % (len(rows), passed, time.time() - t0), '',
             '| 用例 | 结果 | 工具 | 产物 | 说明 |', '|---|---|---|---|---|']
    for cid, res, v, bad in rows:
        lines.append('| %s | %s | %s | %s | %s |' % (
            cid, res, ' → '.join(v['tools']) or '—',
            ', '.join(a.get('name') or '' for a in v['artifacts']) or '—',
            '；'.join(bad) or 'ok'))
    md.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    (OUT / ('eval-%s.json' % stamp)).write_text(
        json.dumps(details, ensure_ascii=False, indent=2), encoding='utf-8')

    print('通过 %d/%d，报告 %s' % (passed, len(rows), md))
    for cid, res, _v, bad in rows:
        print('%-14s %s %s' % (cid, res, '；'.join(bad)))
    return 0 if passed == len(rows) else 1


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
