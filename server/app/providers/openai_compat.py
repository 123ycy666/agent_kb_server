"""OpenAI 兼容协议的 LLM 通用实现。

为什么单独抽出来
----------------
DeepSeek 与阿里云百炼（兼容模式）的对话接口**协议完全一致**，
仅 base_url / model / API Key 不同。抽成基类后：

    新增一个 OpenAI 兼容的模型 = 写 3 行子类

公司本地部署（vLLM / Ollama / Xinference）多数也提供 OpenAI 兼容端点，
因此本文件同样复用于 local 档。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx

from app.providers.base import (
    LLMProvider,
    Message,
    ProviderError,
    ProviderHealth,
    ProviderNotConfigured,
)
from app.providers.http_client import get_client

DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0)


class OpenAICompatibleLLM(LLMProvider):
    """基于 /chat/completions 的通用实现。"""

    def __init__(
        self,
        *,
        provider_name: str,
        model_id: str,
        api_key: str,
        base_url: str,
        timeout: httpx.Timeout | None = None,
    ) -> None:
        self.provider_name = provider_name
        self.model_id = model_id
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout or DEFAULT_TIMEOUT

    # ── 内部工具 ─────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        if not self._api_key:
            raise ProviderNotConfigured(
                f"{self.provider_name} 的 API Key 未配置。请在模型配置页填写，"
                f"或把 PROVIDER_MODE 切回 mock。",
                provider=self.provider_name,
            )
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _payload(
        self,
        messages: list[Message],
        temperature: float,
        max_tokens: int | None,
        json_mode: bool,
        stream: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model_id,
            "messages": [m.to_dict() for m in messages],
            "temperature": temperature,
            "stream": stream,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if json_mode:
            # 统一的 JSON 模式参数，兼容 OpenAI / DeepSeek / DashScope 兼容模式
            payload["response_format"] = {"type": "json_object"}
        return payload

    @staticmethod
    def _raise_for_status(resp: httpx.Response, provider_name: str) -> None:
        if resp.status_code >= 400:
            raise ProviderError(
                f"HTTP {resp.status_code}：{resp.text[:500]}", provider=provider_name
            )

    # ── 对外接口 ─────────────────────────────────────────────

    def chat(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> str:
        # ⚠️ 2026-10-15：改用共享客户端（原为每次新建 httpx.Client）。
        #    冲突判定已并发化（8 路），每次判定都新建连接 = 重复付 8 次 TLS 握手。
        #    注：流式的 chat_stream 仍按请求新建 —— 流式连接的生命周期与请求绑定，
        #    复用收益小、风险大，故保持原样。
        try:
            resp = get_client(f"llm-{self.provider_name}", self._timeout).post(
                f"{self._base_url}/chat/completions",
                headers=self._headers(),
                json=self._payload(messages, temperature, max_tokens, json_mode, False),
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"请求异常：{exc}", provider=self.provider_name, cause=exc) from exc

        self._raise_for_status(resp, self.provider_name)

        try:
            return resp.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError(
                f"响应解析失败：{resp.text[:300]}", provider=self.provider_name, cause=exc
            ) from exc

    def chat_stream(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> Iterator[str]:
        try:
            with httpx.Client(timeout=self._timeout) as client:
                with client.stream(
                    "POST",
                    f"{self._base_url}/chat/completions",
                    headers=self._headers(),
                    json=self._payload(messages, temperature, max_tokens, False, True),
                ) as resp:
                    self._raise_for_status(resp, self.provider_name)
                    for line in resp.iter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            delta = json.loads(data)["choices"][0]["delta"].get("content")
                        except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                            continue
                        if delta:
                            yield delta
        except httpx.HTTPError as exc:
            raise ProviderError(f"流式请求异常：{exc}", provider=self.provider_name, cause=exc) from exc

    def health_check(self) -> ProviderHealth:
        import time

        if not self._api_key:
            return ProviderHealth(
                ok=False,
                provider=self.provider_name,
                model=self.model_id,
                message=f"{self.provider_name.upper()} API Key 未配置",
            )
        start = time.perf_counter()
        try:
            text = self.chat([Message(role="user", content="回复两个字：正常")], max_tokens=16)
        except ProviderError as exc:
            return ProviderHealth(
                ok=False, provider=self.provider_name, model=self.model_id, message=str(exc)
            )
        cost = int((time.perf_counter() - start) * 1000)
        return ProviderHealth(
            ok=True,
            provider=self.provider_name,
            model=self.model_id,
            message=f"连接正常，返回：{text.strip()[:40]}",
            latency_ms=cost,
        )
