# -*- coding: utf-8 -*-
"""文件比对：两份材料的段落级差异 → 差异清单（任务通道 `compare`）。

为什么要它（2026-10-21）
----------------------
制度/方案改版时最常见的活是"哪几处改了"。人眼比 20 页会漏，`difflib` 不会。
不做语义改写，只做**逐段增删改**，并把变更段落原文摘出来 —— 可验收。

输出两样：
- `差异清单.md`：给人看，按「新增/删除/修改」分组，带段号
- `差异清单.docx`：盖章报送用（走 docgen 的公文版式）
"""
from __future__ import annotations

import difflib
from pathlib import Path

from app.secretary import doc_files, docgen

TASK = 'compare'
MAX_ITEMS = 200


def _paras(blocks: list[dict]) -> list[str]:
    out = []
    for b in blocks or []:
        t = ' '.join(str(b.get('text') or '').split())
        if t:
            out.append(t)
    return out


def diff_paras(a: list[str], b: list[str]) -> list[dict]:
    """段落级差异：返回 [{'kind':'add|del|same','text':…,'a':序号,'b':序号}]（压缩连续 same）。"""
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    items: list[dict] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            continue
        for i in range(i1, i2):
            items.append({'kind': 'del', 'text': a[i], 'a': i + 1, 'b': None})
        for j in range(j1, j2):
            items.append({'kind': 'add', 'text': b[j], 'a': None, 'b': j + 1})
    return items[:MAX_ITEMS]


def summarize(items: list[dict]) -> dict:
    return {'add': sum(1 for x in items if x['kind'] == 'add'),
            'del': sum(1 for x in items if x['kind'] == 'del'),
            'total': len(items)}


def to_markdown(name_a: str, name_b: str, items: list[dict]) -> str:
    s = summarize(items)
    lines = ['# 文件差异清单', '',
             '- 旧：%s' % name_a, '- 新：%s' % name_b,
             '- 合计差异 %d 处（新增 %d / 删除 %d）' % (s['total'], s['add'], s['del']), '']
    if not items:
        lines.append('两份文件**没有段落级差异**。')
        return '\n'.join(lines)
    add = [x for x in items if x['kind'] == 'add']
    dele = [x for x in items if x['kind'] == 'del']
    if add:
        lines += ['## 新增段落', '']
        lines += ['%d. %s' % (x['b'], x['text']) for x in add]
        lines.append('')
    if dele:
        lines += ['## 删除段落', '']
        lines += ['%d. %s' % (x['a'], x['text']) for x in dele]
        lines.append('')
    return '\n'.join(lines)


def run(file_id: str, user_id: int, options: dict | None = None,
        progress=None, out_dir: Path | None = None) -> dict:
    opts = options or {}
    say = progress or (lambda pct, stage: None)
    other = (opts.get('other_file_id') or '').strip()
    if not other:
        raise ValueError('比对需要两份文件：options.other_file_id 缺失')

    say(10, '读取两份文件')
    meta_a = doc_files.load(file_id, user_id)
    meta_b = doc_files.load(other, user_id)
    a = _paras(doc_files.blocks(file_id, user_id, offset=0, limit=100000)['blocks'])
    b = _paras(doc_files.blocks(other, user_id, offset=0, limit=100000)['blocks'])

    say(50, '逐段比对')
    items = diff_paras(a, b)
    md = to_markdown(meta_a.get('name') or '旧文件', meta_b.get('name') or '新文件', items)

    out_dir = Path(str(out_dir or '.'))
    md_path = out_dir / '差异清单.md'
    md_path.write_text(md, encoding='utf-8')
    docx_path = out_dir / '差异清单.docx'
    body = '\n'.join('%s %s' % ('[新增]' if x['kind'] == 'add' else '[删除]', x['text'])
                     for x in items) or '两份文件没有段落级差异。'
    docgen.write_docx(docx_path, '文件差异清单',
                      [('比对说明', '旧：%s\n新：%s' % (meta_a.get('name'), meta_b.get('name'))),
                       ('差异明细', body)])

    say(100, '完成')
    return {'task': TASK, 'file_ids': [file_id, other],
            'files': [meta_a.get('name'), meta_b.get('name')],
            'summary': summarize(items), 'outputs': [md_path.name, docx_path.name]}
