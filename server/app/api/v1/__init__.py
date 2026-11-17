"""API v1 路由汇总。

⚠️ 对外契约：所有接口一经发布，**新增字段保持兼容，不删除、不改语义**。
   破坏性变更走 /api/v2。
"""

from fastapi import APIRouter

from app.api.v1 import (
    audit,
    auth,
    caliber,
    catalog,
    conflicts,
    documents,
    eval as eval_api,
    inject,
    kb,
    models,
    prompts,
    roles,
    runtime,
    schema_map,
    search,
    specs,
    system,
    tags,
    tasks,
)

api_router = APIRouter()

api_router.include_router(auth.router)
api_router.include_router(kb.router)
api_router.include_router(catalog.router)
api_router.include_router(documents.router)
api_router.include_router(tags.router)
api_router.include_router(search.router)
api_router.include_router(tasks.router)
api_router.include_router(conflicts.router)
api_router.include_router(audit.router)
api_router.include_router(roles.router)
api_router.include_router(models.router)
api_router.include_router(system.router)
api_router.include_router(prompts.router)
api_router.include_router(runtime.router)
api_router.include_router(caliber.router)
api_router.include_router(specs.router)
api_router.include_router(inject.router)
api_router.include_router(schema_map.router)
api_router.include_router(eval_api.router)

__all__ = ["api_router"]
