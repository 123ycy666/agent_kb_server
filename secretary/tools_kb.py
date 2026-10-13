# -*- coding: utf-8 -*-
"""知识库检索客户端：只取切片，不取它生成的答案。

两种后端（config.KB_BACKEND）：
  bailian —— 阿里云百炼索引（原实现，保留以便一行回滚）
  local   —— 公司自建知识库（FastAPI + pgvector，47.121.183.81）
两者返回**同一个结构**：{'total', 'cost_time', 'nodes':[{'score','doc_name','title','content'}]}，
调用方（agent.rule_slices、kb_search 工具）不需要知道用的是哪一个。
"""
import json
import ssl
import time
import urllib.error
import urllib.request

import config

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE


_CACHE = {}
_CACHE_MAX = 200
_LOCAL = {'token': '', 'exp': 0.0}          # token 只在内存，12h 过期，不落盘


def kb_search(query, top_k=3, kb_ids=None, limit=450):
    backend = (getattr(config, 'KB_BACKEND', 'bailian') or 'bailian').lower()
    kb_ids = kb_ids or (config.KB_LOCAL_IDS if backend == 'local' else config.KB_IDS)
    ck = (backend, str(query), int(top_k), tuple(kb_ids or []), int(limit))
    if ck in _CACHE:                        # ★ 后端进 key：换后端不许串味
        hit = dict(_CACHE[ck])
        hit['cached'] = True
        return hit
    out = _search_local(query, top_k, kb_ids, limit) if backend == 'local' \
        else _search_bailian(query, top_k, kb_ids, limit)
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.clear()
    _CACHE[ck] = out
    return out


# --------------------------------------------------------------------------
# 自建知识库（local）
# --------------------------------------------------------------------------
def _local_login():
    body = json.dumps({'username': config.KB_LOCAL_USER,
                       'password': config.KB_LOCAL_PASSWORD}).encode('utf-8')
    req = urllib.request.Request(config.KB_LOCAL_URL + '/api/v1/auth/login', data=body,
                                 headers={'Content-Type': 'application/json'})
    d = json.loads(urllib.request.urlopen(req, timeout=config.KB_TIMEOUT).read().decode('utf-8', 'replace'))
    _LOCAL['token'] = d['access_token']
    _LOCAL['exp'] = time.time() + float(d.get('expires_in') or 43200) - 300   # 提前 5 分钟换
    return _LOCAL['token']


def _local_post(path, body, retry=True):
    if not _LOCAL['token'] or time.time() >= _LOCAL['exp']:
        _local_login()
    req = urllib.request.Request(config.KB_LOCAL_URL + path,
                                 data=json.dumps(body).encode('utf-8'),
                                 headers={'Content-Type': 'application/json',
                                          'Authorization': 'Bearer ' + _LOCAL['token']})
    try:
        raw = urllib.request.urlopen(req, timeout=config.KB_TIMEOUT).read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        if e.code == 401 and retry:         # token 过期/被顶 → 重登一次再试
            _local_login()
            return _local_post(path, body, retry=False)
        raise
    return json.loads(raw)


def _search_local(query, top_k, kb_ids, limit):
    body = {'query': query, 'kb_ids': [int(x) for x in kb_ids],
            'top_k': max(1, min(int(top_k), 50))}
    try:
        d = _local_post('/api/v1/search', body)
    except Exception as e:                  # noqa: BLE001 —— 检索不通只降级，不许把问答打成 500
        return {'error': type(e).__name__ + ': ' + str(e)[:200], 'nodes': [], 'total': 0}
    nodes = []
    for n in (d.get('results') or [])[:top_k]:
        head = (n.get('heading_path') or '').strip()
        txt = n.get('content') or ''
        pre = '【' + head + '】\n'
        if head and txt.startswith(pre):    # 同一串路径已在 title 里，不重复喂给模型
            txt = txt[len(pre):]
        nodes.append({
            'score': round(float(n.get('rerank_score') or n.get('fused_score') or 0), 4),
            'doc_name': n.get('doc_name') or '',
            'title': head or (n.get('doc_name') or ''),
            'content': txt[:limit],
        })
    return {'total': d.get('total'), 'cost_time': d.get('took_ms'), 'nodes': nodes}


# --------------------------------------------------------------------------
# 阿里云百炼（bailian，原实现，未改动）
# --------------------------------------------------------------------------
def _search_bailian(query, top_k, kb_ids, limit):
    body = {'agent_id': config.KB_AGENT_ID, 'query': query, 'images': []}
    if kb_ids:
        body['kb_search_configs'] = [{'id': i} for i in kb_ids]
    req = urllib.request.Request(config.KB_SEARCH_URL, data=json.dumps(body).encode('utf-8'),
                                 headers={'Authorization': 'Bearer ' + config.LLM_KEY,
                                          'Content-Type': 'application/json'})
    try:
        raw = urllib.request.urlopen(req, timeout=config.KB_TIMEOUT, context=_ctx).read().decode('utf-8', 'replace')
        d = json.loads(raw)
    except Exception as e:
        return {'error': type(e).__name__ + ': ' + str(e)[:200], 'nodes': [], 'total': 0}
    if not d.get('success'):
        return {'error': d.get('code', '') + ' ' + str(d.get('message', ''))[:200], 'nodes': [], 'total': 0}
    data = d.get('data') or {}
    nodes = []
    for n in (data.get('nodes') or [])[:top_k]:
        m = n.get('metadata') or {}
        nodes.append({
            'score': round(float(n.get('score') or 0), 4),
            'doc_name': m.get('doc_name') or m.get('title') or '',
            'title': (m.get('title') or '')[:120],
            'content': (m.get('content') or '')[:limit],
        })
    return {'total': data.get('total'), 'cost_time': data.get('cost_time'), 'nodes': nodes}
