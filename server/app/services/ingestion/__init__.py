"""文档入库：解析、切片、向量化、落库。"""

from app.services.ingestion.chunker import ChunkDraft, chunk_document, chunk_summary
from app.services.ingestion.pipeline import (
    DuplicateDocumentError,
    IngestError,
    IngestResult,
    ReindexResult,
    compute_hash,
    ingest_document,
    reindex_document,
    reindex_knowledge_base,
    save_upload,
)
from app.services.ingestion import parsers

__all__ = [
    "parsers",
    "ChunkDraft",
    "chunk_document",
    "chunk_summary",
    "ingest_document",
    "reindex_document",
    "reindex_knowledge_base",
    "save_upload",
    "compute_hash",
    "IngestResult",
    "ReindexResult",
    "IngestError",
    "DuplicateDocumentError",
]
