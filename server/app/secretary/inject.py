# -*- coding: utf-8 -*-
"""每轮上下文装配（需求 H2「harness 的核心」）。

装配顺序（H2.1，顺序固定，不许调换）：
    身份与纪律 → 说明书目录 → 会话摘要 → 前几轮 → 会话文件清单 → 本轮已读说明正文 → 工具说明

常驻上限默认 6000 字（H2.2），超限按 **先压历史、再压目录** 处理，
压了什么、压掉多少字都记在 `compressed` 里 —— 注入预览页（V14）读的就是它。

正文一律按需取（H2.3）：说明书正文、资料原文、上传文件正文都不常驻。
说明书正文不参与向量检索（H2.4，靠 `document_chunk.is_retrievable=false`）。
"""
from __future__ import annotations

import json

from app.secretary import inject_store

ORDER = ('identity', 'discipline', 'menu', 'summary', 'history', 'files', 'spec', 'tools')
LABEL = {
    'identity': '身份', 'discipline': '纪律（红线/优先级/数字）', 'menu': '说明书目录',
    'summary': '会话摘要', 'history': '前几轮', 'files': '会话文件清单',
    'spec': '本轮说明书正文', 'tools': '工具说明',
}
MENU_MAX = 30
HISTORY_TURNS = 5

# 每段的来源：页面上要能看出「这段是库里配的、还是代码兜底的、还是这一轮自动生成的」。
# 静态来源；`tools` 段要看库里那行有没有内容，在 build() 里现算。
SOURCE = {'identity': '库', 'discipline': '库', 'menu': '自动生成', 'summary': '自动生成',
          'history': '自动生成', 'files': '自动生成', 'spec': '自动生成', 'tools': '代码兜底'}


def _fallback_identity() -> str:
    try:
        from app.secretary import prompts_data
        lines = [ln for ln in (prompts_data.IDENTITY or '').strip().splitlines()
                 if not ln.lstrip().startswith('#')]
        return '\n'.join(lines).strip()
    except Exception:                              # noqa: BLE001
        return '你是随州供电公司的办公智能助手，负责把活做成能直接用的东西。'


def identity_text() -> str:
    return inject_store.get('identity') or _fallback_identity()


def discipline_text() -> str:
    bits = [inject_store.get('redlines'), inject_store.get('priority'),
            inject_store.get('number_discipline')]
    return '\n'.join(b for b in (x.strip() for x in bits) if b)


def tools_text() -> str:
    txt = inject_store.get('tools_guide')
    if txt.strip():
        return txt
    try:
        from app.secretary import prompt_text
        return prompt_text.get('tools', prompt_text.DEFAULT_TOOLS_GUIDE)
    except Exception:                              # noqa: BLE001
        return ''


def history_text(history) -> str:
    if not history:
        return ''
    out = []
    for h in history[-HISTORY_TURNS:]:
        out.append('上一轮问：%s\n上一轮答：%s'
                   % (str(h.get('raw_question') or '')[:200],
                      str(h.get('answer') or '')[:1200]))
    return '\n\n'.join(out)


def files_text(files) -> str:
    if not files:
        return ''
    return '\n'.join('【文件%d】《%s》 约 %s 字，file_id=%s%s'
                     % (i, f.get('name'), f.get('chars'), f.get('file_id'),
                        '（本会话此前带来的）' if f.get('prev') else '')
                     for i, f in enumerate(files, 1))


def spec_block(hit) -> str:
    """本轮已读说明书的正文块（按需注入，不常驻）。"""
    if not hit:
        return ''
    item = hit.get('item') or {}
    lines = ['【本轮说明书】《%s》 v%s（产物 %s，归属 %s）'
             % (item.get('name'), item.get('version_no') or 1,
                item.get('product') or '无产物',
                '本人' if item.get('owner_id') else '全局')]
    if item.get('when_to_use'):
        lines.append('什么时候用：' + str(item['when_to_use']))
    if item.get('outline'):
        lines.append('结构：' + ' / '.join(str(x) for x in item['outline']))
    if item.get('fmt'):
        lines.append('版式：' + json.dumps(item['fmt'], ensure_ascii=False))
    if hit.get('conflicts'):
        lines.append('冲突标注：另有 %s 同时适用；本次用本篇，交付时说明用了哪篇。'
                     % '、'.join('《%s》' % c['name'] for c in hit['conflicts']))
    if item.get('body'):
        lines.append('正文（照它做）：\n' + str(item['body']))
    checks = item.get('check_items') or []
    if checks:
        lines.append('交付前逐项自检（不过不许出文件）：')
        lines += ['%d. %s' % (i, c) for i, c in enumerate(checks, 1)]
    refs = [r for r in (hit.get('refs') or []) if r.get('resolved')]
    if refs:
        lines.append('它点名的其它说明书（要顺着读）：'
                     + '、'.join('《%s》' % r['name'] for r in refs))
    return '\n'.join(lines)


