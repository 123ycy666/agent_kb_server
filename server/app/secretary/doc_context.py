"""问答带文件时，把文件变成"够用又不爆上下文"的那一段文字。

核心问题：内网模型上下文只有 8k–32k，而 20 页公文是 2–3 万字（中文约 1.5 token/字）。
**整篇塞进去一定爆**，塞前 6000 字又常常正好错过"时限要求"那一节。
所以这里做三件事：

1. 短文件（≤ `FULL_CAP`）—— 整篇给，别节选（节选会漏）；
2. 长文件 —— 按问题**召回相关章节块**（字面 bigram 打分，纯本地、不过网），
   同时给一份**目录**让它知道全文有什么，避免"只看到 5 段就以为文件只有这些"；
3. 每个文件都带上 `used_chars / truncated / cited_sections`，回答里的
   「出处章节」必须从这里面挑 —— 这样"可追溯"是接口能验的，而不是靠模型自觉。

⚠️ 不用向量召回：本模块要在**没有外网、没有向量库**的内网机器上跑，
   而且公文问题多是字面重合（"上报时限""责任单位"），bigram 打分够用且可解释。
"""

from __future__ import annotations

import re

from app.secretary import doc_chunks, doc_files

# 单文件整篇直给的上限；超过就走召回
FULL_CAP = 6000
# 全部文件合计进上下文的预算（中文 6000 字 ≈ 9k token，给模型留够输出空间）
TOTAL_BUDGET = 6000
# 长文件：最多取几块
MAX_CHUNKS = 8

_PUNCT = re.compile(r"[\s，。、；：？！“”‘’（）()\[\]【】《》<>~·\-—_/\\|+*#%&@^=]+")
_STOP = {"什么", "怎么", "这个", "那个", "哪些", "多少", "是否", "以及", "我们", "你们"}

# 同义扩展（只放**高频且口径明确**的几组，不做同义词库）：
# 实测「上报时限」搜不到「请于10月31日前报送」—— 公文里"上报/报送/递交"同义，
# "时限/期限/截止"同义，不扩展就一定漏。加表比加参数好：**这是数据，不是逻辑**。
_SYNONYMS = {
    "上报": ("报送", "递交", "提交", "报"),
    "报送": ("上报", "递交", "提交"),
    "时限": ("时间", "期限", "截止", "日期", "安排"),
    "期限": ("时限", "截止", "日期"),
    "时间": ("时限", "日期", "安排"),
    "金额": ("资金", "经费", "预算", "万元", "元"),
    "责任": ("分工", "负责", "责任人", "单位"),
    "分工": ("责任", "负责"),
    "罚则": ("考核", "问责", "通报", "扣分"),
    "要求": ("应当", "必须", "确保", "不得"),
}


def _tokens(question: str) -> set[str]:
    """问题 → 字面特征：去标点后取 2-gram（中文短问句最稳），数字/日期整段保留，再说同义。"""
    q = _PUNCT.sub("", question or "")
    grams = {q[i:i + 2] for i in range(len(q) - 1)}
    grams |= set(re.findall(r"\d{2,}|[一二三四五六七八九十]{1,3}月|\d+月\d+日", question or ""))
    grams = {g for g in grams if g not in _STOP}
    for key, alts in _SYNONYMS.items():
        if key in q:
            grams |= set(alts)
    return grams


def _score(text: str, tokens: set[str]) -> float:
    if not tokens:
        return 0.0
    hit = sum(1 for t in tokens if t in text)
    # 用命中数 / sqrt(长度) 归一：长块不该仅因为长就占便宜
    return hit / (len(text) ** 0.5 + 1.0)


def _full_text(blocks: list[dict]) -> str:
    """整篇（带章节标记），短文件直接用它。"""
    out: list[str] = []
    last_sec = ""
    for b in blocks:
        text = (b.get("text") or "").strip()
        if not text:
            continue
        sec = b.get("heading_path") or ""
        if sec and sec != last_sec:
            out.append(f"[{sec}]")
            last_sec = sec
        out.append(text)
    return "\n".join(out)


def build(question: str, file_ids: list[str], user_id: int, *,
          budget: int = TOTAL_BUDGET) -> dict:
    """返回 {context, files, warnings}。file_ids 为空时 context 为空串。

    任何一个文件读不出来都不抛异常 —— 一个坏文件不该让整次提问失败。
    """
    fids = [f for f in (file_ids or []) if f]
    if not fids:
        return {"context": "", "files": [], "warnings": []}

    tokens = _tokens(question)
    per_file = max(1500, budget // len(fids))
    blocks_out: list[str] = []
    files_info: list[dict] = []
    warnings: list[str] = []

    for i, fid in enumerate(fids, start=1):
        try:
            meta = doc_files.load(fid, user_id)
        except doc_files.DocFileError as exc:
            warnings.append(f"文件 {fid} 不可用：{exc.message}")
            continue

        if not (meta.get("parsed") or {}).get("ok"):
            warnings.append(
                f"《{meta['name']}》没能解析出文字（{(meta.get('parsed') or {}).get('error', '未知原因')}）"
            )
            continue

        data = doc_files.blocks(fid, user_id, offset=0, limit=100000)
        blocks = data.get("blocks") or []
        total_chars = sum(len(b.get("text") or "") for b in blocks)
        heads = doc_chunks.outline(blocks)

        if total_chars <= min(FULL_CAP, per_file):
            body = _full_text(blocks)
            used, cited, truncated, mode = total_chars, heads[:20], False, "全文"
        else:
            chunks = doc_chunks.chunk_blocks(blocks)
            ranked = sorted(chunks, key=lambda c: _score(c["text"], tokens), reverse=True)
            picked = [c for c in ranked[:MAX_CHUNKS] if _score(c["text"], tokens) > 0]
            if not picked:
                # 问句太泛（「这文件说什么」）→ 每节的**第一块**都取一份，
                # 保证覆盖全文结构，而不是只给开头三段（那样时限/责任全在背后）
                seen: set[str] = set()
                for c in chunks:
                    if c["section"] in seen:
                        continue
                    seen.add(c["section"])
                    picked.append(c)
            picked.sort(key=lambda c: c["idx"])           # 按原文顺序还原，别打乱阅读
            body_parts: list[str] = []
            used = 0
            for c in picked:
                if used + c["chars"] > per_file and body_parts:
                    break
                body_parts.append(f"[{c['section']}]\n{c['text']}")
                used += c["chars"]
            body = "\n\n".join(body_parts)
            cited = [c["section"] for c in picked][:12]
            truncated, mode = True, f"节选 {len(picked)}/{len(chunks)} 段"

        files_info.append({
            "file_id": fid,
            "name": meta["name"],
            "chars": total_chars,
            "used_chars": used,
            "truncated": truncated,
            "mode": mode,
            "cited_sections": cited,
            "warnings": (meta.get("parsed") or {}).get("warnings") or [],
        })

        head = f"【文件{i}】《{meta['name']}》（全文约 {total_chars} 字，{mode}）"
        if truncated and heads:
            head += "\n该文件目录（**只是目录**，正文是下面节选的部分）：\n" + "\n".join(f"- {h}" for h in heads[:20])
        blocks_out.append(head + "\n" + body)

    context = "\n\n".join(blocks_out)
    return {"context": context, "files": files_info, "warnings": warnings}
