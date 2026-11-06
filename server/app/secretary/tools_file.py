# -*- coding: utf-8 -*-
"""文件类工具（2026-11-05）：读文件 / 取知识库全文 / 出文件 / 跑脚本。

模型能主动调的四只手：
    read_file   按块读上传文件（分页、带章节、带段落号）
    kb_read     按 doc_id 或标题取知识库文档全文（权限同检索）
    make_file   出 docx / pptx / xlsx，过 inspect 自检，产物落成可下载文件
    run_code    沙箱跑 python（技能包在 PYTHONPATH），兜底用

执行上下文（user_id / 产物目录）由 ask() 入口 `set_ctx()` 登记 —
和 kb_adapter 一样，工具是模型自己调的，参数传不进去。
"""
from __future__ import annotations

import json
import time
from contextvars import ContextVar
from pathlib import Path

_CTX: ContextVar = ContextVar('tools_file_ctx', default={})

MAX_CHARS = 6000          # 单次返回给模型的正文上限
DEFAULT_LIMIT = 40        # 一次读多少块


def set_ctx(user_id=None, out_dir=None) -> None:
    _CTX.set({'user_id': user_id, 'out_dir': str(out_dir or '')})


def get_ctx() -> dict:
    return _CTX.get() or {}


# ── 读 ─────────────────────────────────────────────────────────

def read_file(file_id: str, offset: int = 0, limit: int = DEFAULT_LIMIT) -> dict:
    """读上传文件的一段。返回块列表 + next_offset（读完给 null）。"""
    uid = get_ctx().get('user_id')
    from app.secretary import doc_files
    if not uid:
        return {'error': '没有登录身份，读不了文件'}
    try:
        meta = doc_files.load(file_id, uid)
        data = doc_files.blocks(file_id, uid, offset=int(offset or 0), limit=int(limit or DEFAULT_LIMIT))
    except doc_files.DocFileError as exc:
        return {'error': exc.message}
    blocks = data.get('blocks') or []
    off = int(offset or 0)
    used, out, taken = 0, [], 0
    for i, b in enumerate(blocks):
        text = str(b.get('text') or '').strip()
        if not text:
            taken = i + 1
            continue
        if used + len(text) > MAX_CHARS and out:
            break
        out.append({'para_no': b.get('para_no'), 'heading': b.get('heading_path') or '',
                    'type': b.get('block_type') or 'text', 'text': text})
        used += len(text)
        taken = i + 1
    total = int(data.get('total') or 0)
    # ★ 2026-11-08：next_offset 必须按**实际给出**的块数算。原来按取回的块数算，
    #   一旦撞上 MAX_CHARS 提前 break，剩下的块既没给、next_offset 又是 null，
    #   模型只能回头自己猜 offset 重读（实测速读/PPT/Word 三条链路都白读两轮）。
    end = off + taken
    nxt = end if end < total else None
    note = ('接着读 next_offset=%d（还剩 %d 块，全文 %d 块）。' % (nxt, total - end, total)
            if nxt is not None else
            '已到末尾：全文 %d 块，本次给的是第 %d-%d 块，已经读完，不要重复读。'
            % (total, off + 1, end))
    return {'file_id': file_id, 'name': meta.get('name'), 'total_blocks': total,
            'note': note,
            'offset': off, 'blocks': out, 'next_offset': nxt}


