"""local 档 —— 公司本地部署模型适配（本期仅预留，不启用）。

目标模型（领导已确认公司本地已部署）
------------------------------------
- Embedding : bge-m3              （1024 维）
- Rerank    : bge-rerank-m3
- LLM       : DeepSeek / 千问3.6

接入方式
--------
这三个类**已经写好了**，只是默认不启用。启用步骤：
1. 修改 .env：MODEL_CHANNEL=local
2. 填写 LOCAL_* 三个地址（公司内网服务地址）
3. 重启服务

⚠️ 业务代码零改动 —— 这正是 Provider 抽象层的价值。

协议兼容性说明
--------------
- Embedding：按 OpenAI 兼容的 `/v1/embeddings` 实现。
  Xinference / vLLM / Ollama 均原生支持该端点。
  若公司使用 TEI（text-embeddings-inference），其端点是 `/embed`，
  请求体为 {"inputs": [...]}，需在 `_embed_tei()` 中按注释切换。
- Rerank：按 Xinference / Jina 风格的 `/v1/rerank` 实现。
  TEI 的端点为 `/rerank`，请求体 {"query":..., "texts":[...]}，
  已提供 `_rerank_tei()` 备用实现。
- LLM：OpenAI 兼容端点。
"""

from __future__ import annotations

import time

import httpx

from app.core.config import settings
from app.providers.base import (
    EmbeddingProvider,
    ProviderError,
    ProviderHealth,
    RerankProvider,
    ScoredDoc,
)
from app.providers.http_client import post_with_retry
from app.providers.openai_compat import OpenAICompatibleLLM

PROVIDER_NAME = "local"

# 共享 HTTP 客户端在 http_client 中按此名缓存
# （local 的 read 超时比 online 长，必须分开缓存，不能共用一个实例）
CLIENT_NAME = "local"

TIMEOUT = httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0)


# ══════════════════════════════════════════════════════════════
#  Embedding — bge-m3
# ══════════════════════════════════════════════════════════════


class LocalEmbeddingProvider(EmbeddingProvider):
    """本地 bge-m3。

    ⚠️ bge-m3 输出 1024 维，与阿里 text-embedding-v4 维度相同，
       但**向量空间完全不同** —— 两者生成的向量不可混用，
       切换时必须全量重建索引。
    """

    def __init__(
        self,
        url: str | None = None,
        model_id: str | None = None,
        dim: int | None = None,
        api_key: str | None = None,
    ) -> None:
        self._url = url or settings.local_embedding_url
        self.model_id = model_id or settings.local_embedding_model
        self.dim = dim or settings.embedding_dim
        # ⚠️ 内网网关常强制 Bearer 鉴权；留空则不发头（兼容 vLLM/Ollama）。
        self._api_key = api_key or settings.local_embedding_api_key or ""

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        # ── 方式 A：OpenAI 兼容端点（Xinference / vLLM / Ollama）──
        payload = {"model": self.model_id, "input": texts}
        # ⚠️ 2026-10-15：改走共享客户端 + 退避重试（原为每次新建 httpx.Client）。
        #    与 online 档保持一致：并发分批下不再重复付 TLS 握手，内网服务抖动有兜底。
        resp = post_with_retry(
            self._url,
            headers=self._headers(),
            payload=payload,
            action="本地向量化",
            provider=PROVIDER_NAME,
            client_name=CLIENT_NAME,
            timeout=TIMEOUT,
        )

        if resp.status_code >= 400:
            raise ProviderError(
                f"本地向量化失败（HTTP {resp.status_code}）：{resp.text[:300]}",
                provider=PROVIDER_NAME,
            )

        try:
            data = sorted(resp.json()["data"], key=lambda d: d.get("index", 0))
            return [d["embedding"] for d in data]
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError(
                f"本地向量化响应解析失败：{resp.text[:300]}",
                provider=PROVIDER_NAME,
                cause=exc,
            ) from exc

    # ── 方式 B：TEI 端点（如公司使用 text-embeddings-inference）──
    # 启用步骤：在 .env 中把 LOCAL_EMBEDDING_URL 指向 /embed，
    #          并把上面 embed() 的实现替换为下面这段。
    #
    # def embed(self, texts: list[str]) -> list[list[float]]:
    #     with httpx.Client(timeout=TIMEOUT) as client:
    #         resp = client.post(self._url, json={"inputs": texts})
    #     resp.raise_for_status()
    #     return resp.json()   # TEI 直接返回 [[...], [...]]

    def health_check(self) -> ProviderHealth:
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
            message=f"本地 bge-m3 连接正常，返回 {len(vecs[0])} 维向量",
            latency_ms=cost,
            extra={"dim": len(vecs[0])},
        )


