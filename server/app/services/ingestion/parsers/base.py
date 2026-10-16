"""解析结果的数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ParserError(RuntimeError):
    """解析失败。"""


@dataclass
class ParsedBlock:
    """解析出的内容块。

    heading_path 是**检索命中率的关键**：
    把「第三章 > 3.2 报销标准」这样的标题路径拼进切片内容，
    能让「报销标准是多少」这类查询准确命中层级较深的段落。
    """

    text: str
    heading_path: str | None = None
    # paragraph | heading | table | list_item
    block_type: str = "paragraph"
    level: int = 0


@dataclass
class ParsedDocument:
    """一份文档的完整解析结果。"""

    blocks: list[ParsedBlock] = field(default_factory=list)
    title: str | None = None
    # 解析器的额外信息：页数、表格数、是否疑似扫描件等
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def full_text(self) -> str:
        return "\n\n".join(b.text for b in self.blocks if b.text.strip())

    @property
    def char_count(self) -> int:
        return sum(len(b.text) for b in self.blocks)

    @property
    def is_empty(self) -> bool:
        return not any(b.text.strip() for b in self.blocks)

    def with_heading(self, block: ParsedBlock) -> str:
        """把标题路径拼到正文前，供切片使用。"""
        if block.heading_path:
            return f"{block.heading_path}\n{block.text}"
        return block.text
