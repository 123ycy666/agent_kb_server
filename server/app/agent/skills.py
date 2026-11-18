# -*- coding: utf-8 -*-
"""技能：说明书就是技能。目录只给名字/用途/产物，正文按需读。"""
from __future__ import annotations

# 问数线的两段材料也是技能：内容只有一份（cfg.prompt_material），改在页面上改。
VIRTUAL = {
    '查数口径': 'fetch',
    '作答规则': 'answer',
}
VIRTUAL_LINES = ('- 查数口径 · 查业务数据前必读：表字段 + 指标算法 + 必需过滤\n'
                 '- 作答规则 · 问数作答的口吻与数字要求')


def catalog(user_id: int, limit: int = 30) -> str:
    try:
        from app.secretary import spec
        base = spec.menu_text(user_id, limit=limit) or ''
    except Exception:                              # noqa: BLE001 - 取不到目录不影响干活
        base = ''
    return (base + '\n' + VIRTUAL_LINES).strip()


def read(user_id: int, name: str) -> dict:
    """返回 {ok, name, body, when_to_use, product, fmt, check_items, refs, conflicts}。"""
    key = (name or '').strip()
    if key in VIRTUAL:
        try:
            from app.secretary import prompt_material
            body = prompt_material.text(VIRTUAL[key]) or ''
        except Exception as exc:                   # noqa: BLE001
            return {'ok': False, 'error': '%s: %s' % (type(exc).__name__, str(exc)[:200])}
        return {'ok': True, 'name': key, 'body': body, 'product': '',
                'when_to_use': '', 'fmt': {}, 'check_items': [], 'refs': [], 'conflicts': []}
    try:
        from app.secretary import spec
        hit = spec.load(user_id, name=key)
    except Exception as exc:                       # noqa: BLE001
        return {'ok': False, 'error': '%s: %s' % (type(exc).__name__, str(exc)[:200])}
    if not hit:
        return {'ok': False, 'error': '没有这篇技能：%s。目录里的名字才算。' % name}
    it = hit.get('item') or {}
    return {
        'ok': True,
        'name': it.get('name'),
        'when_to_use': it.get('when_to_use') or '',
        'product': it.get('product') or '',
        'fmt': it.get('fmt') or {},
        'check_items': it.get('check_items') or [],
        'body': hit.get('body') or '',
        'refs': [r.get('name') for r in (hit.get('refs') or []) if r.get('resolved')],
        'conflicts': [c.get('name') for c in (hit.get('conflicts') or [])],
    }


def names(user_id: int) -> list[str]:
    try:
        from app.secretary import spec
        return [r['name'] for r in spec.menu_rows(user_id)]
    except Exception:                              # noqa: BLE001
        return []
