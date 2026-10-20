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
import os

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


# ── 默认知识库（2026-10-20 新增）─────────────────────────────
# 为什么要有它：原来的兜底是 `config.KB_IDS`（.env 里写死的 id 列表）。
# 但**自增 id 会漂移** —— 删库不复用、重建拿更大值，写死的配置就悬空了。
# 本项目就踩到了：.env 写着 `SECRETARY_KB_IDS=1,2`，实际库 1 是「秘书问答规则」
# （规则文档）、库 2 是**空库**，真正装了业务知识的库 3（82 条切片）不在范围里。
# 表现就是「检索测试能命中、问答却说什么都查不到」—— 而且**不报错**。
#
# 所以默认范围改成**按内容动态确定**：谁有切片谁是默认库。
# 顺序（优先级从高到低）：
#   ① 前端勾选的 kb_ids（已由路由层校验）
#   ② 库里 `qa_enabled=1` 的 active 库
#   ③ .env 的 SECRETARY_DEFAULT_KB_ID（显式钉死，建库后 id 稳定时用这个）
#   ④ 切片数最多的 active 库（动态兜底，不依赖任何写死的 id）
_DEFAULT_CACHE = {'ids': None, 'at': 0.0}
_DEFAULT_TTL = 60.0          # 秒：建库/删库后最多 1 分钟生效，不必重启


def default_kb_ids(force: bool = False):
    """当前应当默认检索的知识库 id 列表（按上面 ①④ 的规则，带 60s 缓存）。

    任何异常都返回空列表 —— 宁可让上层显式报「没配知识库」，
    也不要抛异常把整轮问答带崩。
    """
    import time as _t

    if not force and _DEFAULT_CACHE['ids'] is not None \
            and (_t.time() - _DEFAULT_CACHE['at']) < _DEFAULT_TTL:
        return list(_DEFAULT_CACHE['ids'])
    try:
        from sqlalchemy import func, select

        from app.core.database import SessionLocal
        from app.models import DocumentChunk, KnowledgeBase

        db = SessionLocal()
        try:
            # ② qa_enabled 标记
            marked = [int(x) for x in db.execute(
                select(KnowledgeBase.id).where(KnowledgeBase.status == 'active',
                                               KnowledgeBase.qa_enabled.is_(True))
            ).scalars()]
            if marked:
                ids = marked
            else:
                # ③ 显式钉死
                pinned = (os.environ.get('SECRETARY_DEFAULT_KB_ID')
                          or config.ENV.get('SECRETARY_DEFAULT_KB_ID') or '').strip()
                ids = []
                if pinned:
                    try:
                        pid = int(pinned)
                    except ValueError:
                        pid = 0
                    if pid and db.get(KnowledgeBase, pid) is not None:
                        ids = [pid]
                # ④ 所有「有切片」的 active 库
                #    ⚠️ 不是只挑最大的那一个：知识天然分散在多个库。
                #    本项目实测：kb=1 装「考勤管理制度（含第四章请假流程）」、
                #    kb=3 装「信息安全管理制度」。只查 kb=3 时问「请假流程」，
                #    检索返回的却是信息安全制度（rerank 0.07，几乎不相关），
                #    模型只能如实说「系统里没有请假流程」—— 用户看到的就是
                #    「知识库检索不用于回答」。所以默认范围要覆盖**所有有内容的库**。
                if not ids:
                    rows = db.execute(
                        select(DocumentChunk.kb_id)
                        .join(KnowledgeBase, KnowledgeBase.id == DocumentChunk.kb_id)
                        .where(KnowledgeBase.status == 'active')
                        .group_by(DocumentChunk.kb_id)
                        .order_by(func.count(DocumentChunk.id).desc())
                    ).all()
                    ids = [int(r[0]) for r in rows]
        finally:
            db.close()
    except Exception as e:                          # noqa: BLE001
        print('[kb] 解析默认知识库失败：%s: %s' % (type(e).__name__, e), flush=True)
        ids = []
    if not ids:
        # 最后一层：仍然沿用 .env 的写死值（可能为空）
        ids = list(config.KB_IDS or [])
    _DEFAULT_CACHE['ids'] = list(ids)
    _DEFAULT_CACHE['at'] = _t.time()
    return list(ids)


def reset_default_cache() -> None:
    """建库/删库/改 qa_enabled 后调一次，让默认范围立刻重算（不用等 60s）。"""
    _DEFAULT_CACHE['ids'] = None
    _DEFAULT_CACHE['at'] = 0.0


def current_actor_key() -> str:
    """当前请求的身份标识，**专供缓存分桶**（不同用户不共享检索缓存）。

    ⚠️ 为什么需要它（2026-10-16 修的真实缺陷）
    ------------------------------------------------
    `tools_kb.kb_search` 原来的缓存键只含 (query, top_k, kb_ids, limit)、
    **不含身份**；而本模块的检索是**按登录身份做权限过滤**的
    （见下方 `search()`：actor → visible_document_filter）。
    两者一叠加 ⇒ 用户 A 检索到的**私有切片**会被缓存、原样返回给用户 B。
    把本函数的返回值加进缓存键，即可按用户隔离。

    无登录身份时统一返回 "service"：兜底身份只能看公开文档，共享缓存是安全的。
    """
    actor = _actor_var.get()
    if actor is None:
        return "service"
    ident = getattr(actor, "user_id", None)
    if ident is None:
        ident = getattr(actor, "username", None)
    return "%s:%s" % (getattr(actor, "type", "?"), ident)


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
    # 检索范围优先级：显式参数 > 本次请求上下文 > 动态默认库 > .env 写死值
    # 最后一层改成 default_kb_ids()：写死的 id 会因删库/重建漂移（见上方注释），
    # 动态默认至少保证「检索的是真有内容的那个库」。
    effective_kb_ids = list(kb_ids or _scope_var.get() or default_kb_ids())
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
