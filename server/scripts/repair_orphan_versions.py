"""存量修复：把「孤儿版本链」与「空壳活跃文档」恢复成可检索状态。

背景（2026-10-08 定位的缺陷）
----------------------------
下架旧版时系统只做了单向操作（is_active=False + 旧 chunk is_retrievable=False），
却没有记录「谁取代了我」（superseded_by）。一旦取代者被删除，旧版就永久不可检索，
且没有任何恢复入口。

本脚本做两件事：
1. 孤儿版本链 —— 同一 root_id 下已无任何活跃文档 ⇒ 复活版本号最大的那个
2. 空壳活跃文档 —— is_active=True 但全部切片被下架 ⇒ 复活其切片

用法
----
    python scripts/repair_orphan_versions.py            # dry-run，只打印清单（默认）
    python scripts/repair_orphan_versions.py --apply    # 真正执行

⚠️ dry-run 是默认行为。数据修复不应默认生效。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.models import Document, DocumentChunk
from app.providers import get_providers


def _chunk_stats(db, doc_id: int) -> tuple[int, int]:
    total = db.execute(
        select(func.count(DocumentChunk.id)).where(DocumentChunk.doc_id == doc_id)
    ).scalar_one()
    live = db.execute(
        select(func.count(DocumentChunk.id)).where(
            DocumentChunk.doc_id == doc_id, DocumentChunk.is_retrievable.is_(True)
        )
    ).scalar_one()
    return total, live


def scan(db) -> tuple[list, list]:
    """扫描两类可修复状态。"""
    docs = db.execute(select(Document).order_by(Document.kb_id, Document.id)).scalars().all()

    by_root: dict = {}
    for d in docs:
        by_root.setdefault((d.kb_id, d.root_id or d.id), []).append(d)

    # ① 孤儿版本链：链内无活跃文档 → 复活版本号最大者
    orphans = []
    for (kb_id, root_id), group in by_root.items():
        if any(x.is_active for x in group):
            continue
        target = max(group, key=lambda x: (x.version_no, x.id))
        orphans.append((kb_id, root_id, group, target))

    # ② 空壳活跃文档：活跃但切片全下架
    shells = []
    for d in docs:
        if not d.is_active:
            continue
        total, live = _chunk_stats(db, d.id)
        if total > 0 and live == 0:
            shells.append((d, total))

    return orphans, shells


def repair(db, orphans, shells, apply: bool) -> None:
    bundle = get_providers()
    current_model = f"{bundle.mode}/{bundle.embedding.model_id}"

    for kb_id, root_id, group, target in orphans:
        others = ", ".join(f"#{g.id} v{g.version_no}" for g in group if g.id != target.id)
        total, _ = _chunk_stats(db, target.id)
        print(f"\n[孤儿版本链] kb={kb_id} root={root_id}")
        print(f"  复活 → #{target.id} v{target.version_no} 《{target.filename}》切片 {total} 个")
        print(f"  同链其他版本：{others or '无'}")
        stale = db.execute(
            select(func.count(DocumentChunk.id)).where(
                DocumentChunk.doc_id == target.id,
                DocumentChunk.embedding_model != current_model,
            )
        ).scalar_one()
        if stale:
            print(f"  ⚠️ 有 {stale} 个切片向量模型与当前档位（{current_model}）不符，")
            print(f"     复活后仍检索不到，需执行重建索引。")
        if apply:
            target.is_active = True
            target.retired_reason = None
            target.superseded_by = None
            db.query(DocumentChunk).filter(DocumentChunk.doc_id == target.id).update(
                {DocumentChunk.is_retrievable: True}, synchronize_session=False
            )
            print("  ✅ 已执行")

    for doc, total in shells:
        print(f"\n[空壳活跃文档] #{doc.id} 《{doc.filename}》切片 {total} 个全部下架")
        print(f"  复活 → is_active=True，切片 is_retrievable=True")
        if apply:
            doc.is_active = True
            doc.retired_reason = None
            db.query(DocumentChunk).filter(DocumentChunk.doc_id == doc.id).update(
                {DocumentChunk.is_retrievable: True}, synchronize_session=False
            )
            print("  ✅ 已执行")

    if apply:
        db.commit()
        print("\n已提交事务。")
    else:
        print("\n" + "=" * 60)
        print("以上为 dry-run，未做任何改动。确认无误后加 --apply 执行。")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正执行修复（默认只 dry-run）")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        orphans, shells = scan(db)
        if not orphans and not shells:
            print("未发现孤儿版本链或空壳活跃文档，无需修复。")
            return
        repair(db, orphans, shells, apply=args.apply)
    finally:
        db.close()


if __name__ == "__main__":
    main()