"""文档解析器工厂。

支持格式：PDF / Word(.docx) / Excel(.xlsx / .xlsm / .xls) / Markdown / TXT / CSV
⚠️ 不做 OCR —— 所有 PDF 均为纯文本 PDF。
"""

from __future__ import annotations

from pathlib import Path

from app.services.ingestion.parsers.base import (
    ParsedBlock,
    ParsedDocument,
    ParserError,
)

SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".pptx",
    ".pptm",
    ".xlsx",
    ".xlsm",
    ".xls",
    ".md",
    ".markdown",
    ".txt",
    ".csv",
}

_EXT_LABELS = {
    ".pdf": "PDF",
    ".docx": "Word",
    ".pptx": "PPT(2007+)",
    ".pptm": "PPT(宏,2007+)",
    ".xlsx": "Excel(2007+)",
    ".xlsm": "Excel(宏,2007+)",
    ".xls": "Excel(2003 旧版)",
    ".md": "Markdown",
    ".markdown": "Markdown",
    ".txt": "纯文本",
    ".csv": "CSV",
}


def is_supported(filename: str) -> bool:
    return Path(filename).suffix.lower() in SUPPORTED_EXTENSIONS


def supported_formats() -> list[dict[str, str]]:
    return [
        {"ext": ext, "label": _EXT_LABELS.get(ext, ext)}
        for ext in sorted(SUPPORTED_EXTENSIONS)
    ]


def parse_file(path: Path) -> ParsedDocument:
    """按扩展名分发到具体解析器。"""
    ext = path.suffix.lower()

    if ext not in SUPPORTED_EXTENSIONS:
        raise ParserError(
            f"不支持的文件格式：{ext or '(无扩展名)'}。"
            f"支持的格式：{', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )

    if ext == ".pdf":
        from app.services.ingestion.parsers.pdf_parser import parse_pdf

        return parse_pdf(path)

    if ext == ".docx":
        from app.services.ingestion.parsers.docx_parser import parse_docx

        return parse_docx(path)

    if ext in (".pptx", ".pptm"):
        # .pptm 与 .pptx 同为 OOXML（zip），仅多一个宏包，读取方式一致
        from app.services.ingestion.parsers.pptx_parser import parse_pptx

        return parse_pptx(path)

    if ext in (".xlsx", ".xlsm"):
        # .xlsm 与 .xlsx 同为 OOXML（zip）格式，仅多一个宏包，读取方式一致
        from app.services.ingestion.parsers.xlsx_parser import parse_xlsx

        return parse_xlsx(path)

    if ext == ".xls":
        from app.services.ingestion.parsers.xls_parser import parse_xls

        return parse_xls(path)

    # 文本类：md / markdown / txt / csv
    try:
        content = _read_text(path)
    except UnicodeDecodeError as exc:
        raise ParserError(
            f"文本文件编码无法识别（{exc}）。请转换为 UTF-8 编码后重试。"
        ) from exc
    except OSError as exc:
        raise ParserError(f"文件读取失败：{exc}") from exc

    from app.services.ingestion.parsers.text_parser import parse_markdown, parse_text

    if ext in (".md", ".markdown"):
        return parse_markdown(content, filename=path.stem)
    return parse_text(content, filename=path.stem)


def _read_text(path: Path) -> str:
    """读取文本文件，自动尝试多种编码（中文环境常见 GBK）。"""
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "gbk", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise ParserError("无法识别文件编码，请将文件转换为 UTF-8 后重试")


def parse_bytes(content: bytes, filename: str) -> ParsedDocument:
    """直接从字节流解析（用于内存中的上传文件）。

    文本类格式直接解析；Office/PDF 类需要落盘后解析。
    """
    import tempfile

    ext = Path(filename).suffix.lower()
    if ext in (".md", ".markdown", ".txt", ".csv"):
        text = None
        for encoding in ("utf-8", "utf-8-sig", "gb18030", "gbk", "latin-1"):
            try:
                text = content.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise ParserError("无法识别文件编码，请转换为 UTF-8 后重试")

        from app.services.ingestion.parsers.text_parser import (
            parse_markdown,
            parse_text,
        )

        if ext in (".md", ".markdown"):
            return parse_markdown(text, filename=Path(filename).stem)
        return parse_text(text, filename=Path(filename).stem)

    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    try:
        return parse_file(tmp_path)
    finally:
        # ⚠️ 清理动作必须容错：受限环境下 unlink 可能抛 OSError，
        #    不加保护会把真正的解析错误替换成「删除失败」，误导排查方向。
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


__all__ = [
    "SUPPORTED_EXTENSIONS",
    "ParsedBlock",
    "ParsedDocument",
    "ParserError",
    "is_supported",
    "parse_bytes",
    "parse_file",
    "supported_formats",
]
