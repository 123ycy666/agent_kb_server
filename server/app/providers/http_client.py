"""Provider 层共用的 HTTP 客户端与退避重试。

⚠️ 为什么要有这个公共模块（2026-10-15）
1. **连接复用**：原先 online/local 档每次 embed/rerank 都 `with httpx.Client(...)`
   新建客户端，每批都要重付一次 TCP+TLS 握手（实测首批 1.40s vs 稳态 0.93s）。
   分批并发后这笔开销会被乘以批次数，必须复用。
   httpx.Client 官方保证线程安全，可被并发分批共享。
2. **退避重试**：并发分批会把瞬时 QPS 放大 N 倍，服务返回 429 是常态而非异常。
   没有重试，「并发提速」会退化成「并发报错」；内网服务重启/抖动同样需要兜底。

两个档位共用同一套实现 —— 否则会出现「线上档有重试、切到内网后行为悄悄退化」
这种最难排查的差异。
"""

from __future__ import annotations

import threading
import time

import httpx

from app.providers.base import ProviderError

# ⚠️ 按「服务名」复用客户端：online 与 local 的 read 超时不同，不能混用同一个实例。
_clients: dict[str, httpx.Client] = {}
_client_lock = threading.Lock()

_LIMITS = httpx.Limits(max_connections=32, max_keepalive_connections=16)

# 需要退避重试的状态码：429 限流 + 网关类瞬时错误
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_RETRY = 3


def get_client(name: str, timeout: httpx.Timeout) -> httpx.Client:
    """按名字取共享 Client（懒加载 + 双重检查加锁，避免并发下重复创建）。

    httpx.Client 是线程安全的，可安全地被 ThreadPoolExecutor 中的多个批次共用。
    """
    client = _clients.get(name)
    if client is None:
        with _client_lock:
            client = _clients.get(name)
            if client is None:
                client = httpx.Client(timeout=timeout, limits=_LIMITS)
                _clients[name] = client
    return client


def post_with_retry(
    url: str,
    *,
    headers: dict[str, str],
    payload: dict,
    action: str,
    provider: str,
    client_name: str,
    timeout: httpx.Timeout,
) -> httpx.Response:
    """POST 并做指数退避重试（0.5s / 1s）。

    :param action: 用于错误信息的动作名，如「向量化」「精排」
    :param provider: 档位名（aliyun / local），写入 ProviderError
    :param client_name: 共享客户端的名字（不同档位超时不同，需分开缓存）
    :raises ProviderError: 重试耗尽后仍然失败
    """
    last_err: Exception | None = None
    for attempt in range(MAX_RETRY):
        if attempt:
            time.sleep(0.5 * (2 ** (attempt - 1)))
        try:
            resp = get_client(client_name, timeout).post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            last_err = exc
            continue
        if resp.status_code in RETRY_STATUS:
            last_err = ProviderError(
                f"{action}被限流或服务不可用（HTTP {resp.status_code}）：{resp.text[:200]}",
                provider=provider,
            )
            continue
        return resp
    raise ProviderError(
        f"{action}重试 {MAX_RETRY} 次仍失败：{last_err}",
        provider=provider,
        cause=last_err if isinstance(last_err, Exception) else None,
    )
