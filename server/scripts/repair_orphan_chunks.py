"""巡检并修复「孤儿隔离切片」。

场景
----
切片因冲突/覆盖被隔离（is_retrievable=False），但**引发隔离的对象已消失**
（冲突记录被级联删除、取代它的新文档被删除），隔离状态却没人解除 ——
该内容永久检索不到。这正是「覆盖后删除新文件，旧内容依旧搜不到」的残留。

判定（满足任一即为孤儿）
-----------------------
A) retired_reason='conflict'  → 已无 pending 冲突指向它，且所属文档 has_conflict=False
B) retired_reason='overwrite' → superseded_by 指向的文档不存在或非活跃
C) retired_reason='version'   → **不动**（由 rollback_superseded 按版本链负责）

用法
----
    python scripts/repair_orphan_chunks.py            # dry-run（默认）
    python scripts/repair_orphan_chunks.py --apply
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.models import ConflictRecord, Document, DocumentChunk  # noqa: E402


def find_orphans(db) -> list[tuple[DocumentChunk, Document | None, str]]:
    """返回 [(切片, 所属文档, 判定原因)]。"""
    chunks = (
        db.execute(
            select(DocumentChunk).where(
                DocumentChunk.is_retrievable.is_(False),
                DocumentChunk.retired_reason.in_(("conflict", "overwrite")),
            )
        )
        .scalars()
        .all()
    )
    if not chunks:
        return []

    pending = db.execute(
        select(ConflictRecord).where(ConflictRecord.status == "pending")
    ).scalars().all()
    pending_chunks = {c.new_chunk_id for c in pending if c.new_chunk_id}
    pending_docs = {c.new_doc_id for c in pending if c.new_doc_id}

    active_docs = {
        d.id
        for d in db.execute(
            select(Document).where(Document.is_active.is_(True))
        ).scalars().all()
    }

    out: list[tuple[DocumentChunk, Document | None, str]] = []
    for ch in chunks:
        doc = db.get(Document, ch.doc_id)
        if ch.retired_reason == "conflict":
            if ch.id in pending_chunks or ch.doc_id in pending_docs:
                continue
            if doc is not None and doc.has_conflict:
                continue
            out.append((ch, doc, "冲突隔离，但已无待处理冲突"))
        else:  # overwrite
            if ch.superseded_by is not None and ch.superseded_by in active_docs:
                continue
            out.append(
                (ch, doc, f"被 #{ch.superseded_by} 覆盖，但该文档已不存在/非活跃")
            )
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正执行（默认 dry-run）")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        orphans = find_orphans(db)
        if not orphans:
            print("✅ 未发现孤儿隔离切片")
            return 0

        print("=" * 76)
        print(f"发现孤儿隔离切片 {len(orphans)} 个")
        print("=" * 76)
        for ch, doc, why in orphans:
            name = doc.filename if doc else "(文档已不存在)"
            print(
                f"  切片 #{ch.id}  doc#{ch.doc_id} {name[:26]}\n"
                f"      reason={ch.retired_reason} superseded_by={ch.superseded_by}\n"
                f"      判定：{why}\n"
                f"      内容：{(ch.content or '')[:60]!r}"
            )

        if not args.apply:
            print("\n以上为 dry-run，未改动数据。确认无误后加 --apply 执行。")
            return 0

        for ch, _doc, _why in orphans:
            ch.is_retrievable = True
            ch.retired_reason = None
            ch.superseded_by = None
        db.commit()
        print(f"\n✅ 已恢复 {len(orphans)} 个切片")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
