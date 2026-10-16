# -*- coding: utf-8 -*-
"""进程内知识检索 —— 替代原 kb_client 的 HTTP 调用。

2026-10-15 单进程合并：agent 与知识库同进程，检索直接调检索服务层，
一次性拆掉三个「缝合点」：HTTP 往返、内部服务令牌、跨端口。

对外契约与 kb_client.search **完全一致**（返回 dict），
所以 tools_kb.py 除 import 一行外零改动。

身份：问答入口（FastAPI 路由）设置当前登录用户，
检索的可见范围随之变化（公开 + 自己上传的），与 Web 界面口径一致；
无登录身份时兜底为服务身份（只能看公开文档）。
"""
from contextvars import ContextVar

from app.secretary import config

# 当前请求的登录身份（由路由层 set，检索时取用）
_actor_var: ContextVar = ContextVar("kb_actor", default=None)

# 当前请求的检索范围（知识库 id 列表；None = 用配置默认）
#
# 为什么用上下文而不是函数参数层层传：
#   kb_search 是**模型通过 function calling 自主调用**的工具，调用点不在
#   我们的代码栈里，没法从路由层把参数传进去。所以和登录身份一样，
#   用 ContextVar 登记一次，工具真正执行时自己来取。
_scope_var: ContextVar = ContextVar("kb_scope", default=None)


def set_actor(actor) -> None:
    """问答入口调用：登记当前登录用户。"""
    _actor_var.set(actor)


def set_scope(kb_ids) -> None:
    """问答入口调用：登记本次请求要检索的知识库范围。

    :param kb_ids: 知识库 id 列表；传 None/空表示「用配置默认」
                   （当前实现：库里 `qa_enabled=true` 的知识库，
                     由路由层查好后传入；无则退回 config.KB_IDS）
    """
    _scope_var.set([int(x) for x in kb_ids] if kb_ids else None)


def current_scope():
    """取当前请求的检索范围（None 表示未指定，调用方用默认）。"""
    return _scope_var.get()


def _fallback_actor():
    """无登录身份（离线脚本 / 内部任务）时的兜底服务身份。"""
    from app.core.deps import CurrentActor

    return CurrentActor(
        type="service",
        user_id=None,
        username="service:secretary",
        display_name="问数服务（无登录身份）",
        role_ids=[],
        role_codes=["service"],
        permissions={"search:query"},
    )


def search(query, kb_ids=None, top_k=5, rerank=True, tag_filters=None,
           include_conflict=False, timeout=None):
    """检索知识库，返回与 kb_client.search 相同的 dict 结构。

    任何失败都抛异常（由 tools_kb 兜底成「没查到」）——
    不静默吞掉，保证 /health 与日志能看到真实原因。
    """
    from app.core.database import SessionLocal
    from app.services.retrieval.search import search as _search

    actor = _actor_var.get() or _fallback_actor()
    # 检索范围优先级：显式参数 > 本次请求上下文 > 配置默认
    effective_kb_ids = list(kb_ids or _scope_var.get() or config.KB_IDS)
    db = SessionLocal()
    try:
        resp = _search(
            db,
            actor=actor,
            query=query,
            kb_ids=effective_kb_ids,
            top_k=int(top_k),
            use_rerank=bool(rerank),
            include_conflict=bool(include_conflict),
            tag_filters=tag_filters,
        )
    finally:
        db.close()

    results = []
    for it in resp.results:
        results.append({
            'content': it.content,
            'parent_content': it.parent_content,
            'doc_id': it.doc_id,
            'doc_name': it.doc_name,
            'heading_path': it.heading_path,
            'has_conflict': it.has_conflict,
            'rerank_score': it.rerank_score,
            'fused_score': it.fused_score,
        })
    return {
        'results': results,
        'total': resp.total,
        'degraded': resp.degraded,
        'degraded_reason': resp.degraded_reason,
    }


def health(timeout=8):
    """健康检查：真实打一次最小检索（比「连得上」更贴近实际能力）。"""
    try:
        d = search('健康检查', top_k=1)
        return {'available': True, 'total': d.get('total', 0)}
    except Exception as e:                     # noqa: BLE001
        return {'available': False, 'reason': '%s: %s' % (type(e).__name__, str(e)[:200])}
