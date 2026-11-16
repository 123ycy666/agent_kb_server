# -*- coding: utf-8 -*-
"""做法读取的兼容层（2026-11-16 阶段 2）：数据源已换成 `kb.spec`。

上一代实现读 `howto_task INNER JOIN document`，两个病根：
  · 目录要参数行 + 文档行同时在才进得去；
  · 参数只能由脚本从 YAML 头灌。
现在说明书就是 `kb.spec`（一行一篇，正文直接存在 `body`），这个文件只保留
**老调用方的签名**（`menu / menu_text / get / body / match / for_prompt / tasks`），
内部全部转调 `app.secretary.spec`。调用方（`agent.execute('read_howto')`、
`tools_file._doc_fmt`）一行没改。

为什么留着它而不是直接删：`tools_file` 的版式来源是按任务名取 fmt，
这个接口是稳定的；换源不该顺手改接口（V15 只要求"只有一份真身"，这一层不含数据）。
"""
from __future__ import annotations

import json


def _uid():
    try:
        from app.secretary import tools_file
        return (tools_file.get_ctx() or {}).get('user_id')
    except Exception:                              # noqa: BLE001
        return None


def _items():
    from app.secretary import spec as SP
    uid = _uid()
    out = []
    for r in SP.visible(uid, 'spec'):
        out.append({
            'task': r['name'], 'product': r.get('product') or '',
            'applies': r.get('applies') or [], 'fmt': r.get('fmt') or {},
            'outline': r.get('outline') or [], 'length': {},
            'tone': '', 'check': r.get('check_items') or [],
            'doc_id': r.get('doc_id'), 'version_no': r.get('version_no') or 1,
            'spec_id': r['id'],
            'one_line': ' · '.join(x for x in (r['name'], r.get('product') or '',
                                               (r.get('when_to_use') or '')[:40]) if x),
        })
    return out


def invalidate() -> None:
    from app.secretary import spec as SP
    SP.invalidate()


def menu() -> list[dict]:
    from app.secretary import spec as SP
    return [dict(i, line=SP._line({'name': i['task'], 'when_to_use':
                                   (i['one_line'].split(' · ')[-1] if len(i['one_line'].split(' · ')) > 2 else ''),
                                   'product': i['product'], 'conflict_with': []}))
            for i in _items()]


def menu_text() -> str:
    from app.secretary import spec as SP
    return SP.menu_text(_uid())


def tasks() -> list[str]:
    return [i['task'] for i in _items()]


def _hit(name=''):
    from app.secretary import spec as SP
    h = SP.load(_uid(), name=name) if name else {}
    return h or {}


def get(task: str = '', product: str = '') -> dict:
    """按任务名精确取；不给任务名就按产物挑一篇。取不到返回 {}。"""
    items = _items()
    task = (task or '').strip()
    pick = {}
    if task:
        for i in items:
            if i['task'] == task:
                pick = i
                break
        if not pick:
            for i in items:
                if task in i['task'] or i['task'] in task:
                    pick = i
                    break
    if not pick and product:
        for i in items:
            if str(i['product']).lower() == str(product).lower():
                pick = i
                break
    if not pick:
        return {}
    hit = _hit(pick['task'])
    return dict(pick, body=(hit.get('body') or ''))


def match(text: str) -> dict:
    """按说明书自带 applies 词表挑一篇（词表是数据，不是代码规则）。"""
    from app.secretary import spec as SP
    hit = SP.load(_uid(), question=str(text or ''))
    if not hit:
        return {}
    item = hit['item']
    return {'task': item['name'], 'product': item.get('product') or '',
            'applies': item.get('applies') or [], 'fmt': item.get('fmt') or {},
            'outline': item.get('outline') or [], 'length': {},
            'tone': '', 'check': item.get('check_items') or [],
            'doc_id': item.get('doc_id'), 'version_no': item.get('version_no') or 1,
            'spec_id': item['id'], 'body': hit['body'], 'one_line': SP._line(item)}


def body(doc_id) -> str:
    """旧接口：按 doc_id 取正文（说明书正文已存在 body，这里按 doc_id 反查）。"""
    if not doc_id:
        return ''
    from app.secretary import spec as SP
    rows = SP._q("select body from kb.spec where doc_id=%s and enabled "
                 "order by version_no desc limit 1", (doc_id,))
    return (rows[0]['body'] if rows else '') or ''


def for_prompt(task: str = '', product: str = '', question: str = '') -> dict:
    """办公线用：拿到说明书 + 一段可直接进 system 的说明。"""
    item = {}
    if task:
        item = get(task)
    if not item and question:
        item = match(question)
    if not item and product:
        item = get('', product)
    if not item:
        return {'item': {}, 'block': ''}
    lines = ['【本轮说明书】《%s》 v%s（产物 %s）'
             % (item['task'], item.get('version_no') or 1, item['product'] or '无产物')]
    if item.get('outline'):
        lines.append('结构：' + ' / '.join(str(x) for x in item['outline']))
    if item.get('fmt'):
        lines.append('版式：' + json.dumps(item['fmt'], ensure_ascii=False))
    if item.get('check'):
        lines.append('交卷前逐项对：' + '；'.join(str(x) for x in item['check']))
    if item.get('body'):
        lines.append('原文（照它做）：\n' + item['body'])
    return {'item': item, 'block': '\n'.join(lines)}
