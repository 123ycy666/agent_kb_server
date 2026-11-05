# -*- coding: utf-8 -*-
"""真实模型 + 真实文件的五条链路测试（2026-11-05）。

跑法：py -X utf8 server/tests/agent/test_paths_live.py
产出：output/2026-11-05-链路测试-原始.json
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'server'))
os.chdir(ROOT / 'server')

from app.core.deps import CurrentActor                      # noqa: E402
from app.secretary import agent, doc_files, kb_adapter      # noqa: E402

DESK = Path(os.path.expanduser('~')) / 'Desktop' / '测试'
DESK2 = Path(os.path.expanduser('~')) / 'Desktop' / '转正报告'
OUT = ROOT / 'output'
UID = 1
LLM: list = []          # 每次模型调用的耗时/参数，报告用


def install_timer() -> None:
    """包一层 _chat，记下每次模型调用的耗时与提示词字数。"""
    orig = agent._chat

    def timed(msgs, model, **kw):
        t0 = time.time()
        r = orig(msgs, model, **kw)
        LLM.append({'ms': int((time.time() - t0) * 1000),
                    'use_tools': bool(kw.get('use_tools')),
                    'max_tokens': kw.get('max_tokens'),
                    'prompt_chars': sum(len(str(m.get('content') or '')) for m in msgs)})
        return r

    agent._chat = timed


def upload(path: Path) -> dict:
    meta = doc_files.save(UID, path.name, path.read_bytes(), purpose='chat')
    return {'file_id': meta['file_id'], 'name': meta['name'],
            'chars': (meta.get('parsed') or {}).get('chars'),
            'ok': (meta.get('parsed') or {}).get('ok')}


def _sum(res) -> str:
    if not isinstance(res, dict):
        return str(res)[:120]
    if res.get('error'):
        return 'ERROR: ' + str(res['error'])[:120]
    if 'blocks' in res:
        return 'blocks=%s next_offset=%s' % (len(res.get('blocks') or []), res.get('next_offset'))
    if 'chunks' in res:
        return 'chunks=%s next_offset=%s' % (len(res.get('chunks') or []), res.get('next_offset'))
    if 'nodes' in res:
        return 'nodes=%s' % len(res.get('nodes') or [])
    if res.get('name') and res.get('url'):
        return '文件 %s %sB 自检=%s' % (res.get('name'), res.get('bytes'),
                                        (res.get('self_check') or {}).get('verdict'))
    if 'rows' in res:
        return 'rows=%s' % len(res.get('rows') or [])
    return str(res)[:120]


def tool_calls(trace) -> list[dict]:
    out = []
    for t in trace or []:
        if isinstance(t, dict) and t.get('kind') == 'tool':
            res = t.get('result') or {}
            out.append({'tool': t.get('tool'), 'args': t.get('args') or {},
                        'ms': t.get('ms'), 'summary': _sum(res),
                        'ok': not bool(res.get('error')),
                        'error': res.get('error')})
    return out


SCEN = [
    {'id': 'chat', 'title': '纯聊天', 'q': '你好，随便聊两句就行。', 'files': []},
    {'id': 'data', 'title': '问数（业务库）', 'q': '今年预算执行得怎么样？', 'files': []},
    {'id': 'digest', 'title': '文档类 · 速读清单',
     'q': '速读这份测试报告，把跟我（项目管理）有关的要点列成清单，每条带出处。',
     'files': ['A924_POC测试报告_正式版.docx']},
    {'id': 'ppt', 'title': '文档类 · 转 PPT', 'q': '把这份报告做成汇报 PPT。',
     'files': ['A924_POC测试报告_正式版.docx']},
    {'id': 'excel', 'title': '表格类 · 审查', 'q': '帮我看下这张表有没有问题，把问题列出来。',
     'files': ['转正申请表.xlsx']},]


def run_one(sc: dict) -> dict:
    actor = CurrentActor(type='user', user_id=UID, username='admin',
                         display_name='管理员', role_codes=['admin'])
    kb_adapter.set_actor(actor)
    kb_adapter.set_scope(None)
    ups = []
    for name in sc['files']:
        p = DESK / name
        if not p.exists():
            p = DESK2 / name
        if not p.exists():
            return {'id': sc['id'], 'title': sc['title'], 'error': '样本不存在：%s' % name}
        try:
            ups.append(upload(p))
        except Exception as exc:                   # noqa: BLE001
            return {'id': sc['id'], 'title': sc['title'],
                    'error': '上传失败 %s: %s' % (name, exc)}
    t0 = time.time()
    LLM.clear()
    try:
        r = agent.ask(sc['q'], user_id=UID, user_code='admin',
                      session_id='live_%s_%d' % (sc['id'], int(time.time())),
                      history_turns=0, doc_files=ups or None,
                      doc_context=None, max_steps=10)
    except Exception:                              # noqa: BLE001
        return {'id': sc['id'], 'title': sc['title'], 'input': sc['q'], 'files': ups,
                'error': traceback.format_exc()[-1500:]}
    tr = r.get('trace') or []
    route = next((t.get('result') for t in tr if t.get('kind') == 'intent'), {})
    toolset = next((t.get('result', {}).get('tools') for t in tr if t.get('kind') == 'tools'), [])
    sysinfo = next((t.get('result') for t in tr if t.get('kind') == 'system'), {})
    return {'id': sc['id'], 'title': sc['title'], 'input': sc['q'], 'files': ups,
            'route': route, 'tools_given': toolset, 'system': sysinfo,
            'calls': tool_calls(tr), 'elapsed_s': round(time.time() - t0, 1),
            'llm': list(LLM),
            'answer': r.get('answer') or '', 'steps': r.get('steps')}


def main() -> int:
    OUT.mkdir(exist_ok=True)
    install_timer()
    rows = []
    for sc in SCEN:
        print('[run]', sc['id'], flush=True)
        rows.append(run_one(sc))
        got = rows[-1]
        print('   ->', json.dumps({k: got.get(k) for k in ('route', 'tools_given', 'elapsed_s')},
                                  ensure_ascii=False)[:400], flush=True)
    (OUT / '2026-11-05-链路测试-原始.json').write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    print('[done] 原始结果已写', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