def _menu(user_id, limit=MENU_MAX) -> str:
    from app.secretary import spec
    return spec.menu_text(user_id, limit=limit)


def build(user_id, session_id, question, *, spec=None, history=None, files=None,
          summary='', tools='', budget=None, menu_limit=MENU_MAX) -> dict:
    """装配本轮 system。返回 {'system','parts','total','budget','compressed'}。"""
    budget = int(budget or inject_store.budget())
    parts = {
        'identity': identity_text(),
        'discipline': discipline_text(),
        'menu': _menu(user_id, menu_limit),
        'summary': str(summary or ''),
        'history': history_text(history),
        'files': files_text(files),
        'spec': spec_block(spec),
        'tools': tools or tools_text(),
    }
    compressed = []

    def total():
        return sum(len(v) for v in parts.values())

    # 先压历史：从最早的一轮开始丢，丢到够为止
    turns = [t for t in str(parts['history'] or '').split('\n\n') if t.strip()]
    while total() > budget and len(turns) > 1:
        before = total()
        turns = turns[1:]
        parts['history'] = '\n\n'.join(turns)
        compressed.append({'name': 'history', 'why': '超限先从最早一轮开始丢',
                           'from': before, 'to': total()})
    if total() > budget and parts['history']:
        before = total()
        parts['history'] = ''
        compressed.append({'name': 'history', 'why': '只剩一轮也超限 → 前几轮整段不注入',
                           'from': before, 'to': total()})
    # 再压目录：先降到 10 篇，还超就压到 3 篇
    for lim in (10, 3):
        if total() <= budget:
            break
        before = total()
        parts['menu'] = _menu(user_id, lim)
        compressed.append({'name': 'menu', 'why': '历史压完还超限 → 目录收到 %d 篇' % lim,
                           'from': before, 'to': total()})
    # 最后压摘要（摘要是更早轮次的浓缩，可整段省）
    if total() > budget and parts['summary']:
        before = total()
        parts['summary'] = str(parts['summary'])[: max(0, len(str(parts['summary']))
                                                       - (total() - budget))]
        compressed.append({'name': 'summary', 'why': '历史与目录都压完还超限 → 摘要截短',
                           'from': before, 'to': total()})

    blocks, rows = [], []
    src = dict(SOURCE)
    if (tools or '').strip():
        src['tools'] = '调用方传入'
    elif inject_store.get('tools_guide').strip():
        src['tools'] = '库'
    for k in ORDER:
        body = (parts.get(k) or '').strip()
        if not body:
            rows.append({'name': k, 'label': LABEL[k], 'chars': 0, 'injected': False,
                         'source': src[k]})
            continue
        blocks.append(body)
        rows.append({'name': k, 'label': LABEL[k], 'chars': len(body), 'injected': True,
                     'source': src[k]})
    system = '\n\n'.join(blocks)
    return {'system': system, 'parts': rows, 'total': len(system), 'budget': budget,
            'over_budget': len(system) > budget, 'compressed': compressed,
            'labels': LABEL}


def preview(user_id, session_id=None, question='', spec=None, history=None, files=None,
            summary='') -> dict:
    """注入预览（V14）：各部分字数构成 + 压了什么。页面直接渲染它。"""
    out = build(user_id, session_id, question, spec=spec, history=history, files=files,
                summary=summary)
    return {'total': out['total'], 'budget': out['budget'],
            'over_budget': out['over_budget'],
            'parts': [dict(p, share=round(p['chars'] / max(out['total'], 1), 3))
                      for p in out['parts']],
            'compressed': out['compressed'],
            'head': out['system'][:1200]}
