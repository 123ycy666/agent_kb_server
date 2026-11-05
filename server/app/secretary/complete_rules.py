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
    """保存整张表。items = [{id?, kind, match_text, fill_text, priority}]

    带 id 的行原地更新，新行插入，页面没提交的行**软删**（enabled=false，可恢复；
    2026-11-05 Fred）—— 原来整表 DELETE，误删就找不回来了。

    ★ 2026-11-02 真库实测修的一次**数据丢失**：原来是「先 DELETE 全表、再逐行 INSERT」+
      autocommit —— priority 传了非数字时 `int()` 抛异常，而 DELETE 已经提交：
      **整张补全表被清空**；页面还会因为失败路径没清缓存继续显示旧行（看着没事，实际空了）。
      现在：① 参数先全部算好；② 再开事务整体提交；失败 rollback，一行不少。
    """
    items = [x for x in (items or []) if str(x.get('match_text') or '').strip()
             and str(x.get('fill_text') or '').strip()]
    try:
        args = [((int(x['id']) if x.get('id') else None),
                 str(x.get('kind') or 'norm')[:16], str(x.get('match_text'))[:200],
                 str(x.get('fill_text'))[:300],
                 int(x.get('priority') if x.get('priority') is not None else (i + 1) * 10))
                for i, x in enumerate(items)]
    except Exception as exc:                       # noqa: BLE001 - 类型不对就别碰库
        return {'error': '内容格式不对（未写入任何数据）：%s' % str(exc)[:160]}
    conn = None
    try:
        conn = pgdb.connect(autocommit=False)
        cur = conn.cursor()
        try:
            keep = []
            for rid, kind, match, fill, pri in args:
                if rid:
                    cur.execute(
                        'UPDATE complete_rule SET kind=%s, match_text=%s, fill_text=%s, '
                        'priority=%s, enabled=true, updated_at=now() WHERE id=%s',
                        (kind, match, fill, pri, rid))
                    if cur.rowcount:
                        keep.append(rid)
                        continue
                cur.execute(
                    'INSERT INTO complete_rule (kind, match_text, fill_text, priority, '
                    'enabled) VALUES (%s,%s,%s,%s,true) RETURNING id',
                    (kind, match, fill, pri))
                # ⚠️ pgdb 的 execute 把 RETURNING 那行吃掉存进 lastrowid，fetchone() 是 None。
                keep.append(int(cur.lastrowid or 0))
            cur.execute('UPDATE complete_rule SET enabled=false, updated_at=now() '
                        'WHERE NOT (id = ANY(%s))', (keep or [0],))
        finally:
            cur.close()
        conn.commit()
        clear_cache()
        return {'ok': True, 'rows': len(args)}
    except Exception as exc:                       # noqa: BLE001
        try:
            if conn is not None:
                conn.rollback()                    # ★ 回滚：原表一行不少
        except Exception:                          # noqa: BLE001
            pass
        print('[complete_rules] 保存失败（已回滚，原表未动）：%s: %s'
              % (type(exc).__name__, str(exc)[:200]), flush=True)
        return {'error': '保存失败（已回滚，原表未动）：%s' % str(exc)[:200]}
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:                          # noqa: BLE001
            pass


def deleted() -> list[dict]:
    """已软删的条目（页面「已删除」区用）。"""
    sql = ('SELECT id, kind, match_text, fill_text, priority FROM complete_rule '
           'WHERE NOT enabled ORDER BY priority, id')
    try:
        conn = pgdb.connect(autocommit=True)
        try:
            cur = conn.cursor()
            try:
                cur.execute(sql)
                return [dict(r) for r in (cur.fetchall() or [])]
            finally:
                cur.close()
        finally:
            try:
                conn.close()
            except Exception:                      # noqa: BLE001
                pass
    except Exception as exc:                       # noqa: BLE001
        print('[complete_rules] 读已删除失败：%s: %s' % (type(exc).__name__, str(exc)[:120]),
              flush=True)
        return []


def restore(rid: int) -> dict:
    """恢复一条软删的补全条目。"""
    try:
        conn = pgdb.connect(autocommit=True)
        try:
            cur = conn.cursor()
            try:
                cur.execute('UPDATE complete_rule SET enabled=true, updated_at=now() '
                            'WHERE id=%s AND NOT enabled', (int(rid),))
                if not cur.rowcount:
                    return {'error': '没有可恢复的条目：%s' % rid}
                clear_cache()
                return {'ok': True, 'id': int(rid), 'restored': True}
            finally:
                cur.close()
        finally:
            try:
                conn.close()
            except Exception:                      # noqa: BLE001
                pass
    except Exception as exc:                       # noqa: BLE001
        return {'error': '恢复失败：%s' % str(exc)[:200]}
