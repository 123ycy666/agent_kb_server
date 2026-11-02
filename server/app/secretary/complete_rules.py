# -*- coding: utf-8 -*-
"""补全表（`complete_rule`）：这一步回答"缺什么、补成什么口径"（2026-11-01）。

两类行：
    norm  归一：口语问法 → 标准口径（「情况怎么样」→ 六项指标 + 预算执行）
    fill  补缺：只有对象没问法 → 补默认问法；只有问法没对象 → 沿用上一轮

这张表**不进提示词**：预处理阶段由代码读它、直接补问题，模型只拿到补完的结果。
读失败一律返回空，不许让问答崩。
"""
from __future__ import annotations

import time

from app.secretary import pgdb

_TTL = 60.0
_CACHE: dict = {'at': 0.0, 'rows': None}


def clear_cache() -> None:
    _CACHE['rows'] = None
    _CACHE['at'] = 0.0


def rows() -> list[dict]:
    now = time.time()
    if _CACHE['rows'] is not None and (now - _CACHE['at']) < _TTL:
        return _CACHE['rows']
    sql = ('SELECT id, kind, match_text, fill_text, priority, enabled '
           'FROM complete_rule WHERE enabled ORDER BY priority, id')
    try:
        conn = pgdb.connect(autocommit=True)
        try:
            cur = conn.cursor()
            try:
                cur.execute(sql)
                out = [dict(r) for r in (cur.fetchall() or [])]
            finally:
                cur.close()
        finally:
            try:
                conn.close()
            except Exception:                      # noqa: BLE001
                pass
    except Exception as exc:                       # noqa: BLE001
        print('[complete_rules] 读表失败（按空处理）：%s: %s'
              % (type(exc).__name__, str(exc)[:120]), flush=True)
        return _CACHE['rows'] or []
    _CACHE['rows'] = out
    _CACHE['at'] = now
    return out


def as_text() -> str:
    """给页面看的表格文本（也是这一步"看的是什么"的回答）。"""
    data = rows()
    if not data:
        return ('【补全表】（还没配）\n'
                '· 归一：口语问法 → 标准口径\n'
                '· 补缺：只有对象没问法 → 补默认问法')
    lines = ['【补全表】（代码读，不进提示词）', '类型 ｜ 匹配 ｜ 补成什么']
    for r in data:
        kind = '归一' if str(r.get('kind')) == 'norm' else '补缺'
        lines.append('%s ｜ %s ｜ %s' % (kind, r.get('match_text'), r.get('fill_text')))
    return '\n'.join(lines)


def save(items: list[dict]) -> dict:
    """整表覆盖保存。items = [{kind, match_text, fill_text, priority}]"""
    items = [x for x in (items or []) if str(x.get('match_text') or '').strip()
             and str(x.get('fill_text') or '').strip()]
    try:
        conn = pgdb.connect(autocommit=True)
        try:
            cur = conn.cursor()
            try:
                cur.execute('DELETE FROM complete_rule')
                for i, x in enumerate(items):
                    cur.execute(
                        'INSERT INTO complete_rule (kind, match_text, fill_text, priority, enabled) '
                        'VALUES (%s,%s,%s,%s,true)',
                        (str(x.get('kind') or 'norm')[:16], str(x.get('match_text'))[:200],
                         str(x.get('fill_text'))[:300],
                         int(x.get('priority') if x.get('priority') is not None else (i + 1) * 10)))
                clear_cache()
                return {'ok': True, 'rows': len(items)}
            finally:
                cur.close()
        finally:
            try:
                conn.close()
            except Exception:                      # noqa: BLE001
                pass
    except Exception as exc:                       # noqa: BLE001
        print('[complete_rules] 保存失败：%s: %s'
              % (type(exc).__name__, str(exc)[:200]), flush=True)
        return {'error': '保存失败：%s' % str(exc)[:200]}
