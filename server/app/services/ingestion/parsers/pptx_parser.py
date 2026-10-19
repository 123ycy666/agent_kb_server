"""PPT（.pptx / .pptm）解析器 —— **只用标准库**（zipfile + xml）。

为什么不引 `python-pptx`：
1. 这个模块只需要**读文字**，而 pptx 本质是 zip 包里的一堆 XML，
   `ppt/slides/slideN.xml` 里的 `<a:t>` 就是全部文字，用 stdlib 足够；
2. 内网离线包每多一个依赖就多一份「打包漏了 / 装不上」的风险
   （实测本机 pip 装 python-pptx 就卡在权限上）；
3. 真到**生成** PPT 那一步再引 python-pptx（写要比读复杂得多，那时引才划算）。

⭐ 定位口径：按幻灯片给 heading_path = `第N页 > 标题`。
"第 3 页说了什么"是汇报/验收里最常用的引用方式，比"某处"有用得多。

⚠️ 已知取不到：图片、图表（Chart 的数据在嵌入的 xlsx 里）、备注页 —— 一律不假装能读。
   取不到时在 `meta["warnings"]` 里留一句，由上层转告用户，别让人以为"读全了"。
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from app.services.ingestion.parsers.base import ParsedBlock, ParsedDocument, ParserError

_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_SLIDE_RE = re.compile(r"^ppt/slides/slide(\d+)\.xml$")


def _paragraphs(xml_bytes: bytes) -> list[str]:
    """取一页里的文字：按 `<a:p>` 分段，段内 `<a:t>` 直接拼接。

    按 `<a:p>` 而不是按 `<a:t>` 分行，是因为 PowerPoint 把一句话拆成多个
    run（改字体/加粗就会拆），按 run 分行会把一句断成好几截。
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise ParserError(f"PPT 页面 XML 解析失败：{exc}") from exc

    lines: list[str] = []
    for para in root.iter(f"{_A}p"):
        text = "".join(t.text or "" for t in para.iter(f"{_A}t")).strip()
        if text:
            lines.append(text)
    return lines


def parse_pptx(path: Path) -> ParsedDocument:
    try:
        zf = zipfile.ZipFile(str(path))
    except (zipfile.BadZipFile, OSError) as exc:
        raise ParserError(f"PPT 解析失败（不是有效的 .pptx 文件）：{exc}") from exc

    doc = ParsedDocument(title=path.stem)
    warnings: list[str] = []

    with zf:
        slides: list[tuple[int, str]] = []
        for name in zf.namelist():
            m = _SLIDE_RE.match(name)
            if m:
                slides.append((int(m.group(1)), name))
        slides.sort()                                   # 按页码排，不能按 zip 顺序

        if not slides:
            raise ParserError("该文件中没有幻灯片（可能不是 .pptx/.pptm，或已损坏）")

        chart_count = 0
        picture_count = 0

        for idx, name in slides:
            lines = _paragraphs(zf.read(name))
            # 标题：第一段且不太长才当标题（长段落当标题会把正文顶到标题位置）
            title = lines[0] if lines and len(lines[0]) <= 40 else ""
            heading = f"第{idx}页" + (f" > {title}" if title else "")

            if title:
                doc.blocks.append(
                    ParsedBlock(text=heading, heading_path=None, block_type="heading", level=1)
                )
            for line in lines:
                if title and line == title:
                    continue
                doc.blocks.append(
                    ParsedBlock(text=line, heading_path=heading, block_type="paragraph")
                )

        # 有多少图表/图片：只用于给用户一句"这些没读到"，不在正文里编内容
        for name in zf.namelist():
            if re.match(r"^ppt/charts/chart\d+\.xml$", name):
                chart_count += 1
            elif re.match(r"^ppt/media/", name):
                picture_count += 1

    if doc.is_empty:
        raise ParserError(
            "该 PPT 中未提取到任何文字（可能整页都是截图或图表，没有文字层）"
        )

    if chart_count:
        warnings.append(f"含 {chart_count} 个图表，图表里的数字未解析")
    if picture_count:
        warnings.append(f"含 {picture_count} 个图片/媒体文件，图片中的文字未解析")

    doc.meta["slide_count"] = len(slides)
    doc.meta["chart_count"] = chart_count
    doc.meta["picture_count"] = picture_count
    doc.meta.setdefault("warnings", []).extend(warnings)
    return doc
