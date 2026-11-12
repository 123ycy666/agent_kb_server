# -*- coding: utf-8 -*-
"""做法库读取（2026-11-11 办公线）。

和另外两路知识的分工：
    document(doc_type='doc')   事实原文 —— 走语义检索 kb_search
    document(doc_type='rule')  判据     —— 走 rule_doc.get(topic) 精确取
    document(doc_type='howto') 做法     —— 走本模块 get(task) 精确取

为什么做法不做向量检索：实测三次 kb_search 都只命中同一篇、相似度 0.016~0.033，
和不相关的问题一个分；做法是结构化的（fmt/outline/check），按任务名取才有确定答案。
"""
from __future__ import annotations

import json
import time

_CACHE: dict = {'menu': None, 'at': 0.0, 'rows': {}}
_TTL = 60


def _engine():
    from app.core.database import engine
    return engine


def _q(sql, **kw):
    from sqlalchemy import text
    with _engine().connect() as conn:
        return [dict(r._mapping) for r in conn.execute(text(sql), kw)]


def _one_line(item: dict) -> str:
    bits = [item['task'], str(item.get('product') or '')]
    tone = str(item.get('tone') or '').split('；')[0].strip()
    if tone:
        bits.append(tone)
    ln = item.get('length') or {}
    if ln.get('min_chars') or ln.get('max_chars'):
        bits.append('%s-%s字' % (ln.get('min_chars') or 0, ln.get('max_chars') or '?'))
    return ' · '.join(x for x in bits if x)


def _row_to_item(r: dict) -> dict:
    item = {
        'task': r['task'],
        'product': r['product'],
        'applies': r.get('applies') or [],
        'fmt': r.get('fmt') or {},
        'outline': r.get('outline') or [],
        'length': r.get('length') or {},
        'tone': r.get('tone') or '',
        'check': r.get('check_items') or [],
        'doc_id': r.get('doc_id'),
        'version_no': r.get('version_no') or 1,
    }
    item['one_line'] = _one_line(item)
    return item


def _load() -> list[dict]:
    now = time.time()
    if _CACHE['menu'] is not None and now - _CACHE['at'] < _TTL:
        return _CACHE['menu']
    try:
        rows = _q("""select t.task, t.product, t.applies, t.fmt, t.outline, t.length,
                            t.tone, t.check_items, t.doc_id, t.version_no
                       from howto_task t join document d on d.id = t.doc_id
                      where t.enabled and d.is_active
                      order by t.id""")
    except Exception as exc:                        # noqa: BLE001 - 做法读不到不阻断问答
        print('[howto] 读取做法库失败（按无做法继续）：%s: %s'
              % (type(exc).__name__, str(exc)[:120]), flush=True)
        rows = []
    _CACHE['menu'] = [_row_to_item(r) for r in rows]
    _CACHE['at'] = now
    _CACHE['rows'] = {i['task']: i for i in _CACHE['menu']}
    return _CACHE['menu']


def invalidate() -> None:
    _CACHE['menu'] = None
    _CACHE['at'] = 0.0


def menu() -> list[dict]:
    """做法目录：一行一个（任务名 · 产物 · 口吻/篇幅），进 system。"""
    return _load()


def menu_text() -> str:
    items = _load()
    if not items:
        return ''
    return '\n'.join('- ' + i['one_line'] for i in items)


def tasks() -> list[str]:
    return [i['task'] for i in _load()]


def get(task: str = '', product: str = '') -> dict:
    """按任务名精确取做法；不给任务名就按产物挑第一个。取不到返回 {}。"""
    items = _load()
    task = (task or '').strip()
    if task:
        for i in items:
            if i['task'] == task:
                return dict(i, body=body(i['doc_id']))
        for i in items:
            if task in i['task'] or i['task'] in task:
                return dict(i, body=body(i['doc_id']))
    product = (product or '').strip().lower()
    if product:
        for i in items:
            if str(i['product']).lower() == product:
                return dict(i, body=body(i['doc_id']))
    return {}


def match(text: str) -> dict:
    """按做法自带 applies 词表挑一个（词表是数据，不是代码规则）。"""
    q = str(text or '')
    best, hit = None, 0
    for i in _load():
        n = sum(1 for w in (i.get('applies') or []) if w and w in q)
        if n > hit:
            best, hit = i, n
    return dict(best, body=body(best['doc_id'])) if best else {}


def body(doc_id) -> str:
    """做法正文：按 chunk_index 拼回。"""
    if not doc_id:
        return ''
    key = 'body:%s' % doc_id
    if key in _CACHE['rows']:
        return _CACHE['rows'][key]
    try:
        rows = _q("""select content from document_chunk where doc_id = :d
                      order by chunk_index""", d=doc_id)
        text = '\n'.join(r['content'] for r in rows)
    except Exception:                               # noqa: BLE001
        text = ''
    _CACHE['rows'][key] = text
    return text


def for_prompt(task: str = '', product: str = '', question: str = '') -> dict:
    """办公线用：拿到做法 + 一段可直接进 system 的说明。

    顺序：指定任务 → 按问题匹配（applies 是数据词表） → 按产物兜底。
    2026-11-11 修：原来直接按产物挑，read 线（产物 checklist）永远挑到第一篇
    「文章纠错」，用户说「速读」也拿到纠错的做法。
    """
    item = {}
    if task:
        item = get(task)
    if not item and question:
        item = match(question)
    if not item:
        item = get('', product)
    if not item:
        return {'item': {}, 'block': ''}
    lines = ['【本轮做法】《%s》 v%s（产物 %s）'
             % (item['task'], item.get('version_no') or 1, item['product'])]
    if item.get('tone'):
        lines.append('口吻：' + item['tone'])
    if item.get('outline'):
        lines.append('结构：' + ' / '.join(str(x) for x in item['outline']))
    ln = item.get('length') or {}
    if ln:
        lines.append('篇幅：' + json.dumps(ln, ensure_ascii=False))
    if item.get('check'):
        lines.append('交卷前逐项对：' + '；'.join(str(x) for x in item['check']))
    if item.get('body'):
        lines.append('原文（照它做）：\n' + item['body'])
    return {'item': item, 'block': '\n'.join(lines)}
