"""知识库。"""

from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.mixins import TimestampMixin


class KnowledgeBase(Base, TimestampMixin):
    """知识库实体。

    ⚠️ embedding_model 与 embedding_dim 在此绑定：
       切换模型时会校验维度，不一致则强制要求重建索引。
    """

    __tablename__ = "knowledge_base"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    # 向量模型绑定
    embedding_model: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_dim: Mapped[int] = mapped_column(Integer, nullable=False)

    # 切片参数
    chunk_size: Mapped[int] = mapped_column(Integer, nullable=False, default=512)
    chunk_overlap: Mapped[int] = mapped_column(Integer, nullable=False, default=64)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_by: Mapped[int | None] = mapped_column(BigInteger)

    # ─── 是否参与「智能问答」的默认检索范围（2026-10-15 新增）──────────
    #
    # 为什么用「标记」而不是「id 列表」：
    #   id 是自增主键，删除后不复用、新建会拿到更大的值。把 id 写进 .env
    #   或前端默认值，一旦删库/建库就会指向错位（配置悬空或失效）。
    #   改成本字段后：新建库在界面上勾选即生效，删除库自动移出范围，
    #   配置永不漂移；也不依赖「库名」（库名可改、且无唯一约束）。
    qa_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    def __repr__(self) -> str:
        return f"<KnowledgeBase id={self.id} name={self.name!r}>"
