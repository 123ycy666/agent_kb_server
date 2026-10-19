"""表格类文档的公共解析逻辑（.xlsx 与 .xls 共用）。

⭐ 核心约定：**逐行展开为「列名: 值」，每行一个切片**

直接把整张表拼成一大段文本会丢失行列语义 —— 切片里既有 A 产品的报价、
也有 B 产品的报价，全都糊在一起，「A 产品的报价是多少」这类查询就无法命中。
逐行展开后，检索能精确定位到具体那一条记录。

为什么把这段单独抽出来
--------------------
.xlsx（openpyxl）和 .xls（xlrd）的**读法完全不同**，但「怎么把行变成切片」
的规则必须**完全一致**。若各写一份，两边规则迟早会漂移（比如表头探测阈值
改了只改一边），表现为「同样的表格，xls 能查到、xlsx 查不到」这类怪问题。
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

from app.services.ingestion.parsers.base import ParsedBlock, ParsedDocument, ParserError

# 每个 sheet 最多解析的行数，防止超大表格拖垮入库
MAX_ROWS_PER_SHEET = 5000
# 前 N 行用于探测表头
HEADER_PROBE_ROWS = 5


def is_header_like(row: tuple) -> bool:
    """判断某行是否为表头：非空单元格多、且几乎不含数字。"""
    cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
    if len(cells) < 2:
        return False
    numeric = sum(1 for c in cells if c.replace(".", "", 1).replace("-", "", 1).isdigit())
    return numeric == 0


def build_tabular_document(
    *,
    title: str,
    sheets: Iterable[tuple[str, Iterator[tuple[Any, ...]]]],
) -> ParsedDocument:
    """把若干 sheet 的行流解析成「逐行切片」文档。

    参数
    ----
    title : 文档标题（通常是文件名主干）
    sheets : [(sheet_name, 行迭代器), ...]，行迭代器产出原始单元格元组
    """
    doc = ParsedDocument(title=title)
    sheet_count = 0
    row_count = 0

    for sheet_name, rows in sheets:
        sheet_count += 1
        header: list[str] = []
        parsed_rows = 0

        for row_idx, row in enumerate(rows, start=1):
            if parsed_rows >= MAX_ROWS_PER_SHEET:
                break

            values = ["" if c is None else str(c).strip() for c in row]
            if not any(values):
                continue

            # 前几行内探测表头
            if not header and row_idx <= HEADER_PROBE_ROWS and is_header_like(row):
                header = [v or f"列{i + 1}" for i, v in enumerate(values)]
                doc.blocks.append(
                    ParsedBlock(
                        text=f"[{sheet_name}] 表头：{' | '.join(header)}",
                        heading_path=f"{sheet_name}",
                        block_type="heading",
                        level=1,
                    )
                )
                continue

            # 组装「列名: 值」，跳过空值
            if header and len(header) >= len(values):
                pairs = [
                    f"{header[i]}: {v}" for i, v in enumerate(values) if v and i < len(header)
                ]
            else:
                pairs = [f"列{i + 1}: {v}" for i, v in enumerate(values) if v]

            if not pairs:
                continue

            parsed_rows += 1
            row_count += 1
            doc.blocks.append(
                ParsedBlock(
                    text="；".join(pairs),
                    heading_path=f"{sheet_name} > 第 {row_idx} 行",
                    block_type="table",
                )
            )

    if doc.is_empty:
        raise ParserError("该表格文件中未提取到任何有效数据")

    doc.meta["sheet_count"] = sheet_count
    doc.meta["row_count"] = row_count
    return doc
