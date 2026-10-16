"""检索与问答接口。

⚠️ 权限条件由后端强制注入（services/permission），
   调用方**无法**通过参数绕过。
"""

from __future__ import annotations

import json
from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import CurrentActor, get_current_actor, require_permission
from app.providers import Message, get_providers
from app.schemas.search import (
    ChatRequest,
    ChatResponse,
    Citation,
    SearchRequest,
    SearchResponse,
)
from app.services import audit
from app.services.retrieval.search import search as run_search

router = APIRouter(tags=["检索与问答"])


@router.post(
    "/search",
    response_model=SearchResponse,
    summary="检索片段",
    dependencies=[Depends(require_permission("search:query"))],
)
def search(
    payload: SearchRequest,
    request: Request,
    actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    try:
        result = run_search(
            db,
            actor=actor,
            query=payload.query,
            kb_ids=payload.kb_ids,
            top_k=payload.top_k,
            recall_k=payload.recall_k,
            tag_filters=payload.tag_filters,
            use_rerank=payload.rerank,
            include_conflict=payload.include_conflict,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "EMBEDDING_FAILED", "message": str(exc)},
        ) from exc

    # ⚠️ 审计必须记录命中的文档 ID，否则无法追溯数据流向
    audit.log_search(
        db,
        actor=actor,
        query=payload.query,
        hit_doc_ids=[r.doc_id for r in result.results],
        kb_ids=payload.kb_ids,
        result_count=result.total,
        filters=payload.tag_filters,
        took_ms=result.took_ms,
        request=request,
    )
    db.commit()

    return result


SYSTEM_PROMPT = """你是一名企业内部知识库助手。请严格依据提供的资料回答问题。

要求：
1. **只使用**下列资料中的信息作答，不得编造。
2. 每个关键结论后必须标注来源编号，如 [1]、[2]。
3. 如果资料不足以回答问题，直接说明「现有资料无法回答该问题」，不要推测。
4. 如果多个来源存在冲突，优先采用**生效日期更新**的来源，并在回答中说明你采用了哪一份、忽略了哪一份及其原因。
5. 回答使用简体中文，条理清晰。"""


def _build_context(citations: list[Citation]) -> str:
    blocks: list[str] = []
    for c in citations:
        header = f"【片段{c.index}】来源：{c.doc_name}"
        if c.version_no > 1:
            header += f"（第 {c.version_no} 版）"
        if c.effective_date:
            header += f" 生效日期：{c.effective_date}"
        if c.heading_path:
            header += f" 章节：{c.heading_path}"
        if c.has_conflict:
            header += " ⚠️该来源存在未处理的冲突"
        # ⭐ parent-child：优先用父块上下文（同章节聚合，更完整），
        #    无父块（单切片章节）时回退到小块本身（snippet）。
        context_text = c.parent_content or c.snippet
        blocks.append(f"{header}\n{context_text}")
    return "\n\n".join(blocks)


@router.post(
    "/chat",
    summary="问答（RAG，流式）",
    dependencies=[Depends(require_permission("search:query"))],
)
def chat(
    payload: ChatRequest,
    request: Request,
    actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    """检索 + 生成。默认以 SSE 流式返回。"""
    import time

    t0 = time.perf_counter()
    try:
        result = run_search(
            db,
            actor=actor,
            query=payload.query,
            kb_ids=payload.kb_ids,
            top_k=payload.top_k,
            tag_filters=payload.tag_filters,
            use_rerank=payload.use_rerank,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503, detail={"code": "EMBEDDING_FAILED", "message": str(exc)}
        ) from exc

    retrieval_ms = int((time.perf_counter() - t0) * 1000)

    citations = [
        Citation(
            index=i + 1,
            chunk_id=r.chunk_id,
            doc_id=r.doc_id,
            doc_name=r.doc_name,
            version_no=r.version_no,
            heading_path=r.heading_path,
            snippet=r.content[:800],
            parent_content=r.parent_content,
            has_conflict=r.has_conflict,
        )
        for i, r in enumerate(result.results)
    ]

    audit.log_search(
        db,
        actor=actor,
        query=payload.query,
        hit_doc_ids=[r.doc_id for r in result.results],
        kb_ids=payload.kb_ids,
        result_count=result.total,
        filters=payload.tag_filters,
        took_ms=retrieval_ms,
        request=request,
        is_chat=True,
    )
    db.commit()

    bundle = get_providers()
    context = _build_context(citations)
    messages = [
        Message(role="system", content=SYSTEM_PROMPT),
        Message(role="user", content=f"资料：\n{context}\n\n问题：{payload.query}"),
    ]

    if not payload.stream:
        t1 = time.perf_counter()
        try:
            answer = bundle.llm.chat(
                messages, temperature=payload.temperature, max_tokens=2000
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=503,
                detail={"code": "LLM_FAILED", "message": f"生成失败：{exc}"},
            ) from exc
        return ChatResponse(
            answer=answer,
            citations=citations,
            retrieval_took_ms=retrieval_ms,
            generation_took_ms=int((time.perf_counter() - t1) * 1000),
            model=bundle.llm.model_id,
            filter_notice=result.filter_notice,
        )

    def event_stream() -> Iterator[str]:
        # 先推送引用信息
        yield "event: citations\ndata: " + json.dumps(
            [c.model_dump() for c in citations], ensure_ascii=False
        ) + "\n\n"
        try:
            for delta in bundle.llm.chat_stream(
                messages, temperature=payload.temperature, max_tokens=2000
            ):
                yield "event: delta\ndata: " + json.dumps(
                    {"content": delta}, ensure_ascii=False
                ) + "\n\n"
        except Exception as exc:  # noqa: BLE001
            yield "event: error\ndata: " + json.dumps(
                {"message": f"生成失败：{exc}"}, ensure_ascii=False
            ) + "\n\n"
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
