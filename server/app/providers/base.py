"""模型适配层 —— 接口定义。

这是整个项目**最重要的抽象**：业务代码永远不直接调用模型 HTTP 接口，
只依赖这里的三个 Provider 接口，从而做到「换模型不改业务代码」。

实现档位
--------
- mock   : 确定性假实现，零依赖，用于功能开发与演示
- online : 线上 API（阿里云百炼 + DeepSeek），用于全链路真实测试
- local  : 公司本地部署（bge-m3 / bge-rerank-m3 / DeepSeek / 千问3.6）

切换方式：环境变量 MODEL_CHANNEL，业务代码零改动。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings


class ProviderError(RuntimeError):
    """模型调用异常的统一封装。

    所有 Provider 实现都必须把底层异常（HTTP 错误、超时、解析失败）
    统一转换为本异常，避免业务层感知具体实现。
    """

    def __init__(self, message: str, *, provider: str = "", cause: Exception | None = None):
        super().__init__(message)
        self.provider = provider
        self.cause = cause


class ProviderNotConfigured(ProviderError):
    """档位已切换但凭据/地址未配置。"""


# ══════════════════════════════════════════════════════════════
#  冲突判定 Prompt 的边界标记（Mock 与真实实现共用）
# ══════════════════════════════════════════════════════════════
#
# ⚠️ 必须使用带明确边界的 ASCII 标记，不要用【】这类中文符号。
#    原因：切片内容本身以「【标题路径】」开头（为提升检索命中率而设计），
#    若用【】当分隔符，解析时会与正文撞车 ——
#    判定输入被截断成空串，冲突检测静默失效（不报错，只是永远检不出冲突）。
#    这类「功能静默不工作」的问题最难排查，务必保持标记唯一且带结束符。
MARK_NEW = "<<<NEW_TEXT>>>"
MARK_OLD = "<<<OLD_TEXT>>>"
MARK_END = "<<<END>>>"


# ══════════════════════════════════════════════════════════════
#  数据结构
# ══════════════════════════════════════════════════════════════


@dataclass
class ScoredDoc:
    """精排结果。index 指向输入列表下标。"""

    index: int
    text: str
    score: float


@dataclass
class Message:
    """对话消息。"""

    role: str  # system | user | assistant
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass
class ProviderHealth:
    """连接测试结果。"""

    ok: bool
    provider: str
    model: str
    message: str
    latency_ms: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


# ══════════════════════════════════════════════════════════════
#  Embedding
# ══════════════════════════════════════════════════════════════


class EmbeddingProvider(ABC):
    """文本向量化。"""

    model_id: str
    dim: int

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """将一批文本转为向量。

        ⚠️ 必须保证：相同文本 → 相同向量（确定性）。否则检索结果不可复现，
           冲突检测的相似度也无法作为判定依据。
        """

    def embed_query(self, text: str) -> list[float]:
        """单条查询向量化。"""
        return self.embed([text])[0]

    def embed_batch(
        self, texts: list[str], batch_size: int = 10, max_workers: int | None = None
    ) -> list[list[float]]:
        """分批向量化（**并发**）。

        ⚠️ 阿里 text-embedding-v4 单次请求上限硬限制 10 条（超过直接 HTTP 400），
           长文档必须分批，且**无法靠加大批次提速**，只能并发。
           默认 batch_size=10。

        ⚠️ 2026-10-15：分批由串行改为并发。实测在线档每批 ≈ 0.93s（网络往返），
           一份 1500 段的文档串行 = 150 批 ≈ 139s，是上传慢的主因。
           各批次彼此独立 ⇒ 并发后总耗时 ≈ 最慢的一批（实测 100 条 9.28s → 0.61s）。
           并发度取 settings.embed_batch_concurrency（默认 8，可配 1 退回串行）。
        """
        if not texts:
            return []
        batches = [texts[i : i + batch_size] for i in range(0, len(texts), batch_size)]
        if len(batches) == 1:
            return list(self.embed(batches[0]))

        workers = max_workers if max_workers is not None else settings.embed_batch_concurrency
        workers = max(1, min(int(workers), len(batches)))
        if workers == 1:  # 允许显式退回串行（部分内网服务不支持并发）
            out: list[list[float]] = []
            for b in batches:
                out.extend(self.embed(b))
            return out

        with ThreadPoolExecutor(max_workers=workers) as ex:
            parts = list(ex.map(self.embed, batches))  # map 保持批次顺序
        merged: list[list[float]] = []
        for p in parts:
            merged.extend(p)
        return merged

    @abstractmethod
    def health_check(self) -> ProviderHealth:
        """连通性测试（用于「测试连接」按钮）。"""


# ══════════════════════════════════════════════════════════════
#  Rerank
# ══════════════════════════════════════════════════════════════


class RerankProvider(ABC):
    """检索结果精排。"""

    model_id: str

    @abstractmethod
    def rerank(self, query: str, docs: list[str], top_k: int | None = None) -> list[ScoredDoc]:
        """对候选文档重排序，返回按分数降序的结果。"""

    @abstractmethod
    def health_check(self) -> ProviderHealth: ...


# ══════════════════════════════════════════════════════════════
#  LLM
# ══════════════════════════════════════════════════════════════


class LLMProvider(ABC):
    """大语言模型。

    用于两处：
    1. 问答生成（RAG）
    2. 冲突判定（关系分类，需要结构化输出）
    """

    model_id: str

    @abstractmethod
    def chat(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> str:
        """同步对话。

        Parameters
        ----------
        json_mode
            为 True 时要求模型输出严格 JSON。冲突判定**必须**开启，
            否则同一份内容今天判为冲突、明天判为无关。
        """

    @abstractmethod
    def chat_stream(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> Iterator[str]:
        """流式对话，用于问答接口的 SSE 输出。"""

    @abstractmethod
    def health_check(self) -> ProviderHealth: ...


# ══════════════════════════════════════════════════════════════
#  聚合容器
# ══════════════════════════════════════════════════════════════


@dataclass
class ProviderBundle:
    """当前生效的 Provider 集合。"""

    embedding: EmbeddingProvider
    rerank: RerankProvider
    llm: LLMProvider
    mode: str
    # 冲突判定**专用** LLM（2026-10-15 新增）。
    # None = 与 ``llm`` 是同一个实例（未单独配置判定模型时的默认行为）。
    # ⚠️ 判定的调用方一律用 ``judge_llm or llm``：
    #    `llm` 还要服务知识库问答与问数 agent，那两处不可被判定模型的降级波及。
    judge_llm: "LLMProvider | None" = None
