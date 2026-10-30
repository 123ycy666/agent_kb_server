# -*- coding: utf-8 -*-
"""md2docx_guide.py —— 把《小智使用说明》的 Markdown 源转成排版好的 Word。

只处理本说明用到的语法：# 标题 / ## 节 / **加粗** 段落 / > 引文 / - 列表 / | 表格 |。
排版：A4、2.2cm 页边距、宋体正文小四、黑体标题、1.5 倍行距、表格网格线 + 表头底纹、页脚页码。

用法：py -X utf8 output\\md2docx_guide.py <源.md> <输出.docx>
"""
import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

BODY_FONT = '宋体'
HEAD_FONT = '黑体'


def set_run_font(run, name=BODY_FONT, size=12, bold=False, color=None):
    run.font.name = name
    run.font.size = Pt(size)
    run.bold = bold
    if color:
        run.font.color.rgb = RGBColor(*color)
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn('w:rFonts'))
    if rfonts is None:
        rfonts = OxmlElement('w:rFonts')
        rpr.append(rfonts)
    for attr in ('w:ascii', 'w:hAnsi', 'w:eastAsia', 'w:cs'):
        rfonts.set(qn(attr), name)


def add_paragraph(doc, text, *, size=10.5, bold=False, first_indent=None,
                  space_before=0, space_after=2, align=None, color=None,
                  line=1.34, font=BODY_FONT):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.line_spacing_rule = WD_LINE_SPACING.MULTIPLE
    pf.line_spacing = line
    pf.space_before = Pt(space_before)
    pf.space_after = Pt(space_after)
    if first_indent is not None:
        pf.first_line_indent = first_indent
    if align is not None:
        p.alignment = align
    for seg, is_bold in split_bold(text):
        run = p.add_run(seg)
        set_run_font(run, name=font, size=size, bold=(bold or is_bold), color=color)
    return p


def split_bold(text):
    """把 **x** 切成 [(片段, 是否加粗)]。"""
    out, pos = [], 0
    for m in re.finditer(r'\*\*(.+?)\*\*', text):
        if m.start() > pos:
            out.append((text[pos:m.start()], False))
        out.append((m.group(1), True))
        pos = m.end()
    if pos < len(text):
        out.append((text[pos:], False))
    return out or [(text, False)]


def shade(cell, hex_color='F2F2F2'):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:color'), 'auto')
    shd.set(qn('w:fill'), hex_color)
    tc_pr.append(shd)


def add_table(doc, rows):
    header, body = rows[0], rows[1:]
    table = doc.add_table(rows=len(rows), cols=len(header))
    table.style = 'Table Grid'
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = True
    for j, text in enumerate(header):
        cell = table.cell(0, j)
        cell.text = ''
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for seg, is_bold in split_bold(text):
            set_run_font(p.add_run(seg), name=HEAD_FONT, size=9.5, bold=True)
        shade(cell)
    for i, row in enumerate(body, start=1):
        for j, text in enumerate(row):
            if j >= len(header):
                continue
            cell = table.cell(i, j)
            cell.text = ''
            p = cell.paragraphs[0]
            p.paragraph_format.line_spacing = 1.25
            p.paragraph_format.space_after = Pt(2)
            for seg, is_bold in split_bold(text):
                set_run_font(p.add_run(seg), size=9.5, bold=is_bold)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return table


def add_page_footer(doc, label):
    footer = doc.sections[0].footer
    p = footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_run_font(p.add_run(label + '　第 '), size=9, color=(0x66, 0x66, 0x66))
    run = p.add_run()
    set_run_font(run, size=9, color=(0x66, 0x66, 0x66))
    fld = OxmlElement('w:fldSimple')
    fld.set(qn('w:instr'), 'PAGE')
    run._element.addnext(fld)
    set_run_font(p.add_run(' 页'), size=9, color=(0x66, 0x66, 0x66))


def convert(src: Path, dst: Path):
    lines = src.read_text(encoding='utf-8').splitlines()
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    sec.top_margin = sec.bottom_margin = Cm(1.9)
    sec.left_margin = sec.right_margin = Cm(2.2)

    normal = doc.styles['Normal']
    normal.font.name = BODY_FONT
    normal.font.size = Pt(12)
    normal.element.rPr.rFonts.set(qn('w:eastAsia'), BODY_FONT)

    i, title = 0, '小智使用说明'
    while i < len(lines):
        raw = lines[i].rstrip()
        line = raw.strip()
        if not line:
            i += 1
            continue

        if line.startswith('# '):
            title = line[2:].strip()
            add_paragraph(doc, title, size=18, bold=True, font=HEAD_FONT,
                          align=WD_ALIGN_PARAGRAPH.CENTER, space_before=0,
                          space_after=10, line=1.2)
            i += 1
            continue

        if line.startswith('## '):
            add_paragraph(doc, line[3:].strip(), size=14, bold=True, font=HEAD_FONT,
                          space_before=9, space_after=5, line=1.22)
            i += 1
            continue

        if line.startswith('### '):
            add_paragraph(doc, line[4:].strip(), size=13, bold=True, font=HEAD_FONT,
                          space_before=8, space_after=4)
            i += 1
            continue

        if line.startswith('|'):
            rows = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                cells = [c.strip() for c in lines[i].strip().strip('|').split('|')]
                if not all(re.fullmatch(r':?-{2,}:?', c) for c in cells):
                    rows.append(cells)
                i += 1
            if rows:
                add_table(doc, rows)
            continue

        if line.startswith('> '):
            add_paragraph(doc, line[2:].strip(), size=10, color=(0x55, 0x55, 0x55),
                          space_before=2, space_after=10, line=1.35)
            i += 1
            continue

        if line.startswith('- '):
            add_paragraph(doc, '· ' + line[2:].strip(), size=10.5, first_indent=Cm(0.74),
                          space_before=0, space_after=2, line=1.32)
            i += 1
            continue

        # 正文段落：小节标签（1.1 作用 之类已加粗）不缩进，其余缩进两字
        text = line
        indented = not (text.startswith('**') or re.match(r'^（\d+）', text)
                        or re.match(r'^[①②③④⑤⑥⑦⑧⑨⑩]', text)
                        or re.match(r'^\d+\.\s', text))
        add_paragraph(doc, text, size=10.5, first_indent=(Cm(0.74) if indented else None),
                      space_before=0, space_after=2, line=1.32)
        i += 1

    add_page_footer(doc, title)
    dst.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(dst))
    print('OK %s (%d bytes)' % (dst, dst.stat().st_size))


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print('用法：py -X utf8 md2docx_guide.py <源.md> <输出.docx>')
        raise SystemExit(2)
    convert(Path(sys.argv[1]), Path(sys.argv[2]))
