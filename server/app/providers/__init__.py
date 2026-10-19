"""模型适配层。

对外只暴露：接口定义、工厂、数据结构。
业务代码应通过 `get_providers()` 获取模型能力，不得直接实例化具体实现。
"""

from app.providers.base import (
    EmbeddingProvider,
    LLMProvider,
    Message,
    ProviderBundle,
    ProviderError,
    ProviderHealth,
    ProviderNotConfigured,
    RerankProvider,
    ScoredDoc,
)
from app.providers.factory import (
    ProviderOverrides,
    build_bundle,
    current_embedding_model_name,
    get_providers,
    reset_providers,
)

__all__ = [
    # 接口与数据结构
    "EmbeddingProvider",
    "RerankProvider",
    "LLMProvider",
    "Message",
    "ScoredDoc",
    "ProviderHealth",
    "ProviderBundle",
    "ProviderError",
    "ProviderNotConfigured",
    # 工厂
    "get_providers",
    "build_bundle",
    "reset_providers",
    "current_embedding_model_name",
    "ProviderOverrides",
]
