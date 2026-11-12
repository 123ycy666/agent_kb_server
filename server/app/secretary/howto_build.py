# -*- coding: utf-8 -*-
"""按「做法」参数生成 docx / xlsx 的构建模板（2026-11-11 办公线）。

和 tools_file 里原来那份的区别
------------------------------
原来 make_file 把 `fmt` 传成空对象 `{}`，所以对话链路出的文件只有技能包的通用默认，
用户偏好与单位规范（prefs / DOC_DEFAULTS）都没生效。
这里把做法里的 fmt 真正落到：
    page（A4 + 四个页边距）、title / h1 / h2 / h3 的字体字号段前段后、正文行距与首行缩进、
    落款右对齐、页码、表格、Excel 的表头/数字格式/冻结/列宽/合计行。
模板字符串用 @SPEC@ / @FMT@ / @OUT@ 占位，由调用方 replace 后交给 office_runner 跑。
"""
from __future__ import annotations

DOCX_CODE = r'''# -*- coding: utf-8 -*-
import json
import docx
from docx.shared import Pt, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn

SPEC = json.loads(@SPEC@)
FMT = json.loads(@FMT@)
OUT = @OUT@

F = FMT or {}
PAGE = F.get('page') or {}
T = F.get('title') or {}
HEADS = {1: (F.get('h1') or {}), 2: (F.get('h2') or {}), 3: (F.get('h3') or {})}
BODY = F.get('body') or {}
SIGN = F.get('sign_off') or {}
PN = F.get('page_number') or {}

ALIGN = {'center': WD_ALIGN_PARAGRAPH.CENTER, 'right': WD_ALIGN_PARAGRAPH.RIGHT,
         'left': WD_ALIGN_PARAGRAPH.LEFT, 'justify': WD_ALIGN_PARAGRAPH.JUSTIFY}


def set_font(run, name, size_pt, bold=None):
    if name:
        run.font.name = name
        rpr = run._element.get_or_add_rPr()
        rf = rpr.get_or_add_rFonts()
        rf.set(qn('w:eastAsia'), name)
    if size_pt:
        run.font.size = Pt(float(size_pt))
    if bold is not None:
        run.bold = bool(bold)


def para_fmt(p, spec, default_indent=0):
    pf = p.paragraph_format
    pf.space_before = Pt(float(spec.get('before') or 0))
    pf.space_after = Pt(float(spec.get('after') or 0))
    line = spec.get('line')
    if line:
        pf.line_spacing = Pt(float(str(line).replace('pt', '')))
        pf.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    ind = spec.get('indent_chars', default_indent)
    size = float(spec.get('pt') or 16)
    if ind:
        pf.first_line_indent = Pt(size * float(ind))
    al = spec.get('align')
    if al in ALIGN:
        p.alignment = ALIGN[al]


def add_para(text, spec, default_indent=0):
    p = doc.add_paragraph()
    r = p.add_run(text)
    set_font(r, spec.get('font'), spec.get('pt'), spec.get('bold'))
    para_fmt(p, spec, default_indent)
    return p


doc = docx.Document()
sec = doc.sections[0]
if PAGE.get('size') == 'A4' or PAGE:
    sec.page_width = Cm(float(PAGE.get('width') or 21.0))
    sec.page_height = Cm(float(PAGE.get('height') or 29.7))
    sec.top_margin = Cm(float(PAGE.get('top') or 2.54))
    sec.bottom_margin = Cm(float(PAGE.get('bottom') or 2.54))
    sec.left_margin = Cm(float(PAGE.get('left') or 3.17))
    sec.right_margin = Cm(float(PAGE.get('right') or 3.17))

# 标题
if SPEC.get('title'):
    add_para(SPEC['title'], T)

# 正文：sections = [[小节标题, 正文]]，小节标题按「一、」「（一）」「1.」判层级
for head, body in (SPEC.get('sections') or []):
    head = (head or '').strip()
    if head:
        lvl = 1
        if head.startswith('（') or head.startswith('('):
            lvl = 2
        elif len(head) > 1 and head[0].isdigit() and head[1] in '.．、':
            lvl = 3
        add_para(head, HEADS[lvl])
    for line in str(body or '').split('\n'):
        line = line.strip()
        if line:
            add_para(line, BODY, default_indent=BODY.get('indent_chars', 2))

# 表格（可选）：[{"header":[...], "rows":[[...]]}]
for tb in (SPEC.get('tables') or []):
    header = tb.get('header') or []
    rows = tb.get('rows') or []
    cols = len(header) or (len(rows[0]) if rows else 0)
    if not cols:
        continue
    t = doc.add_table(rows=1, cols=cols)
    t.style = 'Table Grid'
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, h in enumerate(header[:cols]):
        cell = t.rows[0].cells[i]
        cell.text = ''
        r = cell.paragraphs[0].add_run(str(h))
        set_font(r, BODY.get('font'), BODY.get('table_pt') or 14, True)
        cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
    for row in rows:
        cells = t.add_row().cells
        for i, v in enumerate(row[:cols]):
            cells[i].text = ''
            r = cells[i].paragraphs[0].add_run('' if v is None else str(v))
            set_font(r, BODY.get('font'), BODY.get('table_pt') or 14, False)

# 落款
sign = SPEC.get('sign_off') or []
if sign:
    for _ in range(int(SIGN.get('blank_lines') or 2)):
        doc.add_paragraph()
    for line in sign:
        p = doc.add_paragraph()
        r = p.add_run(str(line))
        set_font(r, SIGN.get('font') or BODY.get('font'), SIGN.get('pt') or BODY.get('pt'), False)
        p.alignment = ALIGN['right']

# 页码（页脚居中）
if PN and PN.get('on', True):
    try:
        import docx_helpers as _H
        _H.add_page_number_footer(sec)
        _H.enable_update_fields(doc)
    except Exception:
        from docx.oxml import OxmlElement
        fp = sec.footer.paragraphs[0]
        fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = fp.add_run()
        for kind, txt in (('begin', None), (None, 'PAGE'), ('end', None)):
            if kind:
                fld = OxmlElement('w:fldChar')
                fld.set(qn('w:fldCharType'), kind)
                run._r.append(fld)
            else:
                instr = OxmlElement('w:instrText')
                instr.set(qn('xml:space'), 'preserve')
                instr.text = txt
                run._r.append(instr)
        set_font(run, PN.get('font') or '宋体', PN.get('pt') or 14, False)

doc.save(OUT)
print('built', OUT, 'sections', len(SPEC.get('sections') or []), 'tables', len(SPEC.get('tables') or []))
'''


