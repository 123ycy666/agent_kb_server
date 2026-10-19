"""标签治理（极简版）。

## 设计变更

原实现是一套「标签维度 / 值类型 / 枚举约束 / 同义检测 / 命名空间 / 合并废弃」
的重型治理体系，对应的是「标签作为结构化元数据」的场景。

实际需求是「给文档挂几个说明性标签」（如「行政」「预算」），
标签就是**自由字符串**。因此这里只保留三件必要的事：

1. **规范化**：去空白、全角转半角、长度与字符校验
2. **去重**：同一文档内大小写不敏感去重，避免「预算 / 预算 」并存
3. **词表同步**：把用过的标签登记到 `tag` 表，供前端下拉复用

## 为什么仍需「规范化」

标签一旦脏化（多一个空格、全角括号），按标签过滤会**静默失效** ——
不报错，就是筛不出来。所以写入前必须收敛到规范形式，
这是整个标签功能可靠性的前提。
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Tag

# 单个文档最多挂多少个标签（防止前端误传超长数组撑爆存储与过滤）
MAX_TAGS_PER_DOC = 10
# 单个标签最大长度
MAX_TAG_LENGTH = 32


class TagValidationError(ValueError):
    """标签校验失败。"""


def normalize_tag(raw: str) -> str:
    """规范化单个标签名。

    - 全角 → 半角（NFKC）
    - 去除首尾空白，内部连续空白折叠为一个半角空格
    - 去除控制字符

    ⚠️ **不做大小写转换**：「预算」与「Budget」都应保留原样，
       后者若转小写会失去可读性。去重时用 casefold 比较即可。
    """
    if raw is None:
        raise TagValidationError("标签不能为空")

    name = unicodedata.normalize("NFKC", str(raw))
    # 去掉控制字符（含零宽字符）
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C")
    name = " ".join(name.split())

    if not name:
        raise TagValidationError("标签不能为空")
    if len(name) > MAX_TAG_LENGTH:
        raise TagValidationError(f"标签过长（最多 {MAX_TAG_LENGTH} 字符）：{name[:20]}…")
    return name


def normalize_tags(
    raw_tags: Iterable[str] | None, *, max_count: int = MAX_TAGS_PER_DOC
) -> tuple[list[str], list[str]]:
    """批量规范化 + 去重。

    返回 `(规范化后的标签列表, 警告列表)`。

    非法标签（空 / 超长）**跳过并记警告**，而不是整体报错 ——
    用户批量粘贴标签时，不应因为一个笔误导致整次上传失败。
    超数量则截断并警告。
    """
    if not raw_tags:
        return [], []

    seen: set[str] = set()
    result: list[str] = []
    warnings: list[str] = []

    for raw in raw_tags:
        if raw is None:
            continue
        if str(raw).strip() == "":
            # 显式传了空串（而非 None）时给个提示，便于前端发现输入框残留
            warnings.append("空标签已忽略")
            continue
        try:
            name = normalize_tag(raw)
        except TagValidationError as exc:
            warnings.append(str(exc))
            continue

        key = name.casefold()
        if key in seen:
            continue
        if len(result) >= max_count:
            warnings.append(f"标签数量超过上限 {max_count}，已忽略多余标签")
            break

        seen.add(key)
        result.append(name)

    return result, warnings


def sync_tag_vocabulary(db: Session, tags: list[str], *, created_by: int | None = None) -> None:
    """把标签登记进词表，已存在的累加使用次数。

    ⚠️ 只 flush 不 commit —— 由调用方（入库流水线）统一提交，
       保证「文档 + 标签词表」在同一事务里，避免词表被写脏。
    """
    if not tags:
        return

    wanted = {t.casefold(): t for t in tags}
    existing = {
        row.name.casefold(): row
        for row in db.execute(select(Tag).where(Tag.name.in_(list(wanted.values())))).scalars()
    }

    for key, display in wanted.items():
        row = existing.get(key)
        if row is None:
            db.add(Tag(name=display, usage_count=1, created_by=created_by))
        else:
            row.usage_count += 1


def release_tag_usage(db: Session, tags: list[str]) -> None:
    """文档删除时回收标签使用计数（不为负）。"""
    if not tags:
        return
    rows = db.execute(select(Tag).where(Tag.name.in_(tags))).scalars().all()
    for row in rows:
        row.usage_count = max(0, row.usage_count - 1)


def list_vocabulary(db: Session, *, limit: int = 200) -> list[Tag]:
    """按使用次数倒序列出词表（前端下拉用）。"""
    return list(
        db.execute(select(Tag).order_by(Tag.usage_count.desc(), Tag.name).limit(limit))
        .scalars()
        .all()
    )


def rename_tag(db: Session, *, old_name: str, new_name: str) -> int:
    """重命名标签，并同步改写所有文档 / 切片上的标签值。

    返回受影响的文档数。
    """
    new = normalize_tag(new_name)
    old = normalize_tag(old_name)
    if new.casefold() == old.casefold():
        return 0

    from app.models import Document, DocumentChunk

    affected = 0
    for doc in db.execute(select(Document).where(Document.tags.any(old))).scalars():
        doc.tags = [new if t == old else t for t in (doc.tags or [])]
        affected += 1
    for chunk in db.execute(select(DocumentChunk).where(DocumentChunk.tags.any(old))).scalars():
        chunk.tags = [new if t == old else t for t in (chunk.tags or [])]

    row = db.execute(select(Tag).where(Tag.name == old)).scalar_one_or_none()
    if row is not None:
        row.name = new

    db.commit()
    return affected


def delete_tag(db: Session, *, name: str) -> int:
    """从词表删除标签，并从所有文档 / 切片上摘掉。

    返回受影响文档数。删除词表条目不等于删除历史文档内容，仅收回标签。
    """
    old = normalize_tag(name)

    from app.models import Document, DocumentChunk

    affected = 0
    for doc in db.execute(select(Document).where(Document.tags.any(old))).scalars():
        doc.tags = [t for t in (doc.tags or []) if t != old]
        affected += 1
    for chunk in db.execute(select(DocumentChunk).where(DocumentChunk.tags.any(old))).scalars():
        chunk.tags = [t for t in (chunk.tags or []) if t != old]

    row = db.execute(select(Tag).where(Tag.name == old)).scalar_one_or_none()
    if row is not None:
        db.delete(row)

    db.commit()
    return affected
