# -*- coding: utf-8 -*-
"""规则文档（`document.doc_type='rule'`）的取用：主题目录 + 按主题精确取。

为什么要它（2026-10-30）
------------------------
规则原先靠 `agent.rule_slices()` 拿问题去**向量检索**《判据规则》切片，只保留 4 片。
线上实测那 4 片的相似度在 **0.17~0.19** —— 等于"字面沾边"，是不是该用的那条规则全看运气。

改成两步，和"表目录"一个套路：
- **主题目录常驻**（一行一个主题，约 800 字），让模型知道"有哪些规则可查"；
- **`get_rule(主题)` 精确取**：按主题名/别名匹配，命中就给，命中不到就明确说
  「没有这条规则」并给出最接近的几个主题 —— 不再返回一堆低分噪声。

规则原文仍然只有一处：知识库里 `doc_type='rule'` 的文档。
"""
from __future__ import annotations

import time

from app.secretary import pgdb

MAX_TOPICS = 40          # 目录上限（超出按文档顺序截断）
MAX_TEXT = 4000          # 单次 get 返回的正文上限
_TTL = 60.0              # 秒：规则改动最多 1 分钟生效，不必重启
_CACHE: dict = {'at': 0.0, 'rows': None}


def _conn():
    return pgdb.connect(autocommit=True)


def _rows(force: bool = False) -> list[dict]:
    """规则文档的全部切片（带 60 秒缓存）。

    读库失败时沿用旧缓存、没有就返回空 —— 规则读不到**不许**让问答崩，
    由调用方按"没有规则"处理（响应里标 degraded）。
    """
    now = time.time()
    if not force and _CACHE['rows'] is not None and (now - _CACHE['at']) < _TTL:
        return _CACHE['rows']
    sql = (
        'SELECT d.filename, COALESCE(c.heading_path, %s) AS hp, c.content, d.id AS doc_id '
        'FROM document_chunk c JOIN document d ON d.id = c.doc_id '
        'WHERE d.is_active AND c.is_retrievable AND d.doc_type = %s '
        'ORDER BY d.id, c.chunk_index'
    )
    try:
        conn = _conn()
        try:
            cur = conn.cursor()
            try:
                cur.execute(sql, ('', 'rule'))
                rows = cur.fetchall() or []
            finally:
                cur.close()
        finally:
            try:
                conn.close()
            except Exception:                      # noqa: BLE001
                pass
    except Exception as exc:                       # noqa: BLE001
        print('[rule_doc] 读规则文档失败（按无规则继续）：%s: %s'
              % (type(exc).__name__, str(exc)[:120]), flush=True)
        return _CACHE['rows'] or []
    _CACHE['rows'] = rows
    _CACHE['at'] = now
    return rows


def _val(row, key: str, idx: int = 0):
    return row.get(key) if isinstance(row, dict) else row[idx]


def _leaf(hp) -> str:
    """标题路径的最后一段：「判据规则 > 八、审批停留」→「八、审批停留」。"""
    parts = [p.strip() for p in str(hp or '').split('>') if p.strip()]
    return parts[-1] if parts else ''


def _text_of(row) -> str:
    raw = str(_val(row, 'content', 2) or '')
    # 去掉入库时加的【章节】前缀，避免目录里每行都重复一遍
    if raw.startswith('【'):
        nl = raw.find('】')
        if 0 < nl < 200:
            raw = raw[nl + 1:].lstrip('\n')
    return raw.strip()


def topics(force: bool = False) -> list[dict]:
    """规则主题目录：[{'topic','source','hint'}]（按文档顺序，去重）。"""
    seen: dict[str, dict] = {}
    for row in _rows(force=force):
        leaf = _leaf(_val(row, 'hp', 1))
        fname = str(_val(row, 'filename', 0) or '')
        topic = leaf or fname.rsplit('.', 1)[0]
        if not topic or topic in seen:
            continue
        body = _text_of(row).replace('\n', ' ')
        seen[topic] = {'topic': topic, 'source': fname,
                       'hint': (body[:26] + '…') if len(body) > 26 else body}
        if len(seen) >= MAX_TOPICS:
            break
    return list(seen.values())


def menu(force: bool = False) -> str:
    """目录文本（给 system 常驻用），一行一个主题。"""
    items = topics(force=force)
    if not items:
        return ''
    lines = ['【规则目录（公司规则原文，按主题精确查；要判定/下结论前先 get_rule）】']
    for it in items:
        lines.append('- %s ｜ %s' % (it['topic'], it['hint']))
    return '\n'.join(lines)


def get(topic: str = '', key: str = '', force: bool = False) -> dict:
    """按主题（或别名）取规则正文。

    返回 {'topic','source','text'}；取不到时返回 {'error','closest':[...]}，
    **不返回近似内容** —— 规则宁缺毋滥，凑数的规则比没有更危险。
    """
    want = str(topic or key or '').strip()
    if not want:
        return {'error': '缺少 topic（要查哪条规则）',
                'topics': [t['topic'] for t in topics(force=force)][:10]}
    rows = _rows(force=force)
    hits: list[dict] = []
    for row in rows:
        leaf = _leaf(_val(row, 'hp', 1))
        fname = str(_val(row, 'filename', 0) or '')
        label = leaf or fname.rsplit('.', 1)[0]
        if want in label or label in want or want in fname:
            hits.append(row)
    if not hits:
        all_topics = [t['topic'] for t in topics(force=force)]
        head = want[:2]
        closest = [t for t in all_topics if head and head in t][:3] or all_topics[:3]
        return {'error': '没有这条规则：%s' % want, 'closest': closest}
    parts: list[str] = []
    used = 0
    for row in hits:
        body = _text_of(row)
        if not body:
            continue
        if used + len(body) > MAX_TEXT:
            body = body[: max(0, MAX_TEXT - used)]
        parts.append(body)
        used += len(body)
        if used >= MAX_TEXT:
            break
    return {'topic': want, 'source': str(_val(hits[0], 'filename', 0) or ''),
            'text': '\n'.join(parts)}
