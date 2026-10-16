"""切片质量诊断。

用法:
    python scripts/inspect_chunks.py           # 诊断库中现有切片
    python scripts/inspect_chunks.py --doc 53  # 只看某篇

指标（对应已知问题）:
    prefix_repeat  片内标题前缀【…】出现 >=2 次 —— 短块合并把前缀堆叠了
    garbage        HTML 注释 / 纯符号 / 无信息短块 —— 不该入库的噪声
    multi_point    片内独立行或表格项过多 —— 多个语义点挤在一片
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.models import Document, DocumentChunk  # noqa: E402

PREFIX_RE = re.compile(r"【[^】]{1,200}】")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
MIN_INFO_CHARS = 8
MULTI_POINT_LINES = 6


def _strip_prefix(text: str) -> str:
    return PREFIX_RE.sub("", text or "").strip()


def classify(content: str) -> tuple[bool, bool, bool]:
    """返回 (前缀重复, 垃圾块, 多语义点)。"""
    text = content or ""
    body = _strip_prefix(text)

    prefix_repeat = len(PREFIX_RE.findall(text)) >= 2

    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    compact = re.sub(r"[\s|>\-*#`~]+", "", body)

    garbage = (
        text.lstrip().startswith("<!--")
        or len(compact) < MIN_INFO_CHARS
        or (not CJK_RE.search(body) and len(compact) < 20)
    )
    multi_point = len(lines) >= MULTI_POINT_LINES

    return prefix_repeat, garbage, multi_point


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc", type=int, default=None)
    args = ap.parse_args()

    db = SessionLocal()
    try:
        stmt = select(DocumentChunk).order_by(
            DocumentChunk.doc_id, DocumentChunk.chunk_index
        )
        if args.doc:
            stmt = stmt.where(DocumentChunk.doc_id == args.doc)
        chunks = list(db.execute(stmt).scalars().all())
        names = {
            d.id: d.filename for d in db.execute(select(Document)).scalars().all()
        }
    finally:
        db.close()

    by_doc: dict[int, list] = defaultdict(list)
    for c in chunks:
        by_doc[c.doc_id].append(c)

    print("=" * 84)
    print(f"切片质量诊断：共 {len(chunks)} 片 / {len(by_doc)} 篇文档")
    print("=" * 84)
    print(
        f"{'doc':>5} {'片数':>4} {'均长':>6} {'最长':>6} "
        f"{'前缀重复':>9} {'垃圾块':>8} {'多语义点':>9}  文件名"
    )
    print("-" * 84)

    t_repeat = t_garbage = t_multi = 0
    for doc_id in sorted(by_doc):
        rows = by_doc[doc_id]
        lens = [len(c.content or "") for c in rows]
        flags = [classify(c.content or "") for c in rows]
        n = max(len(rows), 1)
        r = sum(1 for f in flags if f[0])
        g = sum(1 for f in flags if f[1])
        m = sum(1 for f in flags if f[2])
        t_repeat += r
        t_garbage += g
        t_multi += m
        name = (names.get(doc_id) or "")[:24]
        print(
            f"{doc_id:>5} {len(rows):>4} {sum(lens) // n:>6} {max(lens):>6} "
            f"{r:>4}({r * 100 // n:>2}%) {g:>4}({g * 100 // n:>2}%) "
            f"{m:>4}({m * 100 // n:>2}%)  {name}"
        )

    n = max(len(chunks), 1)
    print("-" * 84)
    print(
        f"汇总：前缀重复 {t_repeat}/{n} ({t_repeat * 100 // n}%) | "
        f"垃圾块 {t_garbage}/{n} ({t_garbage * 100 // n}%) | "
        f"多语义点 {t_multi}/{n} ({t_multi * 100 // n}%)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