def kb_read(doc_id=None, doc_name: str = '', offset: int = 0, limit: int = DEFAULT_LIMIT) -> dict:
    """取知识库一篇文档的正文（按 chunk_index 顺序，带章节路径）。"""
    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.models import Document, DocumentChunk
    from app.secretary import kb_adapter
    from app.services.permission import SCOPE_SEARCH, visible_document_filter

    actor = kb_adapter.current_actor()
    if actor is None:
        return {'error': '没有登录身份，读不了知识库'}
    db = SessionLocal()
    try:
        stmt = select(Document.id, Document.filename).where(
            Document.is_active.is_(True), visible_document_filter(actor, SCOPE_SEARCH))
        if doc_id:
            stmt = stmt.where(Document.id == int(doc_id))
        elif doc_name:
            stmt = stmt.where(Document.filename.ilike('%' + str(doc_name) + '%'))
        rows = db.execute(stmt.limit(5)).all()
        if not rows:
            return {'error': '知识库里没有这篇（或没有权限）'}
        d_id, fname = rows[0][0], rows[0][1]
        chunks = db.execute(
            select(DocumentChunk.chunk_index, DocumentChunk.heading_path, DocumentChunk.content)
            .where(DocumentChunk.doc_id == d_id)
            .order_by(DocumentChunk.chunk_index)
            .offset(int(offset or 0)).limit(int(limit or DEFAULT_LIMIT))).all()
        total = db.execute(
            select(DocumentChunk.id).where(DocumentChunk.doc_id == d_id)).all()
        off = int(offset or 0)
        used, out, taken = 0, [], 0
        for i, (idx, head, content) in enumerate(chunks):
            text = str(content or '').strip()
            if used + len(text) > MAX_CHARS and out:
                break
            out.append({'chunk': int(idx), 'heading': head or '', 'text': text})
            used += len(text)
            taken = i + 1
        end = off + taken
        nxt = end if end < len(total) else None
        note = ('接着读 next_offset=%d（还剩 %d 块，全文 %d 块）。' % (nxt, len(total) - end, len(total))
                if nxt is not None else
                '已到末尾：全文 %d 块，本次给的是第 %d-%d 块，已经读完，不要重复读。'
                % (len(total), off + 1, end))
        return {'doc_id': d_id, 'name': fname, 'total_chunks': len(total), 'note': note,
                'offset': off, 'chunks': out, 'next_offset': nxt,
                'candidates': [{'doc_id': r[0], 'name': r[1]} for r in rows]}
    except Exception as exc:                       # noqa: BLE001
        return {'error': '%s: %s' % (type(exc).__name__, str(exc)[:200])}
    finally:
        db.close()


# ── 写 ─────────────────────────────────────────────────────────

_XLSX_CODE = '''\
import json
from openpyxl import Workbook
SPEC = json.loads({spec_json!r})
OUT = {out!r}
wb = Workbook()
first = True
for sh in SPEC.get("sheets") or []:
    ws = wb.active if first else wb.create_sheet()
    ws.title = (sh.get("name") or "Sheet1")[:31]
    first = False
    for row in sh.get("rows") or []:
        ws.append([("" if c is None else c) for c in row])
    ws.freeze_panes = "A2"
wb.save(OUT)
print("built", OUT)
'''


