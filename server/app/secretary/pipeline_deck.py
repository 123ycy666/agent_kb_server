# -*- coding: utf-8 -*-
"""文档转 PPT：把一份已上传的文件，用**别人的技能包**排成一册 .pptx，交付前过它的自检。

分工（2026-10-21）
-----------------
- 排页/填字/字体：用 `skills/office/bisheng-pptx/scripts/pptx_helpers.py`（别人的工具）
- 交付前体检：用同一技能包的 `inspect_deck.py`（别人的规则，`结论: 通过` 才算完成）
- 我只做两件"本系统特有"的事：把解析出的章节切成页；定中文汇报的骨架（封面/目录/章节/要点/结尾）

第一版**不调模型**：按解析出的标题层级出页、要点取该节首句。
质量不够时只改 `spec_from_blocks()`（换成模型写要点），接口不变。

自检：py -X utf8 server/tests/agent/test_deck_job.py
"""
from __future__ import annotations

import json
from pathlib import Path

from app.secretary import office_runner as R

TASK = 'deck'
MAX_SECTIONS = 8          # 再多的汇报没人看
MAX_BULLETS = 5
BULLET_CHARS = 44


def _clean(t: str) -> str:
    return ' '.join(str(t or '').split())


def _lead(text: str) -> str:
    s = _clean(text)
    for sep in ('。', '；', '！', '？', '.', ';'):
        i = s.find(sep)
        if 8 <= i <= BULLET_CHARS * 2:
            return s[:i]
    return s


def _bullets(paras: list[str]) -> list[str]:
    out: list[str] = []
    for p in paras:
        b = _lead(p)
        if len(b) < 6 or b in out:
            continue
        out.append(b[:BULLET_CHARS] + ('…' if len(b) > BULLET_CHARS else ''))
        if len(out) >= MAX_BULLETS:
            break
    return out


