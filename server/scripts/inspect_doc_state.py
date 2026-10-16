"""诊断工具：排查「文档删了/传了之后，旧内容检索不到」这类状态漂移。

检查三类可疑状态：
1. 孤儿版本 —— 同一 root_id 下没有任何 is_active=True 的文档（旧版被下架、
   新版又被删 ⇒ 整个版本链永久检索不到）
2. 空壳文档 —— is_active=True 但全部切片 is_retrievable=False（能进列表，搜不到）
3. 冲突挂起残留 —— has_conflict=True 或存在 pending 冲突记录（检索会被硬过滤掉）

用法：python scripts/inspect_doc_state.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.models import ConflictRecord, Document, DocumentChunk


def main() -> None:
    db = SessionLocal()
    try:
        docs = db.execute(select(Document).order_by(Document.kb_id, Document.id)).scalars().all()

        by_root: dict = {}
        for d in docs:
            key = (d.kb_id, d.root_id or d.id)
            by_root.setdefault(key, []).append(d)

        print(f"文档总数：{len(docs)} / 版本链：{len(by_root)}")
        print("-" * 78)

        orphans = [(k, v) for k, v in by_root.items() if not any(x.is_active for x in v)]
        shells: list = []
        conflicted: list = []

        for d in docs:
            total = db.execute(
                select(func.count(DocumentChunk.id)).where(DocumentChunk.doc_id == d.id)
            ).scalar_one()
            live = db.execute(
                select(func.count(DocumentChunk.id)).where(
                    DocumentChunk.doc_id == d.id, DocumentChunk.is_retrievable.is_(True)
                )
            ).scalar_one()
            if d.is_active and live == 0 and total > 0:
                shells.append((d, total))
            if d.has_conflict or d.is_active is False:
                pass
            if d.has_conflict:
                conflicted.append(d)

        print("【1】孤儿版本（同版本链下无活跃文档）")
        if orphans:
            for (kb_id, root_id), group in orphans:
                names = " / ".join(f"#{g.id} v{g.version_no} {g.filename}" for g in group)
                print(f"  kb={kb_id} root={root_id} :: {names}")
        else:
            print("  无")

        print("【2】空壳活跃文档（is_active=True 但切片全被下架）")
        if shells:
            for d, total in shells:
                print(f"  #{d.id} {d.filename}：{total} 个切片全部 is_retrievable=False")
        else:
            print("  无")

        print("【3】冲突挂起文档（has_conflict=True，检索会被硬过滤）")
        if conflicted:
            for d in conflicted:
                print(f"  #{d.id} {d.filename} has_conflict=True is_active={d.is_active}")
        else:
            print("  无")

        pending = db.execute(
            select(func.count(ConflictRecord.id)).where(ConflictRecord.status == "pending")
        ).scalar_one()
        print(f"【4】未处理冲突记录：{pending} 条")
    finally:
        db.close()


if __name__ == "__main__":
    main()
