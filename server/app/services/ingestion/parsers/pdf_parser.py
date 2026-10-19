"""PDF 解析器。

⚠️ 本期已确认：所有 PDF 均为**纯文本 PDF**，不上 OCR。
   因此若解析出的文本极少，会给出「疑似扫描件」的提示，
   而不是静默产出空文档 —— 否则用户会以为上传成功了，实际什么都没入库。
"""

from __future__ import annotations

from pathlib import Path

from app.services.ingestion.parsers.base import ParsedBlock, ParsedDocument, ParserError

# 每页提取到的字符数低于该阈值，判定为疑似扫描件
_SCAN_SUSPECT_THRESHOLD = 30


def parse_pdf(path: Path) -> ParsedDocument:
    try:
        import pdfplumber
    except ImportError as exc:
        raise ParserError(
            "缺少 PDF 解析依赖 pdfplumber，请执行：pip install pdfplumber"
        ) from exc

    doc = ParsedDocument(title=path.stem)
    page_texts: list[str] = []
    table_count = 0

    try:
        with pdfplumber.open(str(path)) as pdf:
            doc.meta["page_count"] = len(pdf.pages)

            for page_no, page in enumerate(pdf.pages, start=1):
                # 表格单独抽取并保留为结构化文本
                try:
                    tables = page.extract_tables() or []
                except Exception:  # noqa: BLE001 - 表格抽取失败不应中断整篇解析
                    tables = []

                for table in tables:
                    rows = [
                        " | ".join((cell or "").replace("\n", " ").strip() for cell in row)
                        for row in table
                        if row
                    ]
                    if len(rows) >= 2:
                        table_count += 1
                        doc.blocks.append(
                            ParsedBlock(
                                text="\n".join(rows),
                                heading_path=f"第 {page_no} 页 表格",
                                block_type="table",
                            )
                        )

                text = page.extract_text() or ""
                text = text.strip()
                if text:
                    page_texts.append(text)
                    doc.blocks.append(
                        ParsedBlock(
                            text=text,
                            heading_path=f"第 {page_no} 页",
                            block_type="paragraph",
                        )
                    )
    except ParserError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ParserError(f"PDF 解析失败：{exc}") from exc

    doc.meta["table_count"] = table_count

    if doc.is_empty:
        raise ParserError(
            "未能从该 PDF 中提取到文本，可能是扫描件或图片型 PDF。"
            "本期不支持 OCR，请提供纯文本 PDF。"
        )

    # 疑似扫描件提示（有页面但几乎没有文字）
    page_count = doc.meta.get("page_count", 0)
    if page_count and doc.char_count / max(page_count, 1) < _SCAN_SUSPECT_THRESHOLD:
        doc.meta["scan_suspect"] = True

    # 首行作为标题
    first_line = (page_texts[0].splitlines() or [""])[0].strip()
    if first_line and len(first_line) <= 80:
        doc.title = first_line

    return doc