def make_file(kind: str, spec: dict, self_check: bool = True) -> dict:
    """出 docx / pptx / xlsx：走技能包 + inspect 自检，产物落成可下载文件。

    spec 形状（与内容直接对应，模型不用写代码）：
        docx  {'title': '', 'sections': [[小节标题, 正文], ...]}
        pptx  {'title': '', 'slides': [{'layout':'cover|section|bullets|end', 'title':'', 'bullets':[..]}]}
        xlsx  {'sheets': [{'name': '', 'rows': [[...], ...]}]}
    """
    from app.secretary import doc_files, office_runner as R
    uid = get_ctx().get('user_id')
    if not uid:
        return {'error': '没有登录身份，出不了文件'}
    kind = (kind or '').strip().lower()
    spec = spec or {}
    base = (Path(get_ctx().get('out_dir') or 'data/agent_out')
            / ('f%s' % int(time.time() * 1000))).resolve()
    wd = R.new_job_dir(base)
    out_dir = wd / 'out'
    title = ' '.join(str(spec.get('title') or '').split())[:40]
    if not title:
        _sheets = spec.get('sheets') or []
        _first = _sheets[0] if _sheets else {}
        title = ' '.join(str((_first or {}).get('name') or '').split())[:40] or '产物'

    if kind == 'docx':
        from app.secretary.pipeline_doc_write import _BUILD_CODE as code_tpl
        out_path = out_dir / ('%s.docx' % title)
        code = (code_tpl
                .replace('{spec_json!r}', repr(json.dumps(
                    {'title': spec.get('title') or title,
                     'sections': spec.get('sections') or [[ '', spec.get('text') or '' ]]},
                    ensure_ascii=False)))
                .replace('{out!r}', repr(str(out_path)))
                .replace('{fmt_json!r}', repr(json.dumps({}, ensure_ascii=False))))
        script = R.DOCX_SCRIPTS / 'inspect_docx.py'
    elif kind == 'pptx':
        from app.secretary.pipeline_deck import _BUILD_CODE as code_tpl
        out_path = out_dir / ('%s.pptx' % title)
        code = (code_tpl
                .replace('{spec_json!r}', repr(json.dumps(
                    {'title': spec.get('title') or title,
                     'slides': spec.get('slides') or [{'layout': 'cover', 'title': title}]},
                    ensure_ascii=False)))
                .replace('{out!r}', repr(str(out_path))))
        script = R.PPTX_SCRIPTS / 'inspect_deck.py'
    elif kind == 'xlsx':
        out_path = out_dir / ('%s.xlsx' % title)
        code = _XLSX_CODE.replace('{spec_json!r}', repr(json.dumps(spec, ensure_ascii=False))) \
                         .replace('{out!r}', repr(str(out_path)))
        script = R.XLSX_SCRIPTS / 'inspect_workbook.py'
    else:
        return {'error': 'kind 只能是 docx / pptx / xlsx'}

    res = R.run_code(code, wd, timeout=180)
    if not res['ok']:
        return {'error': '生成失败：%s' % ((res['stderr'] or res['stdout'] or '')[-400:])}
    verdict, tail = '未自检', ''
    if self_check and script is not None:
        insp = R.run_script(script, [str(out_path)], wd, timeout=180)
        tail = (insp['stdout'] or insp['stderr'] or '')[-500:]
        verdict = '通过' if '结论: 通过' in (insp['stdout'] or '') else '不通过'
        if verdict != '通过':
            return {'error': '自检不通过，已拦下交付', 'self_check': {'verdict': verdict, 'tail': tail}}

    data = out_path.read_bytes()
    meta = doc_files.save(uid, out_path.name, data, purpose='chat')
    return {'kind': kind, 'name': out_path.name, 'bytes': len(data),
            'file_id': meta['file_id'], 'url': '/api/files/%s/download' % meta['file_id'],
            'self_check': {'verdict': verdict, 'tail': tail}}


def run_code(code: str, timeout: int = 60) -> dict:
    """沙箱跑一段 python（技能包在 PYTHONPATH，产物从 out/ 取）。"""
    from app.secretary import doc_files, office_runner as R
    uid = get_ctx().get('user_id')
    if not uid:
        return {'error': '没有登录身份，跑不了脚本'}
    base = (Path(get_ctx().get('out_dir') or 'data/agent_out')
            / ('c%s' % int(time.time() * 1000))).resolve()
    wd = R.new_job_dir(base)
    try:
        res = R.run_code(code, wd, timeout=int(timeout or 60))
    except R.RunnerError as exc:
        return {'error': str(exc)}
    outs = []
    for item in R.outputs(wd):
        p = wd / 'out' / item['name']
        try:
            meta = doc_files.save(uid, p.name, p.read_bytes(), purpose='chat')
            outs.append({'name': p.name, 'file_id': meta['file_id'],
                         'url': '/api/files/%s/download' % meta['file_id']})
        except Exception:                          # noqa: BLE001 - 产物存不进不影响脚本结果
            outs.append({'name': p.name})
    return {'ok': res['ok'], 'exit': res['exit'], 'ms': res['ms'],
            'stdout': (res['stdout'] or '')[-2000:], 'stderr': (res['stderr'] or '')[-1000:],
            'outputs': outs}
