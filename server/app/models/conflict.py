"""冲突记录。

冲突检测是本系统的核心差异化能力：切片级检测、文档级呈现。

⚠️ 关键约束（对应设计方案 10.4）
1. 绝不让 LLM 凭空判断冲突 —— 必须先检索、再判定（Grounded），否则必然幻觉
2. 冲突 ≠ 相似 —— 必须做关系分类（NLI 任务），而非只看相似度分数
3. 复杂度必须控制 —— 只对新切片取 Top-K 候选，复杂度 O(N×K) 而非 O(N×M)
4. 关系类型必须在 Prompt 中写死，尤其要区分「时效型更新」与「真冲突」
5. LLM 输出必须结构化（JSON Schema）+ 低温度，否则判定不稳定
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# 冲突类型
CONFLICT_TYPES = ("fact", "rule", "temporal", "wording")
# 关系分类
RELATIONS = ("contradiction", "entailment", "duplication", "unrelated")
# 处理方式
RESOLUTIONS = ("overwrite", "keep_old", "coexist", "cancel", "ignore")


class ConflictRecord(Base):
    """冲突记录。"""

    __tablename__ = "conflict_record"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    new_doc_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("document.id", ondelete="CASCADE"), nullable=False
    )
    old_doc_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("document.id", ondelete="CASCADE"), nullable=False
    )
    new_chunk_id: Mapped[int | None] = mapped_column(BigInteger)
    old_chunk_id: Mapped[int | None] = mapped_column(BigInteger)

    # fact(事实型) | rule(规则型) | temporal(时效型) | wording(表述型)
    conflict_type: Mapped[str] = mapped_column(String(32), nullable=False, default="fact")
    # contradiction | entailment | duplication | unrelated
    relation: Mapped[str] = mapped_column(String(32), nullable=False, default="contradiction")

    similarity_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    rerank_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))

    # 判定理由与对照文本，供人工决策时展示
    reason: Mapped[str | None] = mapped_column(Text)
    new_snippet: Mapped[str | None] = mapped_column(Text)
    old_snippet: Mapped[str | None] = mapped_column(Text)

    # overwrite | keep_old | coexist | cancel | ignore
    resolution: Mapped[str | None] = mapped_column(String(32))
    resolved_by: Mapped[int | None] = mapped_column(BigInteger)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # pending | resolved | ignored
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        Index("idx_conflict_newdoc", "new_doc_id"),
        Index("idx_conflict_olddoc", "old_doc_id"),
        Index("idx_conflict_kb", "kb_id", "status"),
        Index("idx_conflict_status", "status", postgresql_where="status = 'pending'"),
    )

    def __repr__(self) -> str:
        return f"<ConflictRecord id={self.id} {self.relation} new={self.new_doc_id} old={self.old_doc_id}>"
