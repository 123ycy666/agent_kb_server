# -*- coding: utf-8 -*-
"""知识检索：**只取切片，不取知识库生成的答案**。

2026-10-13 起底层从「阿里云百炼检索服务」换成「自建知识库」的检索服务；
2026-10-15 单进程合并后，HTTP 调用再改为**进程内直调**（kb_adapter.py）。
★ **对外返回结构保持不变**，所以 agent.py（kb_search 工具 + rule_slices 规则检索）
一行都不用改。

返回结构（沿用老契约）：
    {'total': int, 'nodes': [{'score','doc_name','title','content'}, ...], 'cached': bool}
"""
from app.secretary import config
from app.secretary import kb_adapter

_CACHE = {}
_CACHE_MAX = 200


def kb_search(query, top_k=3, kb_ids=None, limit=450):
    # 检索范围优先级：显式参数 > 本次请求上下文（问答页选中的库）> 配置默认
    kb_ids = kb_ids or kb_adapter.current_scope() or config.KB_IDS
    # 缓存键含 kb_ids —— 换库不会命中上一次的结果
    ck = (str(query), int(top_k), tuple(kb_ids or []), int(limit))
    if ck in _CACHE:
        hit = dict(_CACHE[ck])
        hit['cached'] = True
        return hit

    try:
        d = kb_adapter.search(query, kb_ids=kb_ids, top_k=top_k, rerank=True)
    except Exception as e:                       # noqa: BLE001  检索失败 → 按「没查到」兜底
        return {'error': '%s: %s' % (type(e).__name__, str(e)[:200]), 'nodes': [], 'total': 0}

    nodes = []
    for h in (d.get('results') or [])[:top_k]:
        # ★ 优先用 parent_content（同章节父块，信息更全），父块为空再退回切片本身。
        #   两者都**已带【标题路径】前缀**，可直接喂给模型。
        body = (h.get('parent_content') or '').strip() or (h.get('content') or '')
        score = h.get('rerank_score')
        if score is None:                         # 没配 rerank 时会降级，用融合分
            score = h.get('fused_score')
        nodes.append({
            'score': round(float(score or 0), 4),
            'doc_name': h.get('doc_name') or '',
            'title': (h.get('heading_path') or '')[:120],
            'content': body[:limit],
        })

    out = {'total': d.get('total'), 'nodes': nodes}
    if d.get('degraded'):
        # 知识库降级（如未配 rerank）只是提示，不影响取数
        out['degraded'] = True
        out['degraded_reason'] = d.get('degraded_reason') or ''
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.clear()
    _CACHE[ck] = out
    return out
