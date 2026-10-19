"""标签（词表）。

## 已大幅简化

原来的「标签维度 / 值类型 / 枚举值 / 命名空间 / 同义检测 / 合并废弃」全部去掉 ——
实际需求只是**给文档挂几个说明性标签**（如「行政」「预算」）。

现在：

- 标签就是**字符串**
- 文档与标签的关系直接存在 `document.tags`（字符串数组）上
- 本表只做两件事：
  ① 记录「系统里用过哪些标签」→ 前端下拉可选，同时允许自由输入新标签
  ② 统计使用次数，支持重命名/清理

⚠️ 为什么不再用独立的关联表（document_tag）:
   在「标签即字符串」的模型下，那层间接性是纯负担 ——
   检索过滤要 JOIN，写入要两次操作，还得处理关联表的一致性。
   数组列 + GIN 索引能直接完成 `tags && ARRAY['行政']` 的过滤，更简单也更快。
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.mixins import TimestampMixin


class Tag(Base, TimestampMixin):
    """标签词条。"""

    __tablename__ = "tag"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # 标签名，全局唯一（按名称去重，避免「行政」「行政 」并存）
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(String(256))
    # 使用次数：用于列表排序（常用标签靠前）+ 清理长期未使用的标签
    usage_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[int | None] = mapped_column(BigInteger)

    __table_args__ = (Index("idx_tag_usage", "usage_count"),)

    def __repr__(self) -> str:
        return f"<Tag name={self.name!r} used={self.usage_count}>"
