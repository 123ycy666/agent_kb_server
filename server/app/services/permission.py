"""权限强制注入 —— 可见性判定的**唯一出口**。

## 可见性模型（已简化为二值）

- 角色只有两种：`admin` / `user`
- 文档只有两种可见性：`public`（公开）/ `private`（私有）

| 场景 | 管理员 | 普通用户 |
|---|---|---|
| **管理视图**（文档列表 / 详情 / 改 / 删） | 全部文档 | 公开 + 自己上传 |
| **检索视图**（检索 / 问答 / 冲突检测） | 公开 + 自己上传 | 公开 + 自己上传 |

⚠️ **管理员在检索时同样受限** —— 这是刻意设计：

    管理员的「全知」权限只服务于管理事务（排查、处理、删除）。
    若检索也全知，日常问答会被大量与自己无关的私有文档干扰，
    且检索是高频操作，一旦口径放宽就无法收回。
    此处用 `scope` 参数把两种视角显式分开，避免混用。

## 安全铁律

1. 权限条件永远由**后端拼接**，绝不接受调用方传入的过滤条件
2. 检索、详情、下载、冲突检测**复用同一个函数**，保证口径绝对一致
3. 不存在「默认公开」：未显式设为 public 的文档一律私有
"""

from __future__ import annotations

from typing import Literal

from sqlalchemy import ColumnElement, Select, and_, or_, select, true
from sqlalchemy.orm import Session

from app.core.deps import CurrentActor
from app.models import Document

# 两种视角。调用方必须显式选择，避免「顺手用了管理口径」造成越权。
Scope = Literal["manage", "search"]
SCOPE_MANAGE: Scope = "manage"
SCOPE_SEARCH: Scope = "search"


def visible_document_filter(
    actor: CurrentActor, scope: Scope = SCOPE_SEARCH, doc_cls: object = Document
) -> ColumnElement[bool]:
    """返回可直接用于 WHERE 的可见性条件。

    用法::

        stmt = select(Document).where(
            Document.kb_id == kb_id, visible_document_filter(actor, SCOPE_MANAGE)
        )

    Parameters
    ----------
    doc_cls
        目标 Document 实体，或它的 ``aliased()`` 别名。
        冲突检测的 LATERAL 批量召回需要用 ``aliased(Document)`` 复用同一套条件，
        故开放此参数。
        ⚠️ 开放的只是「表对象」，**不是「条件」** —— 权限口径依旧只在本函数拼装，
           不允许调用方自行拼 where 子句（这是越权的高发点）。
    """
    # 管理员在管理视图可见全部
    if scope == SCOPE_MANAGE and actor.is_admin:
        return true()

    conditions: list[ColumnElement[bool]] = [doc_cls.visibility == "public"]  # type: ignore[attr-defined]
    if actor.user_id is not None:
        conditions.append(doc_cls.owner_id == actor.user_id)  # type: ignore[attr-defined]
    return or_(*conditions)


def apply_visibility(
    stmt: Select, actor: CurrentActor, scope: Scope = SCOPE_SEARCH
) -> Select:
    """给任意 Document 查询追加可见性条件。"""
    return stmt.where(visible_document_filter(actor, scope))


def visible_chunk_filter(
    actor: CurrentActor, scope: Scope = SCOPE_SEARCH
) -> ColumnElement[bool]:
    """切片表的可见性条件。

    `is_retrievable` 是「文档是否下架」的缓存（与权限无关），
    这里与文档可见性做**双重校验**，确保绝对一致。
    """
    from app.models import DocumentChunk

    return and_(
        DocumentChunk.is_retrievable.is_(True),
        DocumentChunk.doc_id.in_(
            select(Document.id).where(visible_document_filter(actor, scope))
        ),
    )


def is_document_visible(
    db: Session, actor: CurrentActor, doc_id: int, scope: Scope = SCOPE_SEARCH
) -> bool:
    """单文档可见性判定（详情 / 下载用）。"""
    stmt = select(Document.id).where(
        Document.id == doc_id, visible_document_filter(actor, scope)
    )
    return db.execute(stmt).first() is not None


def can_edit_document(actor: CurrentActor, doc: Document) -> bool:
    """是否可修改/删除该文档。

    规则：**上传者本人 或 管理员**。
    普通用户不能动别人上传的文档（哪怕是公开文档）。
    """
    if actor.is_admin:
        return True
    return actor.user_id is not None and doc.owner_id == actor.user_id


def get_visible_doc_ids(
    db: Session, actor: CurrentActor, kb_id: int | None = None, scope: Scope = SCOPE_SEARCH
) -> list[int]:
    """获取可见文档 id 列表（冲突检测候选集、统计用）。"""
    stmt = select(Document.id).where(visible_document_filter(actor, scope))
    if kb_id is not None:
        stmt = stmt.where(Document.kb_id == kb_id)
    return list(db.execute(stmt).scalars())


def count_visible_documents(
    db: Session, actor: CurrentActor, kb_id: int, scope: Scope = SCOPE_MANAGE
) -> int:
    """统计当前主体在某知识库中可见的文档数（列表页展示用）。

    默认用**管理视图**口径：列表页展示的是「我能看到几份」，
    对管理员而言就是全部。
    """
    from sqlalchemy import func

    stmt = select(func.count(Document.id)).where(
        Document.kb_id == kb_id,
        Document.is_active.is_(True),
        visible_document_filter(actor, scope),
    )
    return int(db.execute(stmt).scalar_one())


def sync_chunk_retrievable(db: Session, doc_id: int) -> int:
    """同步切片的可检索标记。

    ⚠️ 语义边界（曾经踩过坑）：
       `is_retrievable` 表示「该切片在库中是否可用」—— 由文档是否被取代决定；
       **不是**「对某个主体是否可见」—— 那由 `visible_document_filter` 判定。

       早期实现把「是否存在 ACL」当作依据，导致回收授权后文档
       从**所有人**的检索结果中消失（连上传者本人都搜不到），
       而且不报错 —— 典型静默故障。
    """
    from sqlalchemy import update

    from app.models import Document, DocumentChunk

    doc = db.get(Document, doc_id)
    if doc is None:
        return 0

    result = db.execute(
        update(DocumentChunk)
        .where(DocumentChunk.doc_id == doc_id)
        .values(is_retrievable=bool(doc.is_active))
    )
    db.commit()
    return int(result.rowcount or 0)
