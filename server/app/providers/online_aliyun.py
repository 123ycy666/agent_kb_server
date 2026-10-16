"""online 档 —— 阿里云百炼（DashScope）适配。

用途
----
- Embedding：text-embedding-v4（1024 维，与 bge-m3 同维度）
- Rerank   ：gte-rerank-v2
- LLM      ：qwen-plus / qwen-max（作为 DeepSeek 之外的第二可选模型）

⚠️ 关键注意
1. text-embedding-v4 单次请求文本条数有限（约 10 条），必须分批
2. rerank 使用阿里**原生**接口，与 OpenAI 兼容格式不同，需单独适配
3. 维度相同 ≠ 向量空间相同：本档生成的向量与 bge-m3 **不可混用**，
   切换模型必须全量重建索引（由 document_chunk.embedding_model 校验）
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.core.config import settings
from app.providers.base import (
    EmbeddingProvider,
    ProviderError,
    ProviderHealth,
    ProviderNotConfigured,
    RerankProvider,
    ScoredDoc,
)
from app.providers.http_client import post_with_retry
from app.providers.openai_compat import OpenAICompatibleLLM

TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)

# 共享 HTTP 客户端在 http_client 中按此名缓存
# （online 与 local 的 read 超时不同，必须分开缓存，不能共用一个实例）
CLIENT_NAME = "aliyun"

PROVIDER_NAME = "aliyun"


def _ensure_key() -> str:
    raise ProviderNotConfigured(
        "线上通道 API Key 未配置。请在模型配置页填写 ONLINE_*_API_KEY，"
        "或把 MODEL_CHANNEL 切回 mock。",
        provider=PROVIDER_NAME,
    )


def _raise_for_status(resp: httpx.Response, action: str) -> None:
    if resp.status_code >= 400:
        raise ProviderError(
            f"{action} 失败（HTTP {resp.status_code}）：{resp.text[:500]}",
            provider=PROVIDER_NAME,
        )


# ══════════════════════════════════════════════════════════════
#  Embedding
# ══════════════════════════════════════════════════════════════


class AliyunEmbeddingProvider(EmbeddingProvider):
    """阿里云 text-embedding-v4。"""

    # 官方限制：单次请求文本条数上限
    MAX_BATCH = 10

    def __init__(
        self,
        model_id: str | None = None,
        dim: int | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.model_id = model_id or settings.online_embedding_model
        self.dim = dim or settings.embedding_dim
        self._api_key = api_key or settings.online_embedding_api_key
        self._base_url = (base_url or settings.online_embedding_base_url).rstrip("/")

    def _key(self) -> str:
        return self._api_key or _ensure_key()

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if len(texts) > self.MAX_BATCH:
            # 双保险：即使调用方没分批，这里也强制分批
            return self.embed_batch(texts, batch_size=self.MAX_BATCH)

        payload = {
            "model": self.model_id,
            "input": texts,
            "dimensions": self.dim,
            "encoding_format": "float",
        }
        resp = post_with_retry(
            f"{self._base_url}/embeddings",
            headers={
                "Authorization": f"Bearer {self._key()}",
                "Content-Type": "application/json",
            },
            payload=payload,
            action="向量化",
            provider=PROVIDER_NAME,
            client_name=CLIENT_NAME,
            timeout=TIMEOUT,
        )

        _raise_for_status(resp, "向量化")

        try:
            body = resp.json()
            data = sorted(body["data"], key=lambda d: d.get("index", 0))
            vectors = [d["embedding"] for d in data]
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError(
                f"向量化响应解析失败：{resp.text[:300]}", provider=PROVIDER_NAME, cause=exc
            ) from exc

        if len(vectors) != len(texts):
            raise ProviderError(
                f"向量化返回数量不匹配：期望 {len(texts)}，实际 {len(vectors)}",
                provider=PROVIDER_NAME,
            )
        for v in vectors:
            if len(v) != self.dim:
                raise ProviderError(
                    f"向量维度不匹配：期望 {self.dim}，实际 {len(v)}。"
                    f"请检查 EMBEDDING_DIM 配置与所用模型是否一致。",
                    provider=PROVIDER_NAME,
                )
        return vectors

    def health_check(self) -> ProviderHealth:
        if not self._api_key:
            return ProviderHealth(
                ok=False,
                provider=PROVIDER_NAME,
                model=self.model_id,
                message="ONLINE_*_API_KEY 未配置",
            )
        start = time.perf_counter()
        try:
            vecs = self.embed(["连接测试"])
        except ProviderError as exc:
            return ProviderHealth(
                ok=False, provider=PROVIDER_NAME, model=self.model_id, message=str(exc)
            )
        cost = int((time.perf_counter() - start) * 1000)
        return ProviderHealth(
            ok=True,
            provider=PROVIDER_NAME,
            model=self.model_id,
            message=f"连接正常，返回 {len(vecs[0])} 维向量",
            latency_ms=cost,
            extra={"dim": len(vecs[0])},
        )


# ══════════════════════════════════════════════════════════════
#  Rerank
# ══════════════════════════════════════════════════════════════


class AliyunRerankProvider(RerankProvider):
    """阿里云 gte-rerank-v2（原生接口，非 OpenAI 兼容格式）。"""

    MAX_DOCS = 100

    def __init__(
        self,
        model_id: str | None = None,
        api_key: str | None = None,
        endpoint: str | None = None,
    ) -> None:
        self.model_id = model_id or settings.online_rerank_model
        self._api_key = api_key or settings.online_rerank_api_key
        self._endpoint = endpoint or settings.online_rerank_base_url

    def _key(self) -> str:
        return self._api_key or _ensure_key()

    def rerank(self, query: str, docs: list[str], top_k: int | None = None) -> list[ScoredDoc]:
        if not docs:
            return []
        candidates = docs[: self.MAX_DOCS]

        payload: dict[str, Any] = {
            "model": self.model_id,
            "input": {"query": query, "documents": candidates},
            "parameters": {
                "return_documents": False,
                "top_n": top_k or len(candidates),
            },
        }
        resp = post_with_retry(
            self._endpoint,
            headers={
                "Authorization": f"Bearer {self._key()}",
                "Content-Type": "application/json",
            },
            payload=payload,
            action="精排",
            provider=PROVIDER_NAME,
            client_name=CLIENT_NAME,
            timeout=TIMEOUT,
        )

        _raise_for_status(resp, "精排")

        try:
            results = resp.json()["output"]["results"]
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError(
                f"精排响应解析失败：{resp.text[:300]}", provider=PROVIDER_NAME, cause=exc
            ) from exc

        scored: list[ScoredDoc] = []
        for r in results:
            idx = int(r.get("index", 0))
            if 0 <= idx < len(candidates):
                scored.append(
                    ScoredDoc(
                        index=idx,
                        text=candidates[idx],
                        score=float(r.get("relevance_score", 0.0)),
                    )
                )
        scored.sort(key=lambda s: -s.score)
        return scored[:top_k] if top_k is not None else scored

    def health_check(self) -> ProviderHealth:
        if not self._api_key:
            return ProviderHealth(
                ok=False,
                provider=PROVIDER_NAME,
                model=self.model_id,
                message="ONLINE_*_API_KEY 未配置",
            )
        start = time.perf_counter()
        try:
            res = self.rerank("报销标准", ["报销标准为 500 元", "今天天气很好"], top_k=2)
        except ProviderError as exc:
            return ProviderHealth(
                ok=False, provider=PROVIDER_NAME, model=self.model_id, message=str(exc)
            )
        cost = int((time.perf_counter() - start) * 1000)
        top = res[0].score if res else 0.0
        return ProviderHealth(
            ok=True,
            provider=PROVIDER_NAME,
            model=self.model_id,
            message=f"连接正常，最高相关度 {top:.4f}",
            latency_ms=cost,
        )


# ══════════════════════════════════════════════════════════════
#  LLM
# ══════════════════════════════════════════════════════════════


class AliyunLLMProvider(OpenAICompatibleLLM):
    """阿里云通义千问（OpenAI 兼容模式）。"""

    def __init__(
        self,
        model_id: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        super().__init__(
            provider_name=PROVIDER_NAME,
            model_id=model_id or settings.online_llm_model,
            api_key=api_key or settings.online_llm_api_key,
            base_url=base_url or settings.online_llm_base_url,
        )
