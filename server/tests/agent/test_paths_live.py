# -*- coding: utf-8 -*-
"""真实模型 + 真实文件的链路测试（2026-11-05；2026-11-08 补产物清单与直调模式）。

跑法：
    py -X utf8 server/tests/agent/test_paths_live.py            # 五条链路（真模型）
    py -X utf8 server/tests/agent/test_paths_live.py --direct   # 只直调四个工具，不走模型

产出：
    output/2026-11-08-链路测试-原始.json      五条链路逐次调用 + 产物清单
    output/2026-11-08-四工具直调-原始.json    直调记录
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
from app.secretary import tools_file                        # noqa: E402

DESK = Path(os.path.expanduser('~')) / 'Desktop' / '测试'
DESK2 = Path(os.path.expanduser('~')) / 'Desktop' / '转正报告'
OUT = ROOT / 'output'
ART = ROOT / 'server' / 'data' / 'agent_out'
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


def actor() -> None:
    kb_adapter.set_actor(CurrentActor(type='user', user_id=UID, username='admin',
                                      display_name='管理员', role_codes=['admin']))
    kb_adapter.set_scope(None)


def upload(path: Path) -> dict:
    meta = doc_files.save(UID, path.name, path.read_bytes(), purpose='chat')
    return {'file_id': meta['file_id'], 'name': meta['name'],
            'chars': (meta.get('parsed') or {}).get('chars'),
            'ok': (meta.get('parsed') or {}).get('ok')}


def artifacts(since: float) -> list[dict]:
    """跑完之后落在 out/ 里的产物，带大小。"""
    out = []
    if not ART.is_dir():
        return out
    for f in ART.rglob('*'):
        try:
            st = f.stat()
        except OSError:
            continue
        if not f.is_file() or st.st_mtime < since:
            continue
        if f.parent.name != 'out':
            continue
        out.append({'path': str(f), 'bytes': st.st_size,
                    'name': f.name, 'dir': str(f.parent)})
    return sorted(out, key=lambda x: x['path'])


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
    if res.get('multi'):
        return 'multi=%s' % res.get('count')
    return str(res)[:120]


def tool_calls(trace) -> list[dict]:
    out = []
    for t in trace or []:
        if isinstance(t, dict) and t.get('kind') == 'tool':
            res = t.get('result') or {}
            out.append({'tool': t.get('tool'), 'args': t.get('args') or {},
                        'ms': t.get('ms'), 'summary': _sum(res),
                        'ok': not bool(res.get('error')),
                        'error': res.get('error'),
                        'raw': res if t.get('tool') in ('make_file', 'read_file', 'kb_read') else None})
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
     'files': ['转正申请表.xlsx']},
    {'id': 'doc', 'title': '文档类 · 出 Word', 'q': '把这份报告的主要结论写成一份 Word 汇报。',
     'files': ['A924_POC测试报告_正式版.docx']},
]


def run_one(sc: dict) -> dict:
    actor()
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
            'llm': list(LLM), 'artifacts': artifacts(t0),
            'answer': r.get('answer') or '', 'steps': r.get('steps')}


def direct() -> int:
    """不走模型：四个工具各调一次，验分页/全文/出三种文件/跑脚本，并列出落盘产物。"""
    actor()
    t0 = time.time()
    wd = ROOT / 'server' / 'data' / 'agent_out' / '_direct'
    tools_file.set_ctx(user_id=UID, out_dir=str(wd))
    rep: dict = {'at': time.strftime('%Y-%m-%d %H:%M:%S')}

    fid = None
    for name in ('A924_POC测试报告_正式版.docx', '转正申请表.xlsx'):
        p = DESK / name if (DESK / name).exists() else DESK2 / name
        if p.exists():
            rep.setdefault('upload', {})[name] = upload(p)
            fid = fid or rep['upload'][name]['file_id']
    if fid:
        t = time.time()
        r1 = tools_file.read_file(fid, 0, 20)
        rep['read_file_p1'] = {'ms': int((time.time() - t) * 1000), 'summary': _sum(r1),
                               'total_blocks': r1.get('total_blocks'),
                               'next_offset': r1.get('next_offset'),
                               'first': (r1.get('blocks') or [{}])[0].get('text', '')[:120]}
        if r1.get('next_offset') is not None:
            r2 = tools_file.read_file(fid, r1['next_offset'], 20)
            rep['read_file_p2'] = {'summary': _sum(r2), 'next_offset': r2.get('next_offset')}
    t = time.time()
    rk = tools_file.kb_read(doc_id=1)
    rep['kb_read'] = {'ms': int((time.time() - t) * 1000), 'summary': _sum(rk),
                      'name': rk.get('name'), 'total_chunks': rk.get('total_chunks')}
    t = time.time()
    rk2 = tools_file.kb_read(doc_name='文章速读做法')
    rep['kb_read_byname'] = {'summary': _sum(rk2), 'name': rk2.get('name')}

    specs = {
        'xlsx': {'sheets': [{'name': '预算执行', 'rows': [['单位', '分配', '执行'],
                                                         ['全市', 1880, 2441.84]]}]},
        'docx': {'title': '预算执行情况汇报',
                 'sections': [['总体', '今年全市分配 1880 万元，执行 2441.84 万元。'],
                              ['问题', '工单审批通过率偏低。']]},
        'pptx': {'title': '预算执行汇报', 'slides': [
            {'layout': 'cover', 'title': '预算执行情况', 'bullets': ['2026 年 1-10 月']},
            {'layout': 'bullets', 'title': '关键数',
             'bullets': ['分配 1880 万元', '执行 2441.84 万元', '工单 1073 张']},
            {'layout': 'end', 'title': '以上'}]},
    }
    for kind, spec in specs.items():
        t = time.time()
        r = tools_file.make_file(kind, spec)
        rep['make_file_' + kind] = {'ms': int((time.time() - t) * 1000), 'summary': _sum(r),
                                    'name': r.get('name'), 'bytes': r.get('bytes'),
                                    'url': r.get('url'), 'error': r.get('error'),
                                    'self_check': r.get('self_check')}
    t = time.time()
    rc = tools_file.run_code('from openpyxl import Workbook\n'
                             'wb=Workbook(); ws=wb.active; ws.append(["甲",1])\n'
                             'wb.save("out/脚本产物.xlsx")\nprint("done")', timeout=60)
    rep['run_code'] = {'ms': int((time.time() - t) * 1000), 'summary': _sum(rc),
                       'ok': rc.get('ok'), 'stdout': rc.get('stdout'),
                       'outputs': rc.get('outputs'), 'error': rc.get('error')}
    rep['artifacts'] = artifacts(t0)
    (OUT / '2026-11-08-四工具直调-原始.json').write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding='utf-8')
    for k, v in rep.items():
        if k.startswith(('read_file', 'kb_read', 'make_file', 'run_code')):
            print('%-20s %s' % (k, json.dumps({kk: vv for kk, vv in v.items() if kk != 'raw'},
                                              ensure_ascii=False)[:220]))
    print('[产物]')
    for a in rep['artifacts']:
        print('  %8d B  %s' % (a['bytes'], a['path']))
    return 0


def main() -> int:
    if '--direct' in sys.argv:
        print('[run] direct tools only', flush=True)
        return direct()
    OUT.mkdir(exist_ok=True)
    install_timer()
    rows = []
    for sc in SCEN:
        print('[run]', sc['id'], flush=True)
        rows.append(run_one(sc))
        got = rows[-1]
        print('   ->', json.dumps({k: got.get(k) for k in ('route', 'tools_given', 'elapsed_s')},
                                  ensure_ascii=False)[:400], flush=True)
        for c in got.get('calls') or []:
            print('      %-16s %s' % (c.get('tool'), c.get('summary')), flush=True)
        for a in got.get('artifacts') or []:
            print('      [产物] %s B %s' % (a['bytes'], a['name']), flush=True)
    (OUT / '2026-11-08-链路测试-原始.json').write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    print('[done] 原始结果已写', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