# ══════════════════════════════════════════════════════════════
#  Rerank — bge-rerank-m3
# ══════════════════════════════════════════════════════════════


class LocalRerankProvider(RerankProvider):
    """本地 bge-rerank-m3。"""

    def __init__(
        self,
        url: str | None = None,
        model_id: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self._url = url or settings.local_rerank_url
        self.model_id = model_id or settings.local_rerank_model
        self._api_key = api_key or settings.local_rerank_api_key or ""

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}

    def rerank(self, query: str, docs: list[str], top_k: int | None = None) -> list[ScoredDoc]:
        if not docs:
            return []

        # ── 方式 A：Xinference / Jina 风格 /v1/rerank ──
        payload = {
            "model": self.model_id,
            "query": query,
            "documents": docs,
            "top_n": top_k or len(docs),
        }
        # ⚠️ 2026-10-15：与 embed() 一致，改走共享客户端 + 退避重试
        resp = post_with_retry(
            self._url,
            headers=self._headers(),
            payload=payload,
            action="本地精排",
            provider=PROVIDER_NAME,
            client_name=CLIENT_NAME,
            timeout=TIMEOUT,
        )

        if resp.status_code >= 400:
            raise ProviderError(
                f"本地精排失败（HTTP {resp.status_code}）：{resp.text[:300]}",
                provider=PROVIDER_NAME,
            )

        try:
            results = resp.json()["results"]
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError(
                f"本地精排响应解析失败：{resp.text[:300]}",
                provider=PROVIDER_NAME,
                cause=exc,
            ) from exc

        scored: list[ScoredDoc] = []
        for r in results:
            idx = int(r.get("index", 0))
            if 0 <= idx < len(docs):
                scored.append(
                    ScoredDoc(
                        index=idx,
                        text=docs[idx],
                        score=float(r.get("relevance_score", r.get("score", 0.0))),
                    )
                )
        scored.sort(key=lambda s: -s.score)
        return scored[:top_k] if top_k is not None else scored

    # ── 方式 B：TEI 端点 ──
    # def rerank(self, query, docs, top_k=None):
    #     with httpx.Client(timeout=TIMEOUT) as client:
    #         resp = client.post(self._url, json={"query": query, "texts": docs})
    #     resp.raise_for_status()
    #     results = resp.json()   # [{"index": 0, "score": 0.9}, ...]
    #     ...

    def health_check(self) -> ProviderHealth:
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
            message=f"本地 bge-rerank-m3 连接正常，最高相关度 {top:.4f}",
            latency_ms=cost,
        )


# ══════════════════════════════════════════════════════════════
#  LLM — 本地 DeepSeek / 千问3.6
# ══════════════════════════════════════════════════════════════


class LocalLLMProvider(OpenAICompatibleLLM):
    """公司本地部署的对话模型（OpenAI 兼容端点）。

    多数本地推理框架（vLLM / Xinference / Ollama / SGLang）
    都提供 OpenAI 兼容端点，因此直接复用通用实现。
    """

    def __init__(
        self,
        model_id: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        super().__init__(
            provider_name=PROVIDER_NAME,
            model_id=model_id or settings.local_llm_model,
            api_key=api_key or settings.local_llm_api_key or "sk-local-no-auth",
            base_url=base_url or settings.local_llm_base_url,
        )