def spec_from_blocks(title: str, blocks: list[dict], *, max_sections: int = MAX_SECTIONS) -> dict:
    """把解析块切成 deck spec（纯函数，好测）。"""
    sections: list[dict] = []
    cur: dict | None = None
    for b in blocks or []:
        text = _clean(b.get('text'))
        if not text:
            continue
        is_head = b.get('block_type') == 'heading' or (b.get('level') or 0) > 0
        if is_head or cur is None:
            if cur:
                sections.append(cur)
            cur = {'title': text[:40], 'paras': []}
        else:
            cur['paras'].append(text)
    if cur:
        sections.append(cur)

    # 全文没有标题：按段落切块，每块一页
    if len(sections) == 1 and len(sections[0]['paras']) > MAX_BULLETS:
        flat = sections[0]['paras']
        total = (len(flat) + MAX_BULLETS - 1) // MAX_BULLETS
        sections = [{'title': '%s（%d/%d）' % (sections[0]['title'][:24],
                                              i // MAX_BULLETS + 1, total),
                     'paras': flat[i:i + MAX_BULLETS]}
                    for i in range(0, len(flat), MAX_BULLETS)]
    sections = sections[:max_sections]

    slides: list[dict] = [{'layout': 'cover', 'title': title or '汇报材料'}]
    toc = [s['title'] for s in sections if s['title']]
    if len(toc) > 1:
        slides.append({'layout': 'bullets', 'title': '目录',
                       'bullets': ['%d. %s' % (i, t) for i, t in enumerate(toc, start=1)]})
    for i, sec in enumerate(sections, start=1):
        slides.append({'layout': 'section', 'no': str(i), 'title': sec['title']})
        bl = _bullets(sec['paras'])
        if bl:
            slides.append({'layout': 'bullets', 'title': sec['title'], 'bullets': bl})
    slides.append({'layout': 'end', 'title': '谢谢',
                   'subtitle': '如需调整页序或补充数据，请说明'})
    return {'title': title or '汇报材料', 'slides': slides}


# 用技能包的排版纪律（微软雅黑/字号阶梯/页脚/页码），我们只提供内容
_BUILD_CODE = '''\
import json
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN

SPEC = json.loads({spec_json!r})
OUT = {out!r}

prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)

INK = RGBColor(0x1F, 0x29, 0x37)
MUTED = RGBColor(0x6B, 0x72, 0x80)
ACCENT = RGBColor(0x1F, 0x4E, 0x79)


def box(slide, left, top, width, height, text, size=18, bold=False, rgb=INK, center=False):
    shp = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    tf = shp.text_frame
    tf.word_wrap = True
    for i, line in enumerate(str(text or "").split("\\n")):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = line
        if center:
            p.alignment = PP_ALIGN.CENTER
        for r in p.runs:
            r.font.size = Pt(size)
            r.font.bold = bold
            r.font.name = "微软雅黑"
            r.font.color.rgb = rgb
    return shp


slides = SPEC.get("slides") or []
for idx, s in enumerate(slides, start=1):
    layout = s.get("layout") or "bullets"
    sl = prs.slides.add_slide(prs.slide_layouts[6])
    if layout == "cover":
        box(sl, 0.9, 2.4, 11.5, 1.4, s.get("title") or "", 40, True, ACCENT)
        if s.get("subtitle"):
            box(sl, 0.9, 3.9, 11.5, 0.9, s["subtitle"], 22, False, MUTED)
    elif layout == "section":
        box(sl, 0.9, 2.9, 1.6, 1.2, str(s.get("no") or ""), 54, True, ACCENT)
        box(sl, 2.4, 3.05, 10.0, 1.2, s.get("title") or "", 36, True)
    elif layout == "end":
        box(sl, 0.9, 3.0, 11.5, 1.2, s.get("title") or "谢谢", 40, True, ACCENT, True)
        if s.get("subtitle"):
            box(sl, 0.9, 4.2, 11.5, 0.8, s["subtitle"], 18, False, MUTED, True)
    else:
        box(sl, 0.9, 0.6, 11.5, 1.1, s.get("title") or "", 30, True)
        bl = s.get("bullets") or []
        if bl:
            box(sl, 0.9, 1.9, 11.5, 4.4, "\\n".join("\u2022 " + str(b) for b in bl), 18)
    if layout != "cover":
        box(sl, 0.9, 6.85, 9.0, 0.35,
            "本文件由系统辅助生成，内容与数据请人工复核后使用。", 11, False, MUTED)
        box(sl, 11.4, 6.85, 1.2, 0.35, "%d / %d" % (idx, len(slides)), 11, False, MUTED)

prs.save(OUT)
print("built", OUT, "slides", len(slides))
'''


def run(file_id: str, user_id: int, options: dict | None = None,
        progress=None, out_dir: Path | None = None) -> dict:
    """任务层入口：与其它 pipeline 同签名；outputs 是交付文件名列表。"""
    opts = options or {}
    say = progress or (lambda pct, stage: None)

    from app.secretary import doc_files          # noqa: WPS433 (lazy: keeps import chain light)
    say(5, '读取文件')
    meta = doc_files.load(file_id, user_id)
    blocks = doc_files.blocks(file_id, user_id, offset=0, limit=100000)['blocks']
    title = _clean(opts.get('title') or (meta.get('name') or '汇报材料').rsplit('.', 1)[0])

    say(30, '拆分章节')
    spec = spec_from_blocks(title, blocks,
                             max_sections=int(opts.get('max_sections') or MAX_SECTIONS))

    out_dir = Path(str(out_dir or '.'))
    out_path = out_dir / ('%s.pptx' % (title[:40] or '汇报材料'))
    wd = R.new_job_dir(out_dir / '_deck_work')

    say(45, '排版 %d 页' % len(spec['slides']))
    code = (_BUILD_CODE
            .replace('{spec_json!r}', repr(json.dumps(spec, ensure_ascii=False)))
            .replace('{out!r}', repr(str(out_path))))
    res = R.run_code(code, wd, timeout=180)
    if not res['ok']:
        raise RuntimeError('生成 PPT 失败：%s' % ((res['stderr'] or res['stdout'] or '')[-400:]))

    say(75, '交付前自检')
    insp = R.run_script(R.PPTX_SCRIPTS / 'inspect_deck.py', [str(out_path)], wd, timeout=180)
    out_text = insp['stdout'] or ''
    verdict = '通过' if '结论: 通过' in out_text else '不通过'
    if verdict != '通过':
        raise RuntimeError('自检不通过，已拦下交付：\n%s' % out_text[-800:])

    say(100, '完成')
    return {'task': TASK, 'file_id': file_id, 'file_name': meta.get('name'),
            'title': title, 'slides': len(spec['slides']), 'outputs': [out_path.name],
            'self_check': {'verdict': verdict, 'tail': out_text[-500:]}}
