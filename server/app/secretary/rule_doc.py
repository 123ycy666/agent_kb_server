# -*- coding: utf-8 -*-
"""规则取用：目录 + 按主题精确取（2026-11-01 改）。

规则已经不在知识库 `document` 里，搬进了 `prompt_material` 表（四份材料）。
这份文件负责「没被注入的那部分」怎么取：

- **必带**的小节由 `prompt_material.block()` 每轮直接拼进提示词，模型看得到；
- **不勾必带**的小节只存在库里，模型要先看目录、再 `get_rule(主题)` 精确取。

⚠️ 2026-11-01 之前这里查的是 `document.doc_type='rule'`。规则搬走后那张表已经没有 rule
   文档，如果不改这里，「取消必带」就等于「永远取不到」——静默失效，比报错更糟。
"""
from __future__ import annotations

from app.secretary import prompt_material

MAX_TOPICS = 200         # 目录上限（四份合计 59 节，留足余量）
MAX_TEXT = 4000          # 单次取回正文上限


def _rows() -> list[dict]:
    out = []
    for slot in prompt_material.SLOTS:
        for r in prompt_material.sections(slot):
            out.append({
                'slot': slot,
                'section': str(r.get('section') or ''),
                'body': str(r.get('body') or ''),
                'pinned': bool(r.get('pinned')),
            })
    return out


def topics(force: bool = False) -> list[dict]:
    """全部小节名（跨四份），给目录用。"""
    items = []
    for r in _rows():
        if not r['section']:
            continue
        body = r['body'].replace('\n', ' ')
        items.append({
            'topic': r['section'],
            'source': prompt_material.SLOT_NAMES.get(r['slot'], r['slot']),
            'hint': (body[:26] + '…') if len(body) > 26 else body,
        })
        if len(items) >= MAX_TOPICS:
            break
    return items


def menu(force: bool = False) -> str:
    """目录文本：一行一节。"""
    items = topics()
    if not items:
        return ''
    lines = ['【规则目录（要做判断先 get_rule(主题) 取原文，取不到就说明没有这条标准）】']
    for it in items:
        lines.append('- %s ｜ %s ｜ %s' % (it['topic'], it['source'], it['hint']))
    return '\n'.join(lines)


def get(topic: str = '', key: str = '', force: bool = False) -> dict:
    """按小节名取正文。命中就给，命中不到明确说没有，不拿别的节凑数。"""
    want = str(topic or key or '').strip()
    if not want:
        return {'error': '缺少 topic（要查哪条规则）',
                'topics': [t['topic'] for t in topics()][:10]}
    rows = _rows()
    hits = [r for r in rows if r['section'] and (want in r['section'] or r['section'] in want)]
    if not hits:
        all_topics = [t['topic'] for t in topics()]
        head = want[:2]
        closest = [t for t in all_topics if head and head in t][:3] or all_topics[:3]
        return {'error': '没有这条规则：%s' % want, 'closest': closest}
    parts: list[str] = []
    used = 0
    for r in hits:
        body = r['body'].strip()
        if not body:
            continue
        if used + len(body) > MAX_TEXT:
            body = body[: max(0, MAX_TEXT - used)]
        parts.append(body)
        used += len(body)
        if used >= MAX_TEXT:
            break
    return {'topic': want,
            'source': prompt_material.SLOT_NAMES.get(hits[0]['slot'], ''),
            'text': '\n'.join(parts)}
