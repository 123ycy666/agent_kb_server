"""纯文本 / Markdown 解析器（无第三方依赖）。

Markdown 会识别标题层级，构建 heading_path —— 这是提升检索命中率的关键。
"""

from __future__ import annotations

import re

from app.services.ingestion.parsers.base import ParsedBlock, ParsedDocument, ParserError

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_MD_LIST = re.compile(r"^\s*([-*+]|\d+\.)\s+(.*)$")
_MD_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")


def _strip_md_inline(text: str) -> str:
    """去掉行内 Markdown 标记，保留可读文本。"""
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    return text.strip()


def parse_markdown(content: str, filename: str = "") -> ParsedDocument:
    """解析 Markdown，构建标题层级路径。"""
    # 剥离 HTML 注释：上传工具常把元数据写成 <!-- doc_type: 学习资料 -->，
    # 这些内容没有任何检索价值，留着只会变成干扰切片（实测会独立成片）。
    content = re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL)
    content = re.sub(r"<!--.*$", "", content, flags=re.DOTALL)  # 未闭合的注释

    doc = ParsedDocument(title=filename)
    # 标题栈：level → 标题文本
    heading_stack: dict[int, str] = {}

    lines = content.splitlines()
    buffer: list[str] = []
    table_buffer: list[str] = []
    in_code_block = False

    def flush_paragraph() -> None:
        if buffer:
            text = "\n".join(buffer).strip()
            if text:
                doc.blocks.append(
                    ParsedBlock(
                        text=_strip_md_inline(text),
                        heading_path=current_path(),
                        block_type="paragraph",
                    )
                )
            buffer.clear()

    def flush_table() -> None:
        if table_buffer:
            text = "\n".join(table_buffer).strip()
            if text:
                doc.blocks.append(
                    ParsedBlock(
                        text=text,
                        heading_path=current_path(),
                        block_type="table",
                    )
                )
            table_buffer.clear()

    def current_path() -> str | None:
        if not heading_stack:
            return None
        parts = [heading_stack[k] for k in sorted(heading_stack)]
        return " > ".join(parts)

    for raw_line in lines:
        line = raw_line.rstrip()

        # 代码块整体保留
        if line.strip().startswith("```"):
            flush_paragraph()
            flush_table()
            in_code_block = not in_code_block
            if in_code_block:
                buffer.append("```")
            else:
                buffer.append("```")
                text = "\n".join(buffer).strip()
                if text and text != "```":
                    doc.blocks.append(
                        ParsedBlock(text=text, heading_path=current_path(), block_type="code")
                    )
                buffer.clear()
            continue

        if in_code_block:
            buffer.append(line)
            continue

        # 标题
        m = _MD_HEADING.match(line)
        if m:
            flush_paragraph()
            flush_table()
            level = len(m.group(1))
            title = _strip_md_inline(m.group(2))
            # 清理比当前层级更深的标题
            heading_stack = {k: v for k, v in heading_stack.items() if k < level}
            heading_stack[level] = title
            if doc.title in (None, filename):
                doc.title = title
            doc.blocks.append(
                ParsedBlock(
                    text=title,
                    heading_path=" > ".join(
                        heading_stack[k] for k in sorted(heading_stack) if k < level
                    )
                    or None,
                    block_type="heading",
                    level=level,
                )
            )
            continue

        # 表格
        if _MD_TABLE_ROW.match(line):
            flush_paragraph()
            table_buffer.append(line)
            continue
        elif table_buffer:
            flush_table()

        # 空行 → 段落分隔
        if not line.strip():
            flush_paragraph()
            continue

        # 列表项单独成块，保留列表语义
        lm = _MD_LIST.match(line)
        if lm:
            flush_paragraph()
            doc.blocks.append(
                ParsedBlock(
                    text=_strip_md_inline(lm.group(2)),
                    heading_path=current_path(),
                    block_type="list_item",
                )
            )
            continue

        buffer.append(line)

    flush_paragraph()
    flush_table()

    if doc.is_empty:
        raise ParserError("Markdown 内容为空或不含可提取文本")

    doc.meta["heading_count"] = sum(1 for b in doc.blocks if b.block_type == "heading")
    return doc


def parse_text(content: str, filename: str = "") -> ParsedDocument:
    """解析纯文本：按空行分段。"""
    doc = ParsedDocument(title=filename)
    paragraphs = re.split(r"\n\s*\n", content)

    for para in paragraphs:
        text = para.strip()
        if text:
            doc.blocks.append(ParsedBlock(text=text, block_type="paragraph"))

    if doc.is_empty:
        raise ParserError("文本内容为空")

    return doc
