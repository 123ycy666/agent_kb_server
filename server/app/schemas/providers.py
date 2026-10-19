"""模型配置与连接测试 Schema。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.providers.base import ProviderHealth

TestTarget = Literal["embedding", "rerank", "llm", "all"]


class ProviderInfo(BaseModel):
    """单个 Provider 的展示信息。"""

    kind: str                     # embedding | rerank | llm
    mode: str                     # mock | online | local
    model_id: str
    ready: bool                   # 凭据/地址是否齐备
    detail: str = ""


class ProviderConfigOut(BaseModel):
    """当前模型配置总览（已脱敏）。"""

    mode: str
    mode_label: str
    embedding: ProviderInfo
    rerank: ProviderInfo
    llm: ProviderInfo
    embedding_dim: int
    # 各档位的可配置项与当前值（密钥一律脱敏）
    options: dict[str, Any] = {}
    notice: str = ""
    # ⚠️ 向量模型与当前档位不一致、因而「检索不到」的切片数。
    #    切换档位后该值 > 0，前端据此提示用户重建索引。
    stale_chunks: int = 0


class ProviderTestRequest(BaseModel):
    """测试连接。

    允许传入临时凭据，**不会写回配置文件**，
    便于在设置页边填边测。
    """

    target: TestTarget = "all"
    mode: str | None = None

    # 本地
    local_embedding_url: str | None = None
    local_embedding_model: str | None = None
    local_rerank_url: str | None = None
    local_rerank_model: str | None = None
    local_llm_base_url: str | None = None
    local_llm_model: str | None = None
    local_llm_api_key: str | None = None

    # 线上（阿里云百炼）
    online_embedding_base_url: str | None = None
    online_embedding_model: str | None = None
    online_embedding_api_key: str | None = None
    online_rerank_base_url: str | None = None
    online_rerank_model: str | None = None
    online_rerank_api_key: str | None = None
    online_llm_base_url: str | None = None
    online_llm_model: str | None = None
    online_llm_api_key: str | None = None

    embedding_dim: int | None = None


class ProviderTestResult(BaseModel):
    target: str
    ok: bool
    provider: str
    model: str
    message: str
    latency_ms: int = 0
    extra: dict[str, Any] = {}

    @classmethod
    def from_health(cls, target: str, health: ProviderHealth) -> "ProviderTestResult":
        return cls(
            target=target,
            ok=health.ok,
            provider=health.provider,
            model=health.model,
            message=health.message,
            latency_ms=health.latency_ms,
            extra=health.extra,
        )


class ProviderTestResponse(BaseModel):
    mode: str
    results: list[ProviderTestResult]
    all_ok: bool
    # 本次测试实际使用的凭据来源，明确标识避免混淆：
    #   "form" —— 用了表单里填写的临时凭据（不写回配置，只影响本次）
    #   "env"  —— 用了 .env / 环境变量里的凭据
    # 用户常见的困惑是「表单填了成功、.env 却不生效」，
    # 多数情况下两者根本不是同一套凭据。把它显式标出来即可消除歧义。
    credential_source: str = "env"
    notice: str = ""


class ModelUsageStats(BaseModel):
    """模型调用用量（成本控制的依据，全真调用会真实扣费）。"""

    embedding_calls: int = 0
    embedding_tokens: int = 0
    rerank_calls: int = 0
    llm_calls: int = 0
    llm_tokens: int = 0
    estimated_cost: float = Field(default=0.0, description="估算费用（元）")


class SwitchModeRequest(BaseModel):
    """运行时切换模型通道（同时写回 .env，重启后依然生效）。"""

    mode: Literal["mock", "local", "online"]
