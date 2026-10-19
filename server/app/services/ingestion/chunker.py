"""切片器。

设计要点
--------
1. **标题路径前置**：把「第三章 > 3.2 报销标准」拼到切片内容最前面。
   实测这一个小动作能显著提升层级较深段落的命中率。
2. **按语义块切分，再按长度切分**：优先保持段落/表格行完整，
   避免把一条报销标准从中间劈开。
3. **短块合并**：同一标题路径下的短块合并，避免产生大量无信息的碎块。
4. **重叠窗口**：防止关键信息正好落在切片边界上。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.ingestion.parsers.base import ParsedDocument

# 低于此长度的块视为碎片，可并入相邻同标题块（仅兜底，不做跨语义合并）
TINY_CHUNK_CHARS = 12
# 合并时允许的最大字符数
MAX_MERGED_CHARS = 1200
# 中文句子结束符
_SENTENCE_ENDS = "。！？；\n!?;"
# 中文检测（用于判定块是否含有效信息）
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _is_noise(text: str) -> bool:
    """是否应丢弃的噪声块。

    典型噪声：Markdown 里的 HTML 注释（上传工具写入的元数据）、纯符号行、
    无中文且极短的标签行。这类内容没有任何检索价值，入库只会成为干扰项。
    """
    t = text.strip()
    if not t:
        return True
    # HTML 注释块（含 <!-- ... --> 形式的元数据）
    if t.startswith("<!--"):
        return True
    # 去掉空白与纯符号后所剩无几 ⇒ 无信息
    compact = re.sub(r"[\s|>\-*#`~\u3000—－=+]+", "", t)
    if len(compact) < 4:
        return True
    # 无中文且极短（英文标签、编号行等）
    return not _CJK_RE.search(t) and len(compact) < 12


@dataclass
class ChunkDraft:
    """待入库的切片草稿。"""

    index: int
    content: str
    heading_path: str | None = None
    char_count: int = 0
    # ⭐ parent-child：所属父块（同章节聚合文本）。None 表示无更大上下文。
    parent_content: str | None = None


# 父块文本长度上限：超出则不生成父块，退化成返回小块本身，避免巨块。
PARENT_MAX_CHARS = 2000


def _split_long_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """把超长文本切成带重叠的片段，尽量在句子边界切开。"""
    if len(text) <= chunk_size:
        return [text]

    pieces: list[str] = []
    start = 0
    length = len(text)

    while start < length:
        end = min(start + chunk_size, length)

        # 优先在句子边界断开（在窗口后 30% 范围内找）
        if end < length:
            search_from = start + int(chunk_size * 0.6)
            best = -1
            for i in range(end, search_from, -1):
                if text[i - 1] in _SENTENCE_ENDS:
                    best = i
                    break
            if best > start:
                end = best

        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)

        if end >= length:
            break

        next_start = end - overlap
        # 保证前进，避免 overlap >= 片段长度时死循环
        start = max(next_start, start + 1)

    return pieces


def chunk_document(
    parsed: ParsedDocument,
    *,
    chunk_size: int = 512,
    chunk_overlap: int = 64,
) -> list[ChunkDraft]:
    """把解析结果切成切片草稿。"""
    chunk_size = max(chunk_size, 128)
    chunk_overlap = max(0, min(chunk_overlap, chunk_size // 2))

    drafts: list[ChunkDraft] = []

    for block in parsed.blocks:
        raw = block.text.strip()
        if not raw:
            continue

        # ⚠️ 纯标题块**不单独成片**。
        #    标题已经通过下面的「标题路径前置」进入了正文切片（【第三章 打卡与出勤】…），
        #    若再为标题本身留一个「只有标题、没有正文」的切片，它会作为噪声出现在
        #    检索结果里 —— 用户看到的第一条常常就是文档标题本身，观感像是「搜不出东西」。
        if block.block_type == "heading":
            continue

        # 噪声块（HTML 注释元数据 / 纯符号 / 无信息短行）直接丢弃，不进检索
        if _is_noise(raw):
            continue

        # ⚠️ 这里**只存纯正文**，标题路径单独留在 heading_path 字段，最后统一拼装。
        #    原先「每块各拼一次前缀」，合并后同一前缀会在一个切片里重复出现多次
        #    （实测 doc#53 有片重复 3 次），既浪费 token 又稀释 embedding 语义。
        if len(raw) <= chunk_size:
            drafts.append(
                ChunkDraft(index=0, content=raw, heading_path=block.heading_path)
            )
            continue

        for piece in _split_long_text(raw, chunk_size, chunk_overlap):
            drafts.append(
                ChunkDraft(index=0, content=piece, heading_path=block.heading_path)
            )

    # 兜底：整篇文档只有标题（没有任何正文块）时，仍保留标题，避免产生零切片文档
    if not drafts:
        for block in parsed.blocks:
            raw = block.text.strip()
            if raw:
                drafts.append(
                    ChunkDraft(index=0, content=raw, heading_path=block.heading_path)
                )

    # ⭐ 语义单元成片：不再跨块合并。
    #    原先把同一标题路径下的短块全并成一片，导致「一片含多个事实点」——
    #    一旦其中一个点与既有文档冲突，整片/整篇都会被隔离，用户看到的就是
    #    「因为一个小点，一大段信息被废掉」。这里只做极短碎片的兜底合并。
    drafts = _merge_tiny_adjacent(drafts)

    # ⭐ parent-child：在「拼标题前缀」之前，用纯正文按章节聚合父块。
    #    同 heading_path 下的多条切片拼成一个父块（整节），命中任一片都返回整节上下文，
    #    避免单切片信息不完整导致答案片面。长度超限或单切片章节则不生成父块。
    _assign_parent_content(drafts)

    # 标题路径统一在此拼装 —— 保证每片只出现一次
    for i, d in enumerate(drafts):
        if d.heading_path:
            d.content = f"【{d.heading_path}】\n{d.content}"
        d.index = i
        d.char_count = len(d.content)

    return drafts


def _assign_parent_content(drafts: list[ChunkDraft]) -> None:
    """给同一章节（heading_path）下的多条切片标注同一个父块上下文。

    设计要点
    --------
    - **调用时机**：必须在「拼标题前缀」之前 —— 此时 draft.content 仍是纯正文，
      聚合出的父块只在最前面出现一次「【章节】」前缀，不会像逐片拼接那样重复前缀。
    - **触发条件**：同一章节下 **多于 1 片** 且聚合正文 **不超过 PARENT_MAX_CHARS**。
      单切片章节本就是完整片段，无需父块；超长章节生成巨块会稀释检索、拖慢生成，故退化。
    - **退化**：不满足条件时 parent_content 保持 None，检索时返回小块本身（向后兼容）。
    - **安全性**：父块不参与向量索引，仅作为返回上下文，不影响切分粒度与冲突隔离。
    """
    if not drafts:
        return

    groups: dict[str | None, list[ChunkDraft]] = {}
    for d in drafts:
        groups.setdefault(d.heading_path, []).append(d)

    for key, group in groups.items():
        if len(group) <= 1:
            continue  # 单切片无更大上下文
        # 此时 group 内 content 仍为纯正文（前缀尚未拼装）
        body = "\n".join(d.content for d in group)
        if len(body) > PARENT_MAX_CHARS:
            continue  # 超长章节不生成父块，退化返回小块本身
        parent = f"【{key}】\n{body}" if key else body
        for d in group:
            d.parent_content = parent


def _merge_tiny_adjacent(drafts: list[ChunkDraft]) -> list[ChunkDraft]:
    """仅把极短碎片并入相邻同标题块。

    ⚠️ 不做「同标题就合并」——那会把多个独立事实点挤进一片，
    使冲突隔离的粒度被迫放大（一片被隔离 = 好几个事实一起消失）。
    只有短到几乎无信息量的碎片才并入上一块。
    """
    if not drafts:
        return []

    merged: list[ChunkDraft] = []

    for draft in drafts:
        if (
            merged
            and len(draft.content) < TINY_CHUNK_CHARS
            and merged[-1].heading_path == draft.heading_path
            and len(merged[-1].content) + len(draft.content) <= MAX_MERGED_CHARS
        ):
            merged[-1].content = f"{merged[-1].content}\n{draft.content}"
            continue
        merged.append(
            ChunkDraft(
                index=len(merged),
                content=draft.content,
                heading_path=draft.heading_path,
            )
        )

    return merged


def chunk_summary(drafts: list[ChunkDraft]) -> dict:
    """切片统计，用于入库后回显。"""
    if not drafts:
        return {"count": 0, "total_chars": 0, "avg_chars": 0, "max_chars": 0, "min_chars": 0}
    lengths = [len(d.content) for d in drafts]
    return {
        "count": len(drafts),
        "total_chars": sum(lengths),
        "avg_chars": round(sum(lengths) / len(lengths), 1),
        "max_chars": max(lengths),
        "min_chars": min(lengths),
    }
