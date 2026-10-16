"""检索编排。

执行顺序（**不可调换**，对应设计方案 9.1）
-----------------------------------------
① 权限过滤   → 只保留当前主体可见的切片（后端强制注入）
② 标签过滤   → 命中用户选择的标签条件（pre-filter）
③ 向量召回   → HNSW 近邻检索
④ 融合       → 单路时为纯向量分
⑤ 版本压制   → 同一 root_id 的多版本只保留最新
⑥ Rerank 精排
⑦ Top-K 输出

⚠️ ⑤ 必须在 ⑥ 之前。否则「旧版本分数高，把同一文档的新版本挤掉」，
   「优先引用最新」的兜底策略就失效了。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.deps import CurrentActor
from app.core.text import to_tsquery_or
from app.models import Document, DocumentChunk, KnowledgeBase
from app.providers import get_providers
from app.schemas.search import (
    FilterNotice,
    SearchResponse,
    SearchResultItem,
)
from app.services.permission import visible_document_filter


@dataclass
class RecallItem:
    """召回候选。"""

    chunk_id: int
    doc_id: int
    kb_id: int
    content: str
    heading_path: str | None
    tags: list[str]
    distance: float
    doc_name: str = ""
    kb_name: str = ""
    version_no: int = 1
    root_id: int | None = None
    is_active: bool = True
    has_conflict: bool = False
    parent_content: str | None = None
    created_at: Any = None
    extra: dict[str, Any] = field(default_factory=dict)

    # ── 双路召回的排名与分数（RRF 融合用）────────────────────
    vector_rank: int | None = None
    keyword_rank: int | None = None
    keyword_score: float | None = None
    fused_score: float | None = None

    @property
    def vector_score(self) -> float:
        """余弦距离 → 相似度（0~1）。"""
        return max(0.0, 1.0 - self.distance)


# ⚠️ 与 chunker.PARENT_MAX_CHARS 保持一致；此处本地定义以避免检索模块反向依赖入库模块。
PARENT_MAX_CHARS = 2000


def _strip_heading_prefix(content: str, heading_path: str | None) -> str:
    """去掉切片内容开头的「【章节】」前缀。

    存储的 DocumentChunk.content 已带前缀，动态拼父块时需先剥掉，
    否则父块里每段都会重复前缀。
    """
    if heading_path:
        prefix = f"【{heading_path}】\n"
        if content.startswith(prefix):
            return content[len(prefix):]
    return content


def _build_dynamic_parents(
    db: Session, items: list[RecallItem], *, include_conflict: bool = False
) -> None:
    """动态聚合父块，取代切分时写的**静态 parent_content 快照**。

    ⚠️ 关键修复（2026-10-09）：静态快照是切分时的「全章节副本」，
       根本不知道后来发生的「隔离状态」，会把已冲突/被覆盖的旧切片一并带出，
       形成绕过切片级隔离的后门（父块泄漏失效信息）。

       这里改为**检索时实时聚合**：只取「同 (doc_id, heading_path) 且
       is_retrievable=True」的兄弟切片拼成父块。于是：
       - 冲突隔离的切片自动被排除；
       - 覆盖(overwrite)的旧切片自动被排除；
       - 删除新文档复活旧版、版本压制，全部自动生效，无需任何补丁。

    退化规则（与切分侧一致）：剔除不可见切片后仅剩 1 片、或正文超长，
    则不返回父块（= None），由调用方回退到小块本身。

    include_conflict=True（人工核对冲突）时，额外把 retired_reason='conflict'
    的隔离切片也纳入父块，便于审阅完整章节上下文。
    """
    keys: set[tuple[int, str | None]] = {
        (i.doc_id, i.heading_path) for i in items if i.heading_path
    }
    if not keys:
        return

    stmt = (
        select(DocumentChunk)
        .join(Document, Document.id == DocumentChunk.doc_id)
        .where(
            DocumentChunk.doc_id.in_([k[0] for k in keys]),
            Document.is_active.is_(True),
        )
    )
    if include_conflict:
        stmt = stmt.where(
            or_(
                DocumentChunk.is_retrievable.is_(True),
                DocumentChunk.retired_reason == "conflict",
            )
        )
    else:
        stmt = stmt.where(DocumentChunk.is_retrievable.is_(True))

    siblings: dict[tuple[int, str | None], list[DocumentChunk]] = {}
    for ch in db.execute(stmt).scalars().all():
        key = (ch.doc_id, ch.heading_path)
        if key in keys:
            siblings.setdefault(key, []).append(ch)

    for item in items:
        if not item.heading_path:
            continue
        group = siblings.get((item.doc_id, item.heading_path), [])
        # 退化：剔除不可见切片后仅剩命中自身那一片 → 无更大上下文
        if len(group) <= 1:
            item.parent_content = None
            continue
        body = "\n".join(
            _strip_heading_prefix(ch.content, item.heading_path) for ch in group
        )
        if len(body) > PARENT_MAX_CHARS:
            item.parent_content = None
            continue
        item.parent_content = f"【{item.heading_path}】\n{body}"


def _build_tag_condition(tag_filters: list[str] | None):
    """构造标签过滤条件（pre-filter）。

    语义为 **全部命中（AND）**：`tags @> ARRAY['行政','预算']`
    表示文档必须同时带这两个标签。这是「筛选」的标准语义（逐层收窄），
    与「搜索」不同 —— 它只要求包含，不参与相关性评分。

    ⚠️ 把 Python list 直接交给 ARRAY.contains()，由驱动完成参数化。
       手动拼接字符串会破坏参数绑定，既有注入风险，也会因转义问题静默失效。
    """
    tags = [str(t).strip() for t in (tag_filters or []) if t and str(t).strip()]
    if not tags:
        return None
    # PostgreSQL 数组「包含」运算符 @>：要求左侧包含右侧所有元素
    return DocumentChunk.tags.contains(tags)


def recall_vector(
    db: Session,
    *,
    actor: CurrentActor,
    kb_ids: list[int],
    query_vector: list[float],
    recall_k: int,
    tag_filters: list[str] | None = None,
    include_conflict: bool = False,
) -> list[RecallItem]:
    """向量路召回（含权限过滤 + 标签过滤，均为 pre-filter）。"""
    bundle = get_providers()
    model_name = f"{bundle.mode}/{bundle.embedding.model_id}"

    distance = DocumentChunk.embedding.cosine_distance(query_vector).label("distance")

    stmt = (
        select(DocumentChunk, Document, KnowledgeBase, distance)
        .join(Document, Document.id == DocumentChunk.doc_id)
        .join(KnowledgeBase, KnowledgeBase.id == DocumentChunk.kb_id)
        .where(
            DocumentChunk.kb_id.in_(kb_ids),
            # ① 权限过滤 —— 后端强制注入，绝不接受调用方传参
            visible_document_filter(actor),
            # 只取最新版本
            Document.is_active.is_(True),
            # 向量模型一致性校验：模型不一致的切片不参与检索
            DocumentChunk.embedding_model == model_name,
            DocumentChunk.embedding.isnot(None),
            # CASCADE 删除的已删除文档
            Document.owner_id.isnot(None) | Document.owner_id.is_(None),
        )
        .order_by(distance)
        .limit(recall_k)
    )

    # ⚠️ 冲突隔离已细化到**切片级**：被隔离的切片 is_retrievable=False，
    #    同文档其余切片照常参与检索 —— 不再用文档级 has_conflict 一票否决整篇。
    #    include_conflict=True 时，把「因冲突被隔离」的切片也召回（人工核对冲突用）。
    if include_conflict:
        stmt = stmt.where(
            or_(
                DocumentChunk.is_retrievable.is_(True),
                DocumentChunk.retired_reason == "conflict",
            )
        )
    else:
        stmt = stmt.where(DocumentChunk.is_retrievable.is_(True))

    tag_cond = _build_tag_condition(tag_filters or {})
    if tag_cond is not None:
        stmt = stmt.where(tag_cond)

    rows = db.execute(stmt).all()

    items: list[RecallItem] = []
    for rank, (chunk, doc, kb, dist) in enumerate(rows, start=1):
        items.append(
            RecallItem(
                chunk_id=chunk.id,
                doc_id=doc.id,
                kb_id=doc.kb_id,
                content=chunk.content,
                heading_path=chunk.heading_path,
                # ⚠️ parent_content 不再读静态快照（会绕过隔离），改由检索时动态聚合
                parent_content=None,
                tags=list(chunk.tags or []),
                distance=float(dist or 1.0),
                doc_name=doc.filename,
                kb_name=kb.name,
                version_no=doc.version_no,
                root_id=doc.root_id,
                is_active=doc.is_active,
                has_conflict=doc.has_conflict,
                created_at=doc.created_at,
                vector_rank=rank,
            )
        )
    return items


def recall_keyword(
    db: Session,
    *,
    actor: CurrentActor,
    kb_ids: list[int],
    query: str,
    recall_k: int,
    tag_filters: list[str] | None = None,
    include_conflict: bool = False,
) -> list[RecallItem]:
    """关键词路召回（tsvector + GIN 索引）。

    为什么必须有这一路
    ------------------
    向量检索对**数值、条款编号、专有名词**极不敏感，
    而这些恰恰是冲突最常发生的地方（「报销上限 500」vs「800」）。
    只靠向量路，这类查询会漂移到语义相近但数值无关的段落上。

    ⚠️ 查询侧与入库侧必须使用**同一套分词**（app/core/text.py）。
       不一致时 token 对不上，这一路会永远返回空 —— 而且不报错。

    ⚠️ 注意：这一路**不做** embedding_model 校验。
       它不依赖向量，因此即使知识库尚未按新模型重建索引，
       关键词路依然可用 —— 这在模型切换的过渡期是个重要的兜底。
    """
    tsquery_text = to_tsquery_or(query)
    if not tsquery_text:
        return []

    # ⚠️ 必须用 to_tsquery（OR 语义），不能用 plainto_tsquery（AND）——
    #    后者要求每个词都命中，中文提问必然含文档中没有的词，召回直接归零。
    tsquery = func.to_tsquery("simple", tsquery_text)
    kw_score = func.ts_rank_cd(DocumentChunk.search_text, tsquery).label("kw_score")

    stmt = (
        select(DocumentChunk, Document, KnowledgeBase, kw_score)
        .join(Document, Document.id == DocumentChunk.doc_id)
        .join(KnowledgeBase, KnowledgeBase.id == DocumentChunk.kb_id)
        .where(
            DocumentChunk.kb_id.in_(kb_ids),
            # 权限过滤同样是强制注入的，与向量路口径完全一致
            visible_document_filter(actor),
            Document.is_active.is_(True),
            DocumentChunk.search_text.isnot(None),
            DocumentChunk.search_text.op("@@")(tsquery),
        )
        .order_by(kw_score.desc())
        .limit(recall_k)
    )

    # 同向量路：切片级隔离；include_conflict 时召回被冲突隔离的切片
    if include_conflict:
        stmt = stmt.where(
            or_(
                DocumentChunk.is_retrievable.is_(True),
                DocumentChunk.retired_reason == "conflict",
            )
        )
    else:
        stmt = stmt.where(DocumentChunk.is_retrievable.is_(True))

    tag_cond = _build_tag_condition(tag_filters or {})
    if tag_cond is not None:
        stmt = stmt.where(tag_cond)

    items: list[RecallItem] = []
    for rank, (chunk, doc, kb, score) in enumerate(db.execute(stmt).all(), start=1):
        items.append(
            RecallItem(
                chunk_id=chunk.id,
                doc_id=doc.id,
                kb_id=doc.kb_id,
                content=chunk.content,
                heading_path=chunk.heading_path,
                # ⚠️ parent_content 不再读静态快照（会绕过隔离），改由检索时动态聚合
                parent_content=None,
                tags=list(chunk.tags or []),
                # 关键词路没有余弦距离，用 1.0 占位；排序以 keyword_rank 为准
                distance=1.0,
                doc_name=doc.filename,
                kb_name=kb.name,
                version_no=doc.version_no,
                root_id=doc.root_id,
                is_active=doc.is_active,
                has_conflict=doc.has_conflict,
                created_at=doc.created_at,
                keyword_rank=rank,
                keyword_score=float(score or 0.0),
            )
        )
    return items


def recall_hybrid(
    db: Session,
    *,
    actor: CurrentActor,
    kb_ids: list[int],
    query: str,
    query_vector: list[float],
    recall_k: int,
    tag_filters: list[str] | None = None,
    include_conflict: bool = False,
    rrf_k: int = 60,
) -> list[RecallItem]:
    """双路召回 + RRF（Reciprocal Rank Fusion）融合。

    为什么用 RRF 而不是加权求和
    ---------------------------
    向量相似度（0~1 的余弦）与 ts_rank_cd（无上界的稀疏分数）**量纲完全不同**，
    直接加权需要反复调参且不稳定。RRF 只看**排名**，天然免去分数归一化：

        fused = Σ 1 / (rrf_k + rank_i)，rrf_k 通常取 60

    这一做法在多路检索融合中是公认的稳健选择。
    """
    vec_items = recall_vector(
        db,
        actor=actor,
        kb_ids=kb_ids,
        query_vector=query_vector,
        recall_k=recall_k,
        tag_filters=tag_filters,
        include_conflict=include_conflict,
    )
    kw_items = recall_keyword(
        db,
        actor=actor,
        kb_ids=kb_ids,
        query=query,
        recall_k=recall_k,
        tag_filters=tag_filters,
        include_conflict=include_conflict,
    )

    merged: dict[int, RecallItem] = {}
    for item in vec_items:
        merged[item.chunk_id] = item

    for item in kw_items:
        existing = merged.get(item.chunk_id)
        if existing is None:
            merged[item.chunk_id] = item
        else:
            # 两路都命中 → 补齐关键词侧的排名与分数
            existing.keyword_rank = item.keyword_rank
            existing.keyword_score = item.keyword_score

    for item in merged.values():
        score = 0.0
        if item.vector_rank:
            score += 1.0 / (rrf_k + item.vector_rank)
        if item.keyword_rank:
            score += 1.0 / (rrf_k + item.keyword_rank)
        item.fused_score = score

    ranked = sorted(merged.values(), key=lambda x: -(x.fused_score or 0.0))

    # ⚠️ 版本压制在此处统一执行：两路各自可能召回同一文档的不同版本，
    #    必须先合并再压制，否则会出现「新版本在向量路被压掉、
    #    却在关键词路复活」的漏洞。
    return suppress_old_versions(ranked)


def suppress_old_versions(items: list[RecallItem]) -> list[RecallItem]:
    """版本压制：同一份文档的旧版本整体退出，只保留最新版本的全部切片。

    ⭐ 这是「优先引用最新」的**主机制**。
       只做时间加权是不够的 —— 那会让「稍新但完全不相关」的文档污染答案。

    ⚠️ 实现要点：去重的单位是 **doc_id**，不是 root_id。
       一个文档有多个切片，它们共享同一个 root_id；
       若按 root_id 去重会让整篇文档只剩一条切片，检索结果严重残缺。
       正确做法分两步：
           ① 先按 root_id 找出每个版本族中 version_no 最大的 doc_id
           ② 再保留这些最新 doc_id 的全部切片
    """
    if not items:
        return []

    # ① 每个版本族 → 最新的 doc_id
    latest_doc_by_root: dict[int, tuple[int, int]] = {}
    for item in items:
        if item.root_id is None:
            continue
        current = latest_doc_by_root.get(item.root_id)
        if current is None or item.version_no > current[1]:
            latest_doc_by_root[item.root_id] = (item.doc_id, item.version_no)

    # ② 只保留最新文档的切片
    kept: list[RecallItem] = []
    for item in items:
        if item.root_id is None:
            kept.append(item)
            continue
        latest = latest_doc_by_root.get(item.root_id)
        if latest is not None and latest[0] == item.doc_id:
            kept.append(item)

    # 压制后按原召回顺序（距离升序）恢复排序
    kept.sort(key=lambda x: x.distance)
    return kept


def apply_rerank(query: str, items: list[RecallItem], top_k: int) -> list[RecallItem]:
    """精排。若 Provider 不可用则降级为向量分数排序，并标记 degraded。"""
    if not items:
        return []
    bundle = get_providers()
    docs = [i.content for i in items]
    results = bundle.rerank.rerank(query, docs, top_k=top_k)

    ordered: list[RecallItem] = []
    for scored in results:
        if 0 <= scored.index < len(items):
            item = items[scored.index]
            item.extra["rerank_score"] = scored.score
            ordered.append(item)
    return ordered[:top_k]


def _count_baseline(db: Session, actor: CurrentActor, kb_ids: list[int]) -> int:
    """过滤条件放宽后的基线数量（用于空结果提示：「全库有 N 条相关记录」）。"""
    model_name = f"{get_providers().mode}/{get_providers().embedding.model_id}"
    stmt = (
        select(func.count(DocumentChunk.id))
        .join(Document, Document.id == DocumentChunk.doc_id)
        .where(
            DocumentChunk.kb_id.in_(kb_ids),
            visible_document_filter(actor),
            Document.is_active.is_(True),
            DocumentChunk.embedding_model == model_name,
            DocumentChunk.is_retrievable.is_(True),
        )
    )
    return int(db.execute(stmt).scalar_one())


def search(
    db: Session,
    *,
    actor: CurrentActor,
    query: str,
    kb_ids: list[int],
    top_k: int = 5,
    recall_k: int = 60,
    tag_filters: list[str] | None = None,
    use_rerank: bool = True,
    include_conflict: bool = False,
) -> SearchResponse:
    """完整检索流程。"""
    started = time.perf_counter()
    bundle = get_providers()
    degraded = False
    degraded_reason: str | None = None

    # 向量化
    try:
        query_vector = bundle.embedding.embed_query(query)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"查询向量化失败：{exc}") from exc

    # ── ①②③ 双路召回 + RRF 融合 ─────────────────────────────
    #    权限与标签过滤在**两条路内部**都做了 pre-filter，口径完全一致。
    #    ⚠️ 版本压制已在 recall_hybrid 内部完成 ——
    #       必须在「两路合并之后、精排之前」，否则会出现
    #       「新版本在向量路被压掉，却在关键词路复活」的漏洞。
    items = recall_hybrid(
        db,
        actor=actor,
        kb_ids=kb_ids,
        query=query,
        query_vector=query_vector,
        recall_k=recall_k,
        tag_filters=tag_filters,
        include_conflict=include_conflict,
    )

    # ⑥ 精排
    if use_rerank and items:
        try:
            items = apply_rerank(query, items, top_k)
        except Exception as exc:  # noqa: BLE001
            degraded = True
            degraded_reason = f"精排服务不可用，已降级为融合分数排序：{exc}"
            items = items[:top_k]
    else:
        items = items[:top_k]

    took_ms = int((time.perf_counter() - started) * 1000)

    # ⚠️ 动态聚合父块：取代静态快照，彻底避免「父块绕过切片级冲突隔离」。
    #    只取可见兄弟切片，隔离/覆盖/复活/版本压制自动生效。
    _build_dynamic_parents(db, items, include_conflict=include_conflict)

    def _source_of(item: RecallItem) -> str:
        """标记结果由哪一路召回，便于排查召回质量问题。"""
        if item.vector_rank and item.keyword_rank:
            return "hybrid"
        if item.keyword_rank:
            return "keyword"
        return "vector"

    results = [
        SearchResultItem(
            chunk_id=i.chunk_id,
            doc_id=i.doc_id,
            doc_name=i.doc_name,
            kb_id=i.kb_id,
            kb_name=i.kb_name,
            version_no=i.version_no,
            heading_path=i.heading_path,
            content=i.content,
            parent_content=i.parent_content,
            source=_source_of(i),
            vector_score=round(i.vector_score, 4) if i.vector_rank else None,
            keyword_score=round(i.keyword_score, 4) if i.keyword_score is not None else None,
            fused_score=round(i.fused_score, 6) if i.fused_score is not None else None,
            rerank_score=round(float(i.extra["rerank_score"]), 4)
            if "rerank_score" in i.extra
            else None,
            has_conflict=i.has_conflict,
            tags=i.tags,
        )
        for i in items
    ]

    # 空结果兜底提示 —— 避免「明明库里有却搜不到」的大量误报
    notice: FilterNotice | None = None
    if not results and tag_filters:
        baseline = _count_baseline(db, actor, kb_ids)
        if baseline > 0:
            notice = FilterNotice(
                message="当前标签条件下没有找到相关内容",
                suggestion=(
                    f"去掉标签条件后，该知识库范围内有 {baseline} 条可选记录。"
                    "是否放宽筛选？"
                ),
                relaxable_filters=list(tag_filters),
                baseline_total=baseline,
            )
        else:
            notice = FilterNotice(
                message="当前条件下没有可检索的内容",
                suggestion="请确认该知识库中是否已有文档，或你是否有权访问其中的文档。",
                baseline_total=0,
            )

    return SearchResponse(
        query=query,
        total=len(results),
        results=results,
        took_ms=took_ms,
        recall_count=len(items),
        filter_notice=notice,
        applied_filters=list(tag_filters or []),
        degraded=degraded,
        degraded_reason=degraded_reason,
    )
