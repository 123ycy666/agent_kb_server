# -*- coding: utf-8 -*-
"""企业级本地知识库（自建 RAG）客户端：**只取资料，不要它生成的答案**。

对接新知识库系统的 `POST /api/v1/search`（向量召回 + 重排），
由本项目自己的模型与 Prompt 生成回答 —— 即知识库方文档里的「方案 B：只取资料」。

三条硬性线上约定（详见知识库方《对话项目接入知识库-示例》）：
1. **必须用公网 IP 访问**：nginx 的 server_name 是 `47.121.183.81`，
   用域名或 `127.0.0.1` 会落到默认站 → 502。所以 `config.KB_BASE` 写死 IP。
2. **认证只有一条路**：`POST /auth/login` 拿 JWT，之后带 `Authorization: Bearer <token>`。
   JWT **12 小时过期** → 本模块内建「401 自动重登一次再重试」。
3. **服务端调用无跨域问题**（CORS 只约束浏览器），所以这里不管 CORS_ORIGINS。

为什么单独一个文件：它是**唯一接触新知识库 HTTP 的地方**，上层（tools_kb / agent）
永远不直接拼 URL。将来知识库换地址 / 换认证方式，只改这一个文件。
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import config

_LOCK = threading.Lock()
_TOKEN = {'value': None, 'expire_at': 0.0}
# 提前这么久就当作过期，避免边界上「刚拿到就失效」
_SKEW = 60


class KBError(RuntimeError):
    """知识库调用失败（网络 / 认证 / 业务错误）。上层一律按「没检索到」兜底，不抛给用户。"""


def _read_err(e):
    try:
        return e.read().decode('utf-8', 'replace')[:200]
    except Exception:                             # noqa: BLE001
        return ''


def _post(path, body, token=None, timeout=None):
    url = config.KB_BASE.rstrip('/') + path
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'), headers=headers)
    with urllib.request.urlopen(req, timeout=timeout or config.HTTP_TIMEOUT) as r:
        return json.loads(r.read().decode('utf-8', 'replace'))


def _login(timeout=None):
    """登录并缓存 JWT（12h）。返回 token。"""
    d = _post('/auth/login', {'username': config.KB_USER, 'password': config.KB_PASSWORD},
              timeout=timeout)
    tok = d.get('access_token')
    if not tok:
        raise KBError('登录未返回 access_token：' + json.dumps(d, ensure_ascii=False)[:200])
    try:
        expire = float(d.get('expires_in') or 43200)
    except (TypeError, ValueError):
        expire = 43200.0
    with _LOCK:
        _TOKEN['value'] = tok
        _TOKEN['expire_at'] = time.time() + max(expire - _SKEW, 60)
    return tok


def _token():
    with _LOCK:
        tok, exp = _TOKEN['value'], _TOKEN['expire_at']
    if tok and time.time() < exp:
        return tok
    return _login()


def search(query, kb_ids=None, top_k=5, rerank=True, tag_filters=None,
           include_conflict=False, timeout=None):
    """`POST /search`：返回命中切片原文 + 分数（**不带答案**）。

    返回原始响应 dict：`{'results':[...], 'total':..., 'degraded':..., 'filter_notice':...}`。
    每条 result：`content`（切片，**已带【标题路径】前缀**）、`parent_content`（同章节父块，
    喂 LLM 优先用它）、`rerank_score` / `fused_score`、`doc_name` / `doc_id` / `heading_path`、
    `has_conflict` / `tags`。
    """
    body = {'query': query, 'kb_ids': list(kb_ids or config.KB_IDS),
            'top_k': int(top_k), 'rerank': bool(rerank),
            'include_conflict': bool(include_conflict)}
    if tag_filters:
        body['tag_filters'] = list(tag_filters)
    try:
        return _post('/search', body, token=_token(), timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code == 401:                          # token 过期/被吊销 → 重登一次再试
            try:
                return _post('/search', body, token=_login(), timeout=timeout)
            except Exception as e2:                # noqa: BLE001
                raise KBError('401 重登后仍失败：%s: %s' % (type(e2).__name__, str(e2)[:160]))
        raise KBError('HTTP %s %s' % (e.code, _read_err(e)))
    except Exception as e:                         # noqa: BLE001  网络/超时/解析
        raise KBError('%s: %s' % (type(e).__name__, str(e)[:200]))


def health(timeout=8):
    """健康检查（给 /health 与排障用）：能登录即视为可用。"""
    try:
        _login(timeout=timeout)
        return {'available': True, 'source': config.KB_BASE,
                'kb_ids': list(config.KB_IDS), 'user': config.KB_USER}
    except Exception as e:                         # noqa: BLE001
        return {'available': False, 'reason': '%s: %s' % (type(e).__name__, str(e)[:200])}
