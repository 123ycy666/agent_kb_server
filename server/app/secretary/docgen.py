"""生成成果文件：docx / xlsx。

原则：**格式是数据，不是代码**。
- docx 用一套"公文默认样式"（三号仿宋_GB2312、28 磅行距、首行缩进 2 字），
  要换样式就改 `DOC_FONT` 这几个常量，别在业务代码里散落字体设置。
- xlsx 只做"能看、能导入"的最小样式：表头加粗底色、冻结首行、列宽自适应、加边框。
  不做花哨的图表（那个等有真模板再说，见设计稿 §8）。

⚠️ 生成的文档一律带一句"本文件由系统辅助生成，请人工复核"——
内网公文要担责，这一句是必须的，不是客套。
"""

from __future__ import annotations

from pathlib import Path

DOC_FONT = "仿宋_GB2312"
DOC_FONT_FALLBACK = "仿宋"
TITLE_FONT = "方正小标宋简体"
BODY_PT = 16          # 三号
TITLE_PT = 22         # 二号
LINE_SPACING_PT = 28  # 公文常用行距（固定值）

AI_NOTE = "本文件由系统辅助生成，数据与结论请人工复核后使用。"


# ── docx ──────────────────────────────────────────────────────

def write_docx(path: Path, title: str, sections: list[tuple[str, str]],
               tables: list[tuple[str, list[str], list[list[str]]]] | None = None) -> Path:
    """写一份 docx。

    sections：[(小节标题, 正文)]，正文里换行即分段。
    tables  ：[(表标题, 表头, 行)]，表标题为空就不写标题。

    ★ 版式默认值**运行时**从站点包读（`doc_defaults.yml`），不再在 import 时改写模块常量：
      import 期改写等于"部署文件改代码"，改了要重启，而且测不出差异。
    """
    import docx
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Pt

    d = _dd("docx")
    body_font = d.get("body_font_cn") or DOC_FONT
    body_fallback = d.get("body_font_fallback") or DOC_FONT_FALLBACK
    title_font = d.get("title_font_cn") or TITLE_FONT
    body_pt = float(d.get("body_pt") or BODY_PT)
    title_pt = float(d.get("title_pt") or TITLE_PT)
    line_pt = float(d.get("line_spacing_pt") or LINE_SPACING_PT)

    doc = docx.Document()

    # 默认样式：中文字体必须单独设 eastAsia，只设 font.name 中文不生效（老坑）
    style = doc.styles["Normal"]
    style.font.name = body_fallback
    style.font.size = Pt(body_pt)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), body_font)

    t = doc.add_paragraph()
    t.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = t.add_run(title)
    run.bold = True
    run.font.size = Pt(title_pt)
    run.font.name = title_font
    run._element.rPr.rFonts.set(qn("w:eastAsia"), title_font)

    for head, body in sections:
        if head:
            h = doc.add_paragraph()
            hr = h.add_run(head)
            hr.bold = True
            hr.font.size = Pt(body_pt)
        for para in [p for p in (body or "").split("\n") if p.strip()]:
            p = doc.add_paragraph(para.strip())
            p.paragraph_format.first_line_indent = Pt(body_pt * 2)   # 首行缩进 2 字
            p.paragraph_format.line_spacing = Pt(line_pt)

    for cap, header, rows in (tables or []):
        if cap:
            c = doc.add_paragraph()
            cr = c.add_run(cap)
            cr.bold = True
        table = doc.add_table(rows=1, cols=len(header))
        table.style = "Table Grid"
        for i, col in enumerate(header):
            cell = table.rows[0].cells[i]
            cell.text = str(col)
            for run_ in cell.paragraphs[0].runs:
                run_.bold = True
        for row in rows:
            cells = table.add_row().cells
            for i, val in enumerate(row[: len(header)]):
                cells[i].text = "" if val is None else str(val)

    note = doc.add_paragraph(AI_NOTE)
    note.runs[0].font.size = Pt(10)

    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    return path


# ── xlsx ──────────────────────────────────────────────────────

XLSX_BORDER_COLOR = 'BFBFBF'
XLSX_HEAD_FILL = 'DDEBF7'


def write_xlsx(path: Path, sheets: list[tuple[str, list[str], list[list]]]) -> Path:
    """写 xlsx。sheets：[(表名, 表头, 行)]。"""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    x = _dd("xlsx")
    border_color = str(x.get("border_color") or XLSX_BORDER_COLOR)
    head_color = str(x.get("header_fill") or XLSX_HEAD_FILL)

    wb = Workbook()
    wb.remove(wb.active)

    thin = Side(style="thin", color=border_color)
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    head_fill = PatternFill("solid", fgColor=head_color)

    for name, header, rows in sheets:
        ws = wb.create_sheet(title=(name or "数据")[:28])
        ws.append(list(header))
        for cell in ws[1]:
            cell.font = Font(bold=True)
            cell.fill = head_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = border

        for row in rows:
            ws.append([("" if v is None else v) for v in row])

        for r in ws.iter_rows(min_row=2, max_row=ws.max_row, max_col=len(header)):
            for cell in r:
                cell.border = border
                cell.alignment = Alignment(vertical="top", wrap_text=True)

        ws.freeze_panes = "A2"
        for i, col in enumerate(header, start=1):
            width = max(len(str(col)) * 2 + 4,
                        *(len(str(r[i - 1])) * 1.6 + 2 for r in rows if i - 1 < len(r))) if rows else len(str(col)) * 2 + 4
            ws.column_dimensions[get_column_letter(i)].width = min(max(10, width), 60)

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(path))
    return path


# ── 站点包覆盖（运行时读，不在 import 期改常量）────────────────────
def doc_defaults() -> dict:
    """文档版式默认值（原站点包 doc_defaults.yml，2026-10-30 内联进 prompts_data）。"""
    try:
        from app.secretary import prompts_data
        return dict(prompts_data.DOC_DEFAULTS or {})
    except Exception:                          # noqa: BLE001
        return {}


def _dd(part: str) -> dict:
    """取某一类（docx/xlsx）的默认值：代码常量 ← 站点包覆盖。

    ⚠️ 必须在**调用时**读：站点包是按目录 mtime 缓存的，改完 yml 下一次调用就生效；
       原来在 import 期把值写进模块全局 ⇒ 改完必须重启，而且没法测。
    """
    try:
        return dict((doc_defaults() or {}).get(part) or {})
    except Exception:                          # noqa: BLE001
        return {}
