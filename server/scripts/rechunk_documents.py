"""按当前切分算法重建全部活跃文档的切片与向量。

背景：切分算法调整后，库里的旧切片与新算法不一致 —— 不重切，
检索质量仍是旧算法的水平。本脚本对每份活跃文档重新解析、重新切分、
重新向量化，替换原有切片（**文档 id 保持不变**，引用与冲突记录不受影响）。

用法：
    python scripts/rechunk_documents.py            # dry-run，只报告
    python scripts/rechunk_documents.py --apply    # 真正执行
    python scripts/rechunk_documents.py --doc 53 --apply
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.core.text import to_search_text  # noqa: E402
from app.models import Document, DocumentChunk, KnowledgeBase  # noqa: E402
from app.providers import get_providers  # noqa: E402
from app.services.ingestion import parsers as P  # noqa: E402
from app.services.ingestion.chunker import chunk_document  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写入（默认 dry-run）")
    ap.add_argument("--doc", type=int, default=None, help="只处理指定文档")
    args = ap.parse_args()

    db = SessionLocal()
    bundle = get_providers()
    model_name = f"{bundle.mode}/{bundle.embedding.model_id}"

    try:
        stmt = select(Document).where(Document.is_active.is_(True)).order_by(Document.id)
        if args.doc:
            stmt = stmt.where(Document.id == args.doc)
        docs = list(db.execute(stmt).scalars().all())

        print(f"待重建 {len(docs)} 份活跃文档 | 目标向量模型 {model_name}")
        print("-" * 72)

        total_old = total_new = 0
        skipped: list[str] = []

        for doc in docs:
            path = Path(doc.storage_path)
            old_n = db.execute(
                select(func.count(DocumentChunk.id)).where(
                    DocumentChunk.doc_id == doc.id
                )
            ).scalar_one()

            if not path.exists():
                skipped.append(f"#{doc.id} 原文件缺失：{doc.filename}")
                continue

            kb = db.get(KnowledgeBase, doc.kb_id)
            try:
                parsed = P.parse_bytes(path.read_bytes(), doc.filename)
                drafts = chunk_document(
                    parsed,
                    chunk_size=kb.chunk_size if kb else 512,
                    chunk_overlap=kb.chunk_overlap if kb else 64,
                )
            except Exception as exc:  # noqa: BLE001
                skipped.append(f"#{doc.id} 解析/切分失败：{exc}")
                continue

            total_old += old_n
            total_new += len(drafts)
            print(
                f"#{doc.id:>4} {doc.filename[:36]:38} {old_n:>3} → {len(drafts):>3} 片"
            )

            if not args.apply:
                continue

            vectors = bundle.embedding.embed_batch([d.content for d in drafts])
            db.query(DocumentChunk).filter(DocumentChunk.doc_id == doc.id).delete(
                synchronize_session=False
            )
            for draft, vec in zip(drafts, vectors, strict=False):
                db.add(
                    DocumentChunk(
                        doc_id=doc.id,
                        kb_id=doc.kb_id,
                        chunk_index=draft.index,
                        content=draft.content,
                        heading_path=draft.heading_path,
                        parent_content=draft.parent_content,
                        embedding_model=model_name,
                        embedding=vec,
                        tags=doc.tags or [],
                        is_retrievable=True,
                        search_text=func.to_tsvector(
                            "simple", to_search_text(draft.content)
                        ),
                    )
                )
            doc.chunk_count = len(drafts)
            db.commit()

        print("-" * 72)
        if args.apply:
            print(f"✅ 重建完成：切片总数 {total_old} → {total_new}")
        else:
            print(f"[dry-run] 切片总数将 {total_old} → {total_new}（未写入任何数据）")
        if skipped:
            print("跳过：")
            for s in skipped:
                print("  -", s)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
