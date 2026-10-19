"""Provider 工厂 —— 按配置装配三档实现（mock / local / online）。

这是「换模型不改业务代码」的落点：
业务层统一调用 `get_providers()`，永远不知道当前是 mock、本地还是线上模型。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from app.core.config import settings
from app.providers.base import LLMProvider, ProviderBundle
from app.providers.local_bge import (
    LocalEmbeddingProvider,
    LocalLLMProvider,
    LocalRerankProvider,
)
from app.providers.mock import (
    MockEmbeddingProvider,
    MockLLMProvider,
    MockRerankProvider,
)
from app.providers.online_aliyun import (
    AliyunEmbeddingProvider,
    AliyunLLMProvider,
    AliyunRerankProvider,
)


@dataclass
class ProviderOverrides:
    """临时覆盖配置，仅用于「测试连接」场景。

    不写回配置文件，只影响本次构造的 Provider 实例。
    """

    local_embedding_url: str | None = None
    local_embedding_model: str | None = None
    local_embedding_api_key: str | None = None
    local_rerank_url: str | None = None
    local_rerank_model: str | None = None
    local_rerank_api_key: str | None = None
    local_llm_base_url: str | None = None
    local_llm_model: str | None = None
    local_llm_api_key: str | None = None

    # online 档临时凭据（「测试连接」场景用，不写回 .env）
    online_llm_base_url: str | None = None
    online_llm_model: str | None = None
    online_llm_api_key: str | None = None
    online_embedding_base_url: str | None = None
    online_embedding_model: str | None = None
    online_embedding_api_key: str | None = None
    online_rerank_base_url: str | None = None
    online_rerank_model: str | None = None
    online_rerank_api_key: str | None = None

    embedding_dim: int | None = None


def _build_llm_pair(
    *,
    provider_cls: type[LLMProvider],
    ov_model: str | None,
    base_model: str,
    judge_model: str,
    api_key: str | None,
    base_url: str | None,
) -> tuple[LLMProvider, LLMProvider | None]:
    """构造「主 LLM + 冲突判定专用 LLM」。

    ⚠️ 为什么要分开（2026-10-15）
    主 LLM 同时服务「知识库问答」与「问数 agent」，那两处需要强模型（工具调用、
    长回答最稳）；而冲突判定只是四分类关系判断，用快模型即可（实测同网关
    qwen-plus 比 qwen3.8-max 快约 4 倍）。两者共用一个配置键，会把「优化判定
    速度」变成「顺带把问答与整个问数 agent 一起降级」。

    :return: ``(主 LLM, 判定 LLM)``；判定模型与主模型相同时第二个为 ``None``，
             调用方一律用 ``bundle.judge_llm or bundle.llm`` 取值。
    """
    llm = provider_cls(model_id=ov_model, api_key=api_key, base_url=base_url)
    effective_base = ov_model or base_model
    judge = judge_model or effective_base
    if judge == effective_base:
        return llm, None
    return llm, provider_cls(model_id=judge, api_key=api_key, base_url=base_url)


def build_bundle(
    mode: str | None = None,
    overrides: ProviderOverrides | None = None,
) -> ProviderBundle:
    """构造 Provider 集合（无缓存，用于测试连接或指定档位）。"""
    target = (mode or settings.provider_mode).lower()
    ov = overrides or ProviderOverrides()
    dim = ov.embedding_dim or settings.embedding_dim

    # ══════════════════════════════════════════════════════════
    #  mock 档：零依赖
    # ══════════════════════════════════════════════════════════
    if target == "mock":
        return ProviderBundle(
            mode="mock",
            embedding=MockEmbeddingProvider(dim=dim),
            rerank=MockRerankProvider(),
            llm=MockLLMProvider(),
        )

    # ══════════════════════════════════════════════════════════
    #  local 档：公司本地部署（bge-m3 / bge-rerank-m3 / DeepSeek / 千问3.6）
    # ══════════════════════════════════════════════════════════
    if target == "local":
        llm, judge_llm = _build_llm_pair(
            provider_cls=LocalLLMProvider,
            ov_model=ov.local_llm_model,
            base_model=settings.local_llm_model,
            judge_model=settings.local_judge_model,
            api_key=ov.local_llm_api_key,
            base_url=ov.local_llm_base_url,
        )
        return ProviderBundle(
            mode="local",
            embedding=LocalEmbeddingProvider(
                url=ov.local_embedding_url,
                model_id=ov.local_embedding_model,
                dim=dim,
                api_key=ov.local_embedding_api_key,
            ),
            rerank=LocalRerankProvider(
                url=ov.local_rerank_url,
                model_id=ov.local_rerank_model,
                api_key=ov.local_rerank_api_key,
            ),
            llm=llm,
            judge_llm=judge_llm,
        )

    # ══════════════════════════════════════════════════════════
    #  online 档：线上 API（阿里云百炼 compatible-mode）
    # ══════════════════════════════════════════════════════════
    if target == "online":
        llm, judge_llm = _build_llm_pair(
            provider_cls=AliyunLLMProvider,
            ov_model=ov.online_llm_model,
            base_model=settings.online_llm_model,
            judge_model=settings.online_judge_model,
            api_key=ov.online_llm_api_key,
            base_url=ov.online_llm_base_url,
        )
        return ProviderBundle(
            mode="online",
            embedding=AliyunEmbeddingProvider(
                model_id=ov.online_embedding_model,
                dim=dim,
                api_key=ov.online_embedding_api_key,
                base_url=ov.online_embedding_base_url,
            ),
            rerank=AliyunRerankProvider(
                model_id=ov.online_rerank_model,
                api_key=ov.online_rerank_api_key,
                endpoint=ov.online_rerank_base_url,
            ),
            llm=llm,
            judge_llm=judge_llm,
        )

    raise ValueError(
        f"未知的 MODEL_CHANNEL：{target!r}，可选值为 mock / local / online"
    )


@lru_cache(maxsize=1)
def get_providers() -> ProviderBundle:
    """获取当前生效的 Provider 集合（带缓存）。

    业务代码统一通过本函数获取模型能力。
    ⚠️ 修改 .env 后需要调用 reset_providers() 或重启服务才能生效。
    """
    return build_bundle()


def reset_providers() -> None:
    """清除缓存，使配置变更立即生效。"""
    get_providers.cache_clear()


def current_embedding_model_name() -> str:
    """当前 embedding 模型的唯一标识。

    写入 document_chunk.embedding_model，用于识别「哪些切片需要重建索引」。
    格式：{mode}/{model_id}，例如 local/bge-m3、mock/mock-embedding-1024d。
    """
    bundle = get_providers()
    return f"{bundle.mode}/{bundle.embedding.model_id}"
