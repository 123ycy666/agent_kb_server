"""系统信息与健康检查。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.config import settings
from app.core.database import check_connection
from app.core.deps import CurrentActor, get_current_actor
from app.providers import get_providers
from app.services.ingestion import parsers

router = APIRouter(prefix="/system", tags=["系统"])


@router.get("/health", summary="健康检查（无需认证）")
def health():
    db_ok, db_detail = check_connection()
    bundle = get_providers()

    return {
        "status": "ok" if db_ok else "degraded",
        "app": settings.app_name,
        "env": settings.app_env,
        "database": {"ok": db_ok, "detail": db_detail},
        "provider": {
            "mode": bundle.mode,
            "embedding": bundle.embedding.model_id,
            "rerank": bundle.rerank.model_id,
            "llm": bundle.llm.model_id,
        },
    }


@router.get("/formats", summary="支持的文档格式")
def formats():
    return {
        "extensions": sorted(parsers.SUPPORTED_EXTENSIONS),
        "formats": parsers.supported_formats(),
        "ocr": False,
        "note": "本期不支持 OCR，PDF 需为纯文本格式。",
    }


@router.get("/info", summary="系统概览")
def info(_: CurrentActor = Depends(get_current_actor)):
    bundle = get_providers()
    return {
        "app": settings.app_name,
        "env": settings.app_env,
        "provider_mode": bundle.mode,
        "provider_summary": settings.provider_summary(),
        "embedding_dim": settings.embedding_dim,
        "max_upload_mb": settings.max_upload_mb,
        "supported_formats": sorted(parsers.SUPPORTED_EXTENSIONS),
    }


@router.get("/settings", summary="设置页只读信息：员工源与存储")
def settings_view(_: CurrentActor = Depends(get_current_actor)):
    from app.core.database import check_mysql_connection
    from app.secretary import doc_files

    ok, detail = check_mysql_connection()
    return {
        "employee_source": {
            "kind": "MySQL",
            "host": settings.mysql_host,
            "port": settings.mysql_port,
            "database": settings.mysql_db_business,
            "table": "t_employee",
            "ok": ok,
            "detail": detail,
        },
        "storage": {
            "upload_dir": str(settings.upload_path),
            "output_dir": str(doc_files.ROOT),
            "max_upload_mb": settings.max_upload_mb,
        },
    }
