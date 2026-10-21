# -*- coding: utf-8 -*-
"""大屏跳转意图：把（补全后的）用户问题交给 Java 侧 /api/ai/big-screen/preview，
拿回 layer / method / mainId / data，平铺进 /api/ask 的响应，供大屏前端跳转。

纪律（改动前先读这几条）：
  1. **默认关闭**：只有请求显式带 want_screen 才调。知识库页、技能台、定时任务、
     预警等调用方一律不调 —— 否则每个人都替大屏多等一次模型推理。
  2. **绝不影响问答**：超时 / 5xx / 返回不合法 / 网络不通，一律返回 None，
     调用方据此不加字段。问答该成功还是成功。
  3. **只透传，不解释**：对方的 text / markdown / stepResults / allMethods / allData
     是兼容字段，全部丢弃；method / layer 的枚举由对方维护，新增值我们自动跟随。
  4. **必须在线程池里调**：/api/ask 是 async def，这里用的是同步 httpx.Client，
     调用方必须 await run_in_threadpool(...)，不能直接调（会阻塞事件循环）。
"""
from __future__ import annotations

import httpx

from app.core.config import settings

# 只透传这四个字段（对方文档第 3 节）
FIELDS = ('layer', 'method', 'mainId', 'data')


def enabled() -> bool:
    """配置了完整 URL 才算启用；留空 = 整个功能关掉。"""
    return bool((getattr(settings, 'screen_preview_url', '') or '').strip())


def preview(question: str) -> dict | None:
    """调对方 preview，成功返回 {layer, method, mainId, data}；任何失败返回 None。"""
    url = (getattr(settings, 'screen_preview_url', '') or '').strip()
    text = (question or '').strip()
    if not url or not text:
        return None

    timeout = float(getattr(settings, 'screen_preview_timeout', 5.0) or 5.0)
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json={'message': text})
        if resp.status_code != 200:
            print('[screen] preview HTTP %s，跳过（不影响问答）' % resp.status_code, flush=True)
            return None
        body = resp.json()
        if not isinstance(body, dict) or not body.get('ok'):
            print('[screen] preview ok=false：%s' % str(body.get('msg') if isinstance(body, dict) else body)[:120],
                  flush=True)
            return None
        payload = body.get('data') or {}
        if not isinstance(payload, dict):
            return None
        out = {k: payload.get(k) for k in FIELDS}
        # 对方保证 method 一定有值；万一缺失按 unknownIntent 处理（前端据此不跳转）
        if not out.get('method'):
            out['method'] = 'unknownIntent'
        if out.get('layer') is None:
            out['layer'] = ''
        if not isinstance(out.get('data'), list):
            out['data'] = []
        return out
    except Exception as e:                      # noqa: BLE001 - 增强能力，绝不让问答挂
        print('[screen] preview 调用失败，已跳过：%s: %s' % (type(e).__name__, e), flush=True)
        return None
