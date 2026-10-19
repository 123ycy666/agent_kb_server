"""模型配置与连接测试接口。

这是「Provider 抽象」在界面上的落点：
- 查看当前档位与生效模型
- 用临时凭据测试连接（**不写回配置文件**，便于边填边测）
- 切换档位（运行时生效，并同时写回 .env，重启后依然有效）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.config import persist_model_channel, settings
from app.core.database import get_db
from app.core.deps import CurrentActor, get_current_actor, require_permission
from app.providers import build_bundle, get_providers, reset_providers
from app.providers.factory import ProviderOverrides
from app.schemas.providers import (
    ModelUsageStats,
    ProviderConfigOut,
    ProviderInfo,
    ProviderTestRequest,
    ProviderTestResponse,
    ProviderTestResult,
    SwitchModeRequest,
)
from app.services import audit

router = APIRouter(prefix="/models", tags=["模型配置"])

MODE_LABELS = {
    "mock": "Mock 档位（零依赖，用于功能开发与演示）",
    "local": "本地模型档位（内网部署的 bge-m3 / bge-rerank-m3 / DeepSeek / 千问3.6）",
    "online": "线上 API 档位（阿里云百炼：text-embedding-v4 / gte-rerank-v2 / qwen3.8-flash）",
}


def _build_options() -> dict:
    """返回各档位可配置项（密钥一律脱敏）。"""
    s = settings
    return {
        "local": {
            "local_embedding_url": s.local_embedding_url,
            "local_embedding_model": s.local_embedding_model,
            "local_rerank_url": s.local_rerank_url,
            "local_rerank_model": s.local_rerank_model,
            "local_llm_base_url": s.local_llm_base_url,
            "local_llm_model": s.local_llm_model,
        },
        "online": {
            "online_embedding_base_url": s.online_embedding_base_url,
            "online_embedding_model": s.online_embedding_model,
            "online_rerank_base_url": s.online_rerank_base_url,
            "online_rerank_model": s.online_rerank_model,
            "online_llm_base_url": s.online_llm_base_url,
            "online_llm_model": s.online_llm_model,
        },
    }


@router.get(
    "/config",
    response_model=ProviderConfigOut,
    summary="当前模型配置总览",
)
def get_model_config(_: CurrentActor = Depends(get_current_actor)):
    bundle = get_providers()
    summary = settings.provider_summary()

    return ProviderConfigOut(
        mode=bundle.mode,
        mode_label=MODE_LABELS.get(bundle.mode, bundle.mode),
        embedding=ProviderInfo(
            kind="embedding",
            mode=bundle.mode,
            model_id=bundle.embedding.model_id,
            ready=True,
            detail=summary.get("embedding", ""),
        ),
        rerank=ProviderInfo(
            kind="rerank",
            mode=bundle.mode,
            model_id=bundle.rerank.model_id,
            ready=True,
            detail=summary.get("rerank", ""),
        ),
        llm=ProviderInfo(
            kind="llm",
            mode=bundle.mode,
            model_id=bundle.llm.model_id,
            ready=True,
            detail=summary.get("llm", ""),
        ),
        embedding_dim=bundle.embedding.dim,
        options=_build_options(),
        notice=(
            "当前为 Mock 档位，检索结果为确定性伪向量，仅用于验证链路连通性。"
            "如需真实效果，请切换到 local 档（配 LOCAL_* 内网地址）或 online 档（配 ONLINE_* API Key）。"
            if bundle.mode == "mock"
            else (
                "当前为线上 API 档位：测试连接与检索会产生真实调用费用，请按需使用。"
                if bundle.mode == "online"
                else ""
            )
        ),
    )


@router.post(
    "/test",
    response_model=ProviderTestResponse,
    summary="测试连接（可用临时凭据，不写回配置）",
)
def test_connection(
    payload: ProviderTestRequest,
    _: CurrentActor = Depends(get_current_actor),
):
    overrides = ProviderOverrides(
        local_embedding_url=payload.local_embedding_url or None,
        local_embedding_model=payload.local_embedding_model or None,
        local_rerank_url=payload.local_rerank_url or None,
        local_rerank_model=payload.local_rerank_model or None,
        local_llm_base_url=payload.local_llm_base_url or None,
        local_llm_model=payload.local_llm_model or None,
        local_llm_api_key=payload.local_llm_api_key or None,
        online_embedding_base_url=payload.online_embedding_base_url or None,
        online_embedding_model=payload.online_embedding_model or None,
        online_embedding_api_key=payload.online_embedding_api_key or None,
        online_rerank_base_url=payload.online_rerank_base_url or None,
        online_rerank_model=payload.online_rerank_model or None,
        online_rerank_api_key=payload.online_rerank_api_key or None,
        online_llm_base_url=payload.online_llm_base_url or None,
        online_llm_model=payload.online_llm_model or None,
        online_llm_api_key=payload.online_llm_api_key or None,
        embedding_dim=payload.embedding_dim,
    )

    bundle = build_bundle(payload.mode, overrides)

    targets = (
        ["embedding", "rerank", "llm"] if payload.target == "all" else [payload.target]
    )
    results: list[ProviderTestResult] = []

    for target in targets:
        if target == "embedding":
            health = bundle.embedding.health_check()
        elif target == "rerank":
            health = bundle.rerank.health_check()
        else:
            health = bundle.llm.health_check()
        results.append(ProviderTestResult.from_health(target, health))

    # 明确本次测试用的是哪一套凭据。
    # 用户最常见的困惑是「表单里填了显示成功、.env 却不生效」——
    # 根源往往是两者根本不是同一套凭据，把它标出来即可消除歧义。
    used_form = any(
        [
            payload.local_llm_api_key,
            payload.local_embedding_url,
            payload.online_llm_api_key,
            payload.online_embedding_base_url,
        ]
    )
    note = (
        "本次测试使用的是【表单里填写的临时凭据】，不会写回配置文件。"
        "若要让它长期生效，请把这些值写进 .env 并重启服务。"
        if used_form
        else "本次测试使用的是【.env / 环境变量中的凭据】。"
    )

    return ProviderTestResponse(
        mode=bundle.mode,
        results=results,
        all_ok=all(r.ok for r in results),
        credential_source="form" if used_form else "env",
        notice=note,
    )


@router.post(
    "/switch",
    response_model=ProviderConfigOut,
    summary="切换模型档位（同时写回 .env，重启后依然生效）",
    dependencies=[Depends(require_permission("system:config"))],
)
def switch_mode(
    payload: SwitchModeRequest,
    request: Request,
    actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    settings.model_channel = payload.mode
    reset_providers()

    # 写回 .env —— 避免「界面切了通道、重启后又变回去」的困惑。
    # 原来的行为只改内存，用户很难意识到重启会回退。
    persisted = persist_model_channel(payload.mode)

    audit.log_action(
        db,
        actor=actor,
        action=audit.AuditAction.INDEX_REBUILD,
        resource_type="system",
        resource_name="model_channel",
        detail={
            "action": "switch_model_channel",
            "mode": payload.mode,
            "persisted_to_env": persisted,
        },
        request=request,
        commit=True,
    )

    # ⚠️ 切换档位会换掉 embedding 模型，而不同模型的向量不在同一空间。
    #    旧切片不会被检索到 —— 而且**不报错**，用户只会看到「什么都搜不到」。
    #    这里主动统计并明确提示，把「静默故障」变成「可见提示」。
    from sqlalchemy import func, select

    from app.models import DocumentChunk, KnowledgeBase

    bundle = get_providers()
    new_model = f"{bundle.mode}/{bundle.embedding.model_id}"

    stale_rows = db.execute(
        select(DocumentChunk.kb_id, func.count(DocumentChunk.id))
        .where(DocumentChunk.embedding_model != new_model)
        .group_by(DocumentChunk.kb_id)
    ).all()

    out = get_model_config(actor)
    if stale_rows:
        total = sum(int(cnt) for _, cnt in stale_rows)
        names: list[str] = []
        for kb_id, _cnt in stale_rows[:5]:
            kb = db.get(KnowledgeBase, kb_id)
            names.append(kb.name if kb else f"#{kb_id}")
        suffix = " 等" if len(stale_rows) > 5 else ""
        out.notice = (
            f"⚠️ 已切换到 {payload.mode} 档位。检测到 {len(stale_rows)} 个知识库、"
            f"共 {total} 条切片的向量模型与新档位不一致"
            f"（涉及：{'、'.join(names)}{suffix}）。"
            "这些内容**当前检索不到**，请到「知识库」页点击 ⟳ 重建索引。"
            + ("" if persisted else "（注：.env 写回失败，重启后会回退到原档位）")
        )
        out.stale_chunks = total
    elif not persisted:
        out.notice = "档位已切换，但 .env 写回失败 —— 重启后会回退到原档位。"

    return out


@router.get(
    "/usage",
    response_model=ModelUsageStats,
    summary="模型调用用量（成本控制依据）",
)
def model_usage(_: CurrentActor = Depends(get_current_actor)):
    """用量统计。

    ⚠️ local 档走内网自建模型，一般不计费；本接口保留用于未来的调用量观测。
       本期返回占位数据，接入真实调用后由 middleware 埋点累计。
    """
    return ModelUsageStats()
