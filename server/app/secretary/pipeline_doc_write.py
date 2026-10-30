# -*- coding: utf-8 -*-
"""写公文 Word：按**毕昇的公文版式助手**重排成 .docx（任务通道 `doc_write`）。

谁干什么（2026-10-21，Fred 的要求：有别人的就用别人的，只做本地化）
----------------------------------------------------------------
- 版式：`skills/office/bisheng-docx/scripts/docx_helpers.py` 的
  `apply_gongwen_defaults`（三号仿宋/行距/首行缩进）、`setup_page`（A4 版心）、
  `add_toc`（目录域）、`add_page_number_footer`（第 X 页 / 共 Y 页）、`enable_update_fields`
- 交付前体检：同包的 `inspect_docx.py`，`结论: 通过` 才算完成
- 我们只做本地化三件事：① 把解析块切成「小节 + 正文」② 用站点包 `doc_defaults`
  覆盖字号字体（换单位时改包） ③ 末尾固定"AI 辅助生成，请人工复核"

不改写文字：重排是可验收的（格式逐项可核）；代写版本只需替换正文来源。
"""
from __future__ import annotations

from pathlib import Path

from app.secretary import office_runner as R

TASK = 'doc_write'
MAX_PARAS_PER_SECTION = 40

_BUILD_CODE = '''\
import json
import docx
from docx.shared import Pt
import docx_helpers as H

SPEC = json.loads({spec_json!r})
OUT = {out!r}
FMT = json.loads({fmt_json!r})

doc = docx.Document()

# 1) 别人的公文版式（三号仿宋/固定行距/首行缩进）；本站点的字体字号从 doc_defaults 传进来
H.apply_gongwen_defaults(doc)
try:
    if FMT.get("body_font_cn") or FMT.get("body_pt"):
        H.apply_chinese_defaults(doc, body_font=FMT.get("body_font_cn"),
                                 body_pt=FMT.get("body_pt"))
except Exception:
    pass
H.setup_page(doc)

# 2) 标题
t = doc.add_paragraph()
t.alignment = 1
run = t.add_run(SPEC["title"])
run.bold = True
run.font.size = Pt(FMT.get("title_pt") or 22)

# 3) 正文：小节标题 + 段落
for head, body in SPEC["sections"]:
    if head:
        h = doc.add_paragraph()
        hr = h.add_run(head)
        hr.bold = True
        hr.font.size = Pt(FMT.get("body_pt") or 16)
    for para in [p for p in (body or "").split("\\n") if p.strip()]:
        doc.add_paragraph(para.strip())

# 4) 出处声明
note = doc.add_paragraph("本文件由系统辅助生成，数据与结论请人工复核后使用。")
for r in note.runs:
    r.font.size = Pt(10)

# 5) 页码 + 目录域（别人的实现）
H.add_page_number_footer(doc.sections[0])
H.enable_update_fields(doc)

doc.save(OUT)
print("built", OUT, "sections", len(SPEC["sections"]))
'''


def sections_from_blocks(title: str, blocks: list[dict]) -> list[tuple[str, str]]:
    """解析块 → [(小节标题, 正文)]；没有标题就整体作为一节。"""
    out: list[tuple[str, str]] = []
    head = ''
    buf: list[str] = []

    def flush() -> None:
        if head or buf:
            out.append((head, '\n'.join(buf[:MAX_PARAS_PER_SECTION])))

    for b in blocks or []:
        text = ' '.join(str(b.get('text') or '').split())
        if not text:
            continue
        if b.get('block_type') == 'heading' or (b.get('level') or 0) > 0:
            flush()
            head, buf = text[:60], []
        else:
            buf.append(text)
    flush()
    if not out:
        out = [('', '（文件里没有解析到正文）')]
    return [(h, t) for h, t in out if h or t]


def run(file_id: str, user_id: int, options: dict | None = None,
        progress=None, out_dir: Path | None = None) -> dict:
    import json

    opts = options or {}
    say = progress or (lambda pct, stage: None)

    from app.secretary import doc_files                 # lazy: keeps import chain light
    say(10, '读取文件')
    meta = doc_files.load(file_id, user_id)
    blocks = doc_files.blocks(file_id, user_id, offset=0, limit=100000)['blocks']
    title = ' '.join(str(opts.get('title') or (meta.get('name') or '公文').rsplit('.', 1)[0]).split())

    say(40, '按公文版式重排')
    sections = sections_from_blocks(title, blocks)

    from app.secretary import prompts_data
    from app.secretary import prefs as _prefs
    dx = _prefs.merge_fmt(prompts_data.DOC_DEFAULTS,
                          user_id, opts.get('fmt') or {})
    fmt = {'body_font_cn': dx.get('body_font_cn') or '仿宋_GB2312',
           'body_pt': dx.get('body_pt') or 16,
           'title_pt': dx.get('title_pt') or 22,
           'line_spacing_pt': dx.get('line_spacing_pt') or 28}

    out_dir = Path(str(out_dir or '.'))
    out_path = out_dir / ('%s.docx' % (title[:40] or '公文'))
    wd = R.new_job_dir(out_dir / '_doc_work')

    say(60, '生成 docx')
    code = (_BUILD_CODE
            .replace('{spec_json!r}', repr(json.dumps({'title': title, 'sections': sections},
                                                    ensure_ascii=False)))
            .replace('{out!r}', repr(str(out_path)))
            .replace('{fmt_json!r}', repr(json.dumps(fmt, ensure_ascii=False))))
    res = R.run_code(code, wd, timeout=180)
    if not res['ok']:
        raise RuntimeError('生成公文失败：%s' % ((res['stderr'] or res['stdout'] or '')[-400:]))

    say(80, '交付前自检')
    insp = R.run_script(R.DOCX_SCRIPTS / 'inspect_docx.py', [str(out_path)], wd, timeout=180)
    text = insp['stdout'] or ''
    verdict = '通过' if '结论: 通过' in text else '不通过'
    if verdict != '通过':
        raise RuntimeError('自检不通过，已拦下交付：\n%s' % text[-800:])

    say(100, '完成')
    return {'task': TASK, 'file_id': file_id, 'file_name': meta.get('name'),
            'title': title, 'sections': len(sections), 'outputs': [out_path.name],
            'fmt': fmt, 'self_check': {'verdict': verdict, 'tail': text[-400:]}}
