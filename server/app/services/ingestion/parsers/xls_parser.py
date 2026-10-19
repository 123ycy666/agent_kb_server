"""Excel 旧版（.xls，BIFF 二进制）解析器。

⭐ 为什么需要它
--------------
`.xlsx` 是 OOXML 格式（本质是 zip 包），`openpyxl` 能读；
但 2003~2007 年的 `.xls` 是**另一种二进制格式**，openpyxl 完全读不了。
企业里存量表格大量是 .xls，让用户「先另存为 .xlsx」在真实场景里往往不现实。

⭐ 逐行展开规则复用 `tabular_common`（与 .xlsx 完全一致）。

⚠️ 依赖注意：`xlrd` 2.x **只支持 .xls**（官方在 2.0 起主动移除了 .xlsx 支持）。
   两个库各管一种格式，正好互补，不要把 xlrd 降级到 1.x。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.services.ingestion.parsers.base import ParsedDocument, ParserError
from app.services.ingestion.parsers.tabular_common import build_tabular_document


def _cell_value(cell: Any, book: Any, xlrd_mod: Any) -> Any:
    """把 xlrd 的 Cell 转成可读文本。

    ⚠️ 日期必须显式转换：xlrd 对日期单元格返回的是**序列号浮点数**，
       直接 str() 会得到 "45000.0" 这种垃圾值混进知识库。
    """
    ctype = cell.ctype
    try:
        if ctype == xlrd_mod.XL_CELL_DATE:
            dt = xlrd_mod.xldate_as_datetime(cell.value, book.datemode)
            if (dt.hour, dt.minute, dt.second) == (0, 0, 0):
                return dt.strftime("%Y-%m-%d")
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        if ctype == xlrd_mod.XL_CELL_BOOLEAN:
            return "是" if cell.value else "否"
        if ctype == xlrd_mod.XL_CELL_ERROR:
            return ""
        if ctype == xlrd_mod.XL_CELL_EMPTY:
            return ""
        return cell.value
    except Exception:  # noqa: BLE001
        return cell.value


def parse_xls(path: Path) -> ParsedDocument:
    try:
        import xlrd
    except ImportError as exc:
        raise ParserError(
            "缺少 .xls 解析依赖 xlrd，请执行：pip install xlrd"
        ) from exc

    try:
        book = xlrd.open_workbook(str(path))
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "encrypted" in msg.lower():
            raise ParserError(
                "该 .xls 文件已加密，无法解析。请在 Excel 中去掉打开密码后重新上传。"
            ) from exc
        raise ParserError(
            f"Excel(.xls) 解析失败：{msg}。"
            "若该文件其实是 .xlsx / .xlsm，请直接上传（同样支持）。"
        ) from exc

    sheets = []
    for sh in book.sheets():
        nrows, ncols = sh.nrows, sh.ncols

        def rows(sh=sh, nrows=nrows, ncols=ncols):
            for r in range(nrows):
                yield [_cell_value(sh.cell(r, c), book, xlrd) for c in range(ncols)]

        sheets.append((sh.name, rows()))

    return build_tabular_document(title=path.stem, sheets=sheets)
