"""把解析出的 blocks 切成"章节块"。

两个地方都用它，所以单独拎出来：
1. **问答带文件**（`doc_context`）：长文按问题召回相关块，不整篇塞给模型；
2. **文档任务**（速读/纠错）：逐块抽字段，再归并。

为什么按 heading 切而不是按固定字数切：
一份公文的"时限""责任单位"往往在标题下一整段里，按 1200 字硬切会把
「二、时间安排」和它下面的时限要求切到两个块里 —— 抽出来的字段就会缺一半。
按标题边界切，块的语义是完整的；只有单个章节超过 `max_chars` 时才在段内续切。
"""

from __future__ import annotations

from typing import Any

DEFAULT_MAX_CHARS = 1200
_HEADING_TYPES = ("heading",)


def _emit(out: list[dict], section: str, buf: list[str], para_from: int, para_to: int) -> None:
    text = "\n".join(t for t in buf if t.strip()).strip()
    if not text:
        return
    out.append({
        "idx": len(out) + 1,
        "section": section or "正文开头",
        "para_from": para_from,
        "para_to": para_to,
        "chars": len(text),
        "text": text,
    })


def chunk_blocks(
    blocks: list[dict],
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    skip_types: tuple[str, ...] = ("heading",),
) -> list[dict]:
    """blocks（`doc_files.blocks` 的形态）→ 章节块。

    `skip_types` 默认跳过标题块本身（标题已经进 `section`，再进正文会让模型
    把标题当成一句话去抽字段）。
    """
    out: list[dict] = []
    section = ""
    buf: list[str] = []
    para_from = 0
    para_to = 0
    size = 0

    def flush() -> None:
        nonlocal buf, size, para_from, para_to
        _emit(out, section, buf, para_from, para_to)
        buf = []
        size = 0
        para_from = 0
        para_to = 0

    for b in blocks:
        text = (b.get("text") or "").strip()
        if not text:
            continue
        no = int(b.get("para_no") or 0)

        if b.get("block_type") == "heading":
            flush()
            section = (b.get("heading_path") or text.split("\n")[0])[:80]
            if b.get("block_type") in skip_types:
                continue

        if size + len(text) > max_chars and buf:
            flush()

        if not buf:
            para_from = no
            if b.get("heading_path"):
                section = b["heading_path"]
        buf.append(text)
        para_to = no
        size += len(text)

    flush()
    return out


def outline(blocks: list[dict], *, limit: int = 40) -> list[str]:
    """文档目录（只要标题），给模型一个全局感，成本很低。"""
    heads = [(b.get("text") or "").split("\n")[0].strip()
             for b in blocks if b.get("block_type") == "heading"]
    return [h for h in heads if h][:limit]


def locate(blocks: list[dict], text: str) -> dict[str, Any] | None:
    """在原文里找一句话（自检用：抽出的时限/金额能不能回指原文）。"""
    needle = "".join((text or "").split())
    if not needle:
        return None
    for b in blocks:
        hay = "".join((b.get("text") or "").split())
        if needle in hay:
            return {"para_no": b.get("para_no"), "section": b.get("heading_path") or ""}
    return None