XLSX_CODE = r'''# -*- coding: utf-8 -*-
import json
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
from openpyxl.utils import get_column_letter

SPEC = json.loads(@SPEC@)
FMT = json.loads(@FMT@)
OUT = @OUT@

F = FMT or {}
HDR = F.get('header') or {}
BOD = F.get('body') or {}
THIN = Side(style='thin', color='999999')
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

wb = Workbook()
first = True
for sh in (SPEC.get('sheets') or []):
    ws = wb.active if first else wb.create_sheet()
    ws.title = (sh.get('name') or 'Sheet1')[:31]
    first = False
    header = sh.get('header') or []
    rows = sh.get('rows') or []
    if sh.get('title'):
        ws.append([sh['title']])
        ws.merge_cells(start_row=1, start_column=1, end_row=1,
                       end_column=max(1, len(header) or len(rows[0]) if rows else 1))
        c = ws.cell(row=1, column=1)
        c.font = Font(name=HDR.get('font') or '宋体', size=(HDR.get('pt') or 11) + 3, bold=True)
        c.alignment = Alignment(horizontal='center', vertical='center')
        start = 2
    else:
        start = 1
    if header:
        ws.append(header)
        for i in range(1, len(header) + 1):
            c = ws.cell(row=start, column=i)
            c.font = Font(name=HDR.get('font') or '宋体', size=HDR.get('pt') or 11,
                          bold=HDR.get('bold', True))
            c.fill = PatternFill('solid', fgColor=HDR.get('fill') or 'DDEBF7')
            c.alignment = Alignment(horizontal=HDR.get('align') or 'center',
                                    vertical='center', wrap_text=True)
            c.border = BOX
        body_start = start + 1
    else:
        body_start = start
    for row in rows:
        ws.append(row)
    formats = sh.get('formats') or []
    nrow = body_start + len(rows) - 1
    for r in range(body_start, nrow + 1):
        for i in range(1, (len(header) or (len(rows[0]) if rows else 0)) + 1):
            c = ws.cell(row=r, column=i)
            c.font = Font(name=BOD.get('font') or '宋体', size=BOD.get('pt') or 10)
            c.border = BOX
            if i - 1 < len(formats) and formats[i - 1]:
                c.number_format = formats[i - 1]
    # 合计行：末行加粗 + 上双线
    if sh.get('total_row') and rows:
        for i in range(1, (len(header) or len(rows[0])) + 1):
            c = ws.cell(row=nrow, column=i)
            c.font = Font(name=BOD.get('font') or '宋体', size=BOD.get('pt') or 10, bold=True)
            c.border = Border(left=THIN, right=THIN, top=Side(style='double', color='333333'),
                              bottom=THIN)
    widths = sh.get('widths') or []
    total = (len(header) or (len(rows[0]) if rows else 0))
    for i in range(1, total + 1):
        w = widths[i - 1] if i - 1 < len(widths) else None
        if not w:
            longest = max([len(str((header[i - 1] if i - 1 < len(header) else '') or ''))]
                          + [len(str(row[i - 1])) for row in rows if i - 1 < len(row) and row[i - 1] is not None] or [6])
            w = min(40, max(8, longest * 2 + 2))
        ws.column_dimensions[get_column_letter(i)].width = float(w)
    freeze = sh.get('freeze') or F.get('freeze')
    if freeze:
        ws.freeze_panes = freeze

wb.save(OUT)
print('built', OUT)
'''
