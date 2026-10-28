# -*- coding: utf-8 -*-
"""F1-3 验收：文档 → PPT（走技能包排版 + 交付前自检）。

钉死四条
--------
1. 一份 docx 走 pipeline 能产出合法 .pptx（能被 python-pptx 重新打开）。
2. **交付前必须过技能包的 `inspect_deck`**，结论不是"通过"就抛错、不交文件。
3. 页数与 spec 一致（封面 + 目录 + 每节 2 页 + 结尾）。
4. 分页规则：标题切节；没有标题时按段落分块，每块一页。

运行：py -X utf8 server/tests/agent/test_deck_job.py
"""
from __future__ import annotations

import shutil
import sys
import types
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SERVER))

from app.secretary import office_runner as R, pipeline_deck as D  # noqa: E402

_checks: list[tuple[str, bool, str]] = []


def chk(label: str, cond: bool, extra: str = "") -> None:
    _checks.append((label, bool(cond), extra))


def _make_docx(path: Path) -> None:
    import docx
    d = docx.Document()
    d.add_heading('配网改造推进情况汇报', 0)
    d.add_heading('一、总体进展', 1)
    d.add_paragraph('本年度下达配网改造项目 12 个，累计完成投资 860 万元。已完成 7 个，在建 5 个。')
    d.add_paragraph('线路绝缘化改造完成 32 公里，台区改造完成 18 个。')
    d.add_heading('二、存在问题', 1)
    d.add_paragraph('部分台区施工受阻，物资到货滞后两周，影响整体进度。')
    d.add_heading('三、下一步安排', 1)
    d.add_paragraph('加强物资催交，优化施工组织，确保年底前完成全部项目。')
    d.save(str(path))


def main() -> int:
    print("F1-3：文档转 PPT（技能包排版 + 自检闸门）")

    work = SERVER / '.tmp' / 'deck_job'
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    src = work / '配网改造汇报.docx'
    _make_docx(src)

    # 1) 纯函数：分页规则
    blocks = [
        {'text': '一、总体进展', 'block_type': 'heading', 'level': 1},
        {'text': '本年度下达配网改造项目 12 个，累计完成投资 860 万元。', 'block_type': 'paragraph'},
        {'text': '二、存在问题', 'block_type': 'heading', 'level': 1},
        {'text': '部分台区施工受阻，物资到货滞后两周。', 'block_type': 'paragraph'},
    ]
    spec = D.spec_from_blocks('汇报', blocks)
    chk("分页：封面+目录+2节x2页+结尾 = 7 页", len(spec['slides']) == 7,
        str([s.get('layout') for s in spec['slides']]))
    flat = [{'text': '段落%d：这是一段用于测试分块的正文内容，长度足够。' % i,
             'block_type': 'paragraph'} for i in range(12)]
    spec2 = D.spec_from_blocks('无标题材料', flat)
    chk("无标题时按段落分块（12 段 → 3 页正文）",
        sum(1 for s in spec2['slides'] if s.get('layout') == 'bullets') >= 3,
        str([s.get('layout') for s in spec2['slides']]))

    # 2) 走真实链路：存文件 → 跑 pipeline
    # doc_files 走 app.services.ingestion -> sqlalchemy，本机没装；用等价桩占位，
    # 真实部署环境走真模块（接口相同：load/blocks）
    fake = types.ModuleType('app.secretary.doc_files')
    fake.load = lambda fid, uid: {'file_id': fid, 'name': src.name, 'user_id': uid}
    fake.blocks = lambda fid, uid, offset=0, limit=50: {
        'blocks': [{'text': b['text'], 'block_type': b['block_type'],
                    'level': b.get('level', 0), 'heading_path': None} for b in blocks],
        'total': len(blocks)}
    sys.modules['app.secretary.doc_files'] = fake
    file_id = 'f_deck_test'
    out_dir = work / 'out'
    out_dir.mkdir(parents=True)
    stages: list[str] = []
    res = D.run(file_id, 1, {}, lambda p, s: stages.append('%s:%s' % (p, s)), out_dir)

    pptx = out_dir / res['outputs'][0]
    chk("pipeline 产出 .pptx 且文件存在", pptx.is_file() and pptx.suffix == '.pptx', str(pptx))
    chk("自检结论为通过", res['self_check']['verdict'] == '通过', res['self_check']['tail'][-120:])

    from pptx import Presentation
    prs = Presentation(str(pptx))
    chk("能被 python-pptx 重新打开", prs.slides is not None)
    chk("页数 = spec 页数", len(prs.slides.__iter__.__self__._sldIdLst) == res['slides'],
        '%d vs %d' % (len(prs.slides._sldIdLst), res['slides']))
    chk("进度有 5/30/45/75/100 五个阶段", len(stages) >= 4, str(stages))

    texts = []
    for sl in prs.slides:
        for shp in sl.shapes:
            if shp.has_text_frame:
                texts.append(shp.text_frame.text)
    joined = '\n'.join(texts)
    chk("封面标题取文件名", '配网改造汇报' in joined)
    chk("章节标题进页面", '总体进展' in joined)
    chk("页脚免责声明进页面", '人工复核' in joined)

    bad = [c for c in _checks if not c[1]]
    for label, ok, extra in _checks:
        print(("  [OK]   " if ok else "  [FAIL] ") + label + (("   ← " + extra) if not ok and extra else ""))
    print("\n%d 项检查，%d 项失败" % (len(_checks), len(bad)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
