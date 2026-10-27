# -*- coding: utf-8 -*-
"""PDF 工具：合并 / 拆分 / 抽页（用 pypdf，不自己写 PDF 结构）。

为什么用 pypdf（2026-10-21）
--------------------------
PDF 的交叉引用表、对象流、加密字典都不该手写；pypdf 是 BSD、纯 Python、离线可用。
我们只做**参数校验 + 命名 + 结果说明**，格式活全交给它。

边界：只处理**未加密**的 PDF；加密的明确报错（不静默跳过页）。
"""
from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader, PdfWriter

MAX_PAGES = 2000


class PdfError(RuntimeError):
    pass


def _reader(path) -> PdfReader:
    p = Path(str(path))
    if not p.is_file():
        raise PdfError('PDF 不存在：%s' % p.name)
    try:
        r = PdfReader(str(p))
    except Exception as exc:                       # noqa: BLE001
        raise PdfError('不是可读的 PDF（%s）：%s' % (p.name, str(exc)[:120])) from exc
    if getattr(r, 'is_encrypted', False):
        raise PdfError('PDF 有密码保护，请先解密再处理：%s' % p.name)
    if len(r.pages) > MAX_PAGES:
        raise PdfError('页数太多（%d 页，上限 %d）' % (len(r.pages), MAX_PAGES))
    return r


def merge(paths: list, out_path, *, add_bookmarks: bool = True) -> dict:
    """按给定顺序合并多个 PDF。"""
    paths = [p for p in (paths or []) if str(p).strip()]
    if len(paths) < 2:
        raise PdfError('合并至少需要 2 个 PDF（现在 %d 个）' % len(paths))
    w = PdfWriter()
    detail = []
    for p in paths:
        r = _reader(p)
        start = len(w.pages)
        for page in r.pages:
            w.add_page(page)
        if add_bookmarks:
            w.add_outline_item(Path(str(p)).stem, start)
        detail.append({'file': Path(str(p)).name, 'pages': len(r.pages), 'from': start})
    out = Path(str(out_path))
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, 'wb') as f:
        w.write(f)
    return {'op': 'merge', 'output': out.name, 'pages': len(w.pages), 'inputs': detail}


def split(path, out_dir, *, every: int = 1, prefix: str = '') -> dict:
    """按每 N 页拆成多个 PDF。"""
    r = _reader(path)
    n = max(1, int(every or 1))
    out_dir = Path(str(out_dir))
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = prefix or Path(str(path)).stem
    made = []
    for i in range(0, len(r.pages), n):
        w = PdfWriter()
        for page in r.pages[i:i + n]:
            w.add_page(page)
        name = '%s_%03d-%03d.pdf' % (stem, i + 1, min(i + n, len(r.pages)))
        with open(out_dir / name, 'wb') as f:
            w.write(f)
        made.append(name)
    return {'op': 'split', 'pages': len(r.pages), 'every': n, 'outputs': made}


def pick_pages(path, out_path, pages: list[int]) -> dict:
    """抽指定页（从 1 开始）拼成新 PDF。"""
    r = _reader(path)
    total = len(r.pages)
    want = []
    for x in pages or []:
        try:
            k = int(x)
        except (TypeError, ValueError):
            raise PdfError('页码必须是整数：%r' % (x,)) from None
        if not (1 <= k <= total):
            raise PdfError('页码 %d 超出范围（1–%d）' % (k, total))
        want.append(k)
    if not want:
        raise PdfError('没有给页码')
    w = PdfWriter()
    for k in want:
        w.add_page(r.pages[k - 1])
    out = Path(str(out_path))
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, 'wb') as f:
        w.write(f)
    return {'op': 'pick', 'output': out.name, 'pages': len(want),
            'from_file': Path(str(path)).name}


def info(path) -> dict:
    r = _reader(path)
    return {'pages': len(r.pages),
            'encrypted': bool(getattr(r, 'is_encrypted', False)),
            'size_kb': round(Path(str(path)).stat().st_size / 1024.0, 1)}
