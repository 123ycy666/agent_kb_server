"""速读流水线（`digest`）：20 页公文 → 一页纸要点 + 待办清单 + 出处。

骨架写死在代码里（设计稿 §5 方案 C），模型只干两件事：
**逐块抽字段**、**把已核实的事实写成一段话**。理由：20 页 × 逐块抽取 = 十几次调用，
让模型自己决定"先干嘛后干嘛"必然每次都不一样，既没法验收也没法给进度。

一页纸的四个产出（对应领导真实要的）：
1. 结论：这是什么文件、要我们干什么、什么时候要；
2. 待办清单表：事项 / 责任单位 / 时限 / 需报材料 / 出处章节 → 可直接导入督办；
3. 风险与易漏：罚则、一票否决、口径变化；
4. 每条时限金额**必须回指原文**（`locate()` 校验），查不到的标"未核实"而不是丢掉。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from app.secretary import doc_chunks, doc_files, docgen

TASK = "digest"
CHUNK_CHARS = 1200
MAX_CHUNKS = 30          # 安全阀：>30 块（约 4 万字）先只跑前 30 块并告警

# 测试可替换：`set_llm(lambda messages, max_tokens: "……")`
_LLM = None


def set_llm(fn) -> None:
    """注入模型调用（单测用假模型；线上不调）。"""
    global _LLM
    _LLM = fn


def _llm(messages: list[dict], max_tokens: int = 2000) -> str:
    if _LLM is not None:
        return _LLM(messages, max_tokens)
    from app.secretary import agent as agent_mod
    from app.secretary import config as sec_config

    resp = agent_mod._chat(messages, sec_config.MODEL, use_tools=False,
                           max_tokens=max_tokens, think=False)
    return (resp["choices"][0]["message"].get("content") or "").strip()


def _json_of(text: str) -> dict:
    """模型输出 → dict。内网小模型经常带 ```json 包裹或前后废话，这里只做必要的清洗。"""
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
    m = re.search(r"\{.*\}", t, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


_EXTRACT_PROMPT = (
    "你是公文分析助手。下面是公文的一个片段，请只抽下面这些字段，**抽不到就留空，绝不推测**。\n"
    "输出严格 JSON：{\"items\":[{\"matter\":\"要做的事\",\"unit\":\"责任单位\",\"deadline\":\"时限\","
    "\"materials\":[\"需上报的材料\"],\"quote\":\"支撑这句话的原文短句（不超过60字，必须原文照抄）\"}],"
    "\"risks\":[\"罚则/一票否决/口径变化等风险点\"]}\n"
    "三条硬要求（线上实测踩过，务必遵守）：\n"
    "① **一句话里有多个并列动作时，合成一条，不要拆开**。"
    "例如「制定实施方案、明确责任分工、细化工作措施」是一个事项，写成一条"
    "「制定实施方案、明确责任分工、细化工作措施」，不要拆成三条。\n"
    "② **只抽对执行有意义的**：谁要做、做什么、什么时间前、要报什么材料、达到什么标准。"
    "纯口号、泛泛而谈的「要高度重视」「要抓好落实」不要抽。\n"
    "③ 同一段里同一个意思只输出一次；没写时限/单位就不要填；quote 必须能在片段里原样找到。\n"
    "④ matter 要写成完整的事（谁+做什么），**不要只写「具体实施」「统筹协调」这类没有对象的词**；"
    "单位已经填进 unit 的，matter 里不必重复单位。"
)


def _extract_chunk(chunk: dict) -> dict:
    prompt = (f"【片段位置】{chunk['section']}\n"
              f"【片段正文】\n{chunk['text']}")
    out = _llm([{"role": "system", "content": _EXTRACT_PROMPT},
                {"role": "user", "content": prompt}], max_tokens=1200)
    data = _json_of(out)
    items = data.get("items") or []
    if not isinstance(items, list):
        items = []
    for it in items:
        if isinstance(it, dict):
            it["_section"] = chunk["section"]
            it["_para"] = chunk.get("para_from")
    return {"items": [i for i in items if isinstance(i, dict)],
            "risks": [r for r in (data.get("risks") or []) if isinstance(r, str)]}


def _norm(s: str) -> str:
    return re.sub(r"[\s，。、；：（）()“”\"'·]", "", str(s or ""))[:40]


def _merge(extracts: list[dict]) -> tuple[list[dict], list[str]]:
    """同一件事在多块/多句里重复出现 → 合并。

    ⚠️ 线上实测的教训（2026-10-19 上线冒烟）：只按"规范化后完全相等"去重不够。
    模型会把同一句话拆成 `结合实际制定实施方案` / `明确责任分工` / `细化工作措施`
    这样 5 条碎片，再重复 2 条整句 —— 13 条待办里真正有用的只有 1 条。
    所以这里加**子串包含**判定：一条是另一条的子串（且都 ≥4 字）就算同一件事，
    并保留更完整的那种说法。
    """
    merged: list[dict] = []
    keys: list[str] = []          # 与 merged 平行：规范化后的 matter
    risks: list[str] = []

    def absorb(cur: dict, it: dict) -> None:
        for field in ("unit", "deadline", "quote"):
            if not cur.get(field) and it.get(field):
                cur[field] = it[field]
        mats = list(cur.get("materials") or [])
        for m in (it.get("materials") or []):
            if m and m not in mats:
                mats.append(m)
        cur["materials"] = mats
        if len(_norm(it.get("matter"))) > len(_norm(cur.get("matter"))):
            cur["matter"] = it.get("matter")

    for ext in extracts:
        for it in ext["items"]:
            key = _norm(it.get("matter"))
            if not key:
                continue
            hit = None
            for i, k in enumerate(keys):
                if k == key:
                    hit = i
                    break
                if len(k) >= 4 and len(key) >= 4 and (k in key or key in k):
                    hit = i
                    break
            if hit is None:
                merged.append(dict(it))
                keys.append(key)
            else:
                absorb(merged[hit], it)
                keys[hit] = _norm(merged[hit].get("matter"))
        for r in ext["risks"]:
            if r not in risks:
                risks.append(r)
    return merged, risks


def _verify(items: list[dict], blocks: list[dict]) -> list[dict]:
    """自检：quote 能不能在原文里找到；找不到就标未核实（不删，删了人就不知道有这回事）。"""
    out = []
    for it in items:
        quote = (it.get("quote") or "").strip()
        hit = doc_chunks.locate(blocks, quote) if quote else None
        it["verified"] = bool(hit)
        if hit:
            it["section"] = hit.get("section") or it.get("_section")
            it["para_no"] = hit.get("para_no")
        else:
            it["section"] = it.get("_section")
            it["para_no"] = it.get("_para")
        out.append(it)
    return out


_SUMMARY_PROMPT = (
    "你是办公室主任的助手。下面是一份公文里抽出来的**事实清单**（已核对过出处）。\n"
    "请写一份**一页纸速读**，Markdown，严格按这四段，不要加别的段：\n"
    "## 结论\n（3 句话内：这是什么文件、要我们干什么、什么时候要；没有的事不要写）\n"
    "## 一页纸要点\n（分条，每条一句，不超过 6 条）\n"
    "## 待办清单\n（每行：- [ ] 事项｜责任单位｜时限｜需报材料）\n"
    "## 风险与易漏\n（罚则、一票否决、口径变化；没有就写「未提及」）\n"
    "纪律：**只许用下面清单里的事实**，清单里没有的写「文件中未提及」；不许补充常识、不许推测。"
)


def _summary(items: list[dict], risks: list[str], title: str) -> str:
    lines = [f"文件：{title}"]
    for i, it in enumerate(items, 1):
        lines.append(
            f"{i}. 事项：{it.get('matter')}｜单位：{it.get('unit') or '未提及'}"
            f"｜时限：{it.get('deadline') or '未提及'}"
            f"｜材料：{'、'.join(it.get('materials') or []) or '未提及'}"
            f"｜出处：{it.get('section') or '未提及'}"
            f"｜核实：{'是' if it.get('verified') else '否'}"
        )
    if risks:
        lines.append("风险点：" + "；".join(risks))
    return _llm([{"role": "system", "content": _SUMMARY_PROMPT},
                 {"role": "user", "content": "\n".join(lines)}], max_tokens=1600)


def run(file_id: str, user_id: int, options: dict | None = None,
        progress=None, out_dir: Path | None = None) -> dict:
    """跑一次速读。progress(pct:int, stage:str) 由任务层传入；返回结果结构由任务层落盘。"""
    opts = options or {}
    role = (opts.get("role") or "").strip()
    say = progress or (lambda pct, stage: None)

    def _tick(pct: int, stage: str) -> None:
        say(pct, stage)

    _tick(3, "读取文件")
    meta = doc_files.load(file_id, user_id)
    blocks = doc_files.blocks(file_id, user_id, offset=0, limit=100000)["blocks"]
    chunks = doc_chunks.chunk_blocks(blocks, max_chars=CHUNK_CHARS)
    warnings: list[str] = []
    if not chunks:
        raise RuntimeError("文件里没有可分析的正文（可能全是表格或图片）")
    if len(chunks) > MAX_CHUNKS:
        warnings.append(f"文件过长，只分析了前 {MAX_CHUNKS}/{len(chunks)} 段")
        chunks = chunks[:MAX_CHUNKS]

    extracts = []
    t0 = time.time()
    for i, ch in enumerate(chunks, 1):
        _tick(5 + int(60 * i / len(chunks)), f"抽取 {i}/{len(chunks)} 段")
        try:
            extracts.append(_extract_chunk(ch))
        except Exception as exc:                       # noqa: BLE001
            warnings.append(f"第 {i} 段抽取失败：{type(exc).__name__}")
    items, risks = _merge(extracts)
    items = _verify(items, blocks)
    _tick(72, "归并去重")

    inner = [it for it in items if it.get("matter")]
    if not inner:
        warnings.append("没抽出任何事项，可能这份文件是纯说明性内容")
    # 展示顺序：有明确时限的排前面，其次有责任单位的。稳定排序，同组内保持原文顺序 ——
    # 领导先看到"什么时候要、谁来做"，碎片化的泛要求自然沉到后面。
    inner.sort(key=lambda it: (0 if it.get("deadline") else 1, 0 if it.get("unit") else 1))

    md = ""
    try:
        _tick(80, "生成一页纸")
        md = _summary(inner, risks, meta["name"])
    except Exception as exc:                           # noqa: BLE001
        warnings.append(f"生成摘要失败（清单仍然可用）：{type(exc).__name__}")

    unverified = sum(1 for it in inner if not it.get("verified"))
    if unverified:
        warnings.append(f"{unverified} 条事项的原文出处未核实，请人工确认")

    # 成果文件
    outputs: list[Path] = []
    if out_dir is not None and (opts.get("want_docx", True) or opts.get("want_xlsx", True)):
        _tick(90, "生成成果文件")
        base = Path(meta["name"]).stem
        if opts.get("want_docx", True):
            rows = [[it.get("matter", ""), it.get("unit") or "未提及",
                     it.get("deadline") or "未提及",
                     "、".join(it.get("materials") or []) or "—",
                     it.get("section") or "—",
                     "是" if it.get("verified") else "待核实"] for it in inner]
            outputs.append(docgen.write_docx(
                out_dir / f"速读_{base}.docx",
                f"{base}·报送",
                [("一、文件速读", md or "（摘要生成失败，见下方清单）")],
                [("二、待办清单", ["事项", "责任单位", "时限", "需报材料", "出处", "已核实"], rows)],
            ))
        if opts.get("want_xlsx", True):
            outputs.append(docgen.write_xlsx(
                out_dir / f"待办清单_{base}.xlsx",
                [("待办清单", ["序号", "事项", "责任单位", "时限", "需报材料", "出处章节", "已核实"],
                  [[i, it.get("matter", ""), it.get("unit") or "未提及",
                    it.get("deadline") or "未提及",
                    "、".join(it.get("materials") or []) or "",
                    it.get("section") or "",
                    "是" if it.get("verified") else "待核实"] for i, it in enumerate(inner, 1)])],
            ))

    _tick(100, "完成")
    return {
        "task": TASK,
        "file_id": file_id,
        "file_name": meta["name"],
        "role": role,
        "answer_markdown": md.strip() + "\n\n---\n" + _list_md(inner, warnings),
        "items": inner,
        "risks": risks,
        "evidence": [{"claim": it.get("matter"), "quote": it.get("quote"),
                      "section": it.get("section"), "para_no": it.get("para_no"),
                      "verified": it.get("verified")} for it in inner],
        "warnings": warnings,
        "stats": {"chunks": len(chunks), "items": len(inner),
                  "unverified": unverified, "elapsed_ms": int((time.time() - t0) * 1000)},
        "outputs": [p.name for p in outputs],
    }


def _list_md(items: list[dict], warnings: list[str]) -> str:
    """纯事实清单（不经过模型）——摘要挂了也能交差，这是底线。"""
    out = ["## 待办清单（逐条原文可核）"]
    if not items:
        out.append("- 未抽出事项")
    for i, it in enumerate(items, 1):
        flag = "" if it.get("verified") else "（出处待核）"
        out.append(
            f"{i}. **{it.get('matter')}**｜{it.get('unit') or '责任单位未提及'}"
            f"｜{it.get('deadline') or '时限未提及'}"
            f"｜材料：{'、'.join(it.get('materials') or []) or '未提及'}"
            f"｜出处：{it.get('section') or '—'}{flag}"
        )
    if warnings:
        out.append("\n> 提示：" + "；".join(warnings))
    return "\n".join(out)
