# -*- coding: utf-8 -*-
"""运行配置接口：模型 / 业务库连接 / 步数 / 语句超时。

    GET  /api/v1/runtime/config   当前值（页面上的优先，空 = 用代码默认）
    PUT  /api/v1/runtime/config   保存并立即生效
    POST /api/v1/runtime/test     用当前（或传入的）连接试一次
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.core.deps import CurrentActor, get_current_actor

router = APIRouter(prefix='/runtime', tags=['运行配置'])


def _jr(data, status_code: int = 200):
    return JSONResponse(content=data, status_code=status_code)


@router.get('/config', summary='运行配置当前值 + 代码默认值')
def get_config(actor: CurrentActor = Depends(get_current_actor)):
    from app.secretary import config, runtime_cfg
    runtime_cfg.ensure()
    vals = runtime_cfg.refresh()
    return _jr({
        'ok': True,
        'values': vals,
        'defaults': {
            'model': config.MODEL,
            'max_steps': config.MAX_STEPS,
            'sql_timeout_ms': config.SQL_TIMEOUT_MS,
            'tool_result_chars': config.TOOL_RESULT_CHARS,
            'db_host': config.DB.get('host'),
            'db_port': config.DB.get('port'),
            'db_name': config.DB.get('db'),
            'db_user': config.DB.get('user'),
        },
        'models': config.MODELS,
        'keys': runtime_cfg.KEYS,
    })


@router.put('/config', summary='保存运行配置（空串 = 恢复代码默认）')
async def save_config(request: Request, actor: CurrentActor = Depends(get_current_actor)):
    from app.secretary import runtime_cfg
    try:
        body = await request.json()
    except Exception:                                 # noqa: BLE001
        return _jr({'error': '请求体不是 JSON'}, 400)
    if not isinstance(body, dict):
        return _jr({'error': '请求体必须是 JSON 对象'}, 400)
    res = runtime_cfg.save(body, user=actor.username)
    return _jr(res, 200 if res.get('ok') else 500)


@router.post('/test', summary='试连业务库')
async def test(request: Request, actor: CurrentActor = Depends(get_current_actor)):
    from app.secretary import runtime_cfg
    try:
        body = await request.json()
    except Exception:                                 # noqa: BLE001
        body = {}
    return _jr(runtime_cfg.test_conn(body if isinstance(body, dict) else {}))
