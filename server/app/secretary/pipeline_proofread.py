"""公文纠错流水线（`proofread`）：错别字 / 标点 / 数字用法 / 层次序号 / 前后矛盾。

**先跑规则、再交给模型**，顺序不能反（这是审查类活的关键）：
确定性错误（"其它"、"截止目前"、英文逗号、序号混用）用规则查，100% 稳定、可解释、
不花钱；模型只负责规则覆盖不到的"病句 / 表述不清 / 前后矛盾"。
反过来（全交给模型）会出现"每次报的错都不一样"，领导一对比就不信了。

⚠️ 不产"修订痕迹版"：python-docx 做不了 tracked changes（见设计稿 §8）。
   产出是**问题清单（带原文与出处）+ 统计**，改不改、怎么改由人定。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from app.secretary import doc_chunks, doc_files, docgen

TASK = "proofread"
MAX_CHUNKS = 30

_LLM = None


def set_llm(fn) -> None:
    global _LLM
    _LLM = fn


def _llm(messages: list[dict], max_tokens: int = 1200) -> str:
    if _LLM is not None:
        return _LLM(messages, max_tokens)
    from app.secretary import agent as agent_mod
    from app.secretary import config as sec_config

    resp = agent_mod._chat(messages, sec_config.MODEL, use_tools=False,
                           max_tokens=max_tokens, think=False)
    return (resp["choices"][0]["message"].get("content") or "").strip()


def _json_of(text: str) -> dict:
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
    m = re.search(r"\{.*\}", t, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


# ── 规则层（确定性，不花模型钱） ────────────────────────────────

# 只放**高置信度**的：公文里这些写法是被明确判错的，报了不会有争议
_WORD_RULES = [
    ("其它", "其他", "公文与法律文本统一用「其他」"),
    ("做为", "作为", "「作为」是规范写法"),
    ("帐号", "账号", "财务与信息类文本统一用「账号」"),
    ("帐户", "账户", "财务类文本统一用「账户」"),
    ("帐目", "账目", "财务类文本统一用「账目」"),
    ("截止目前", "截至目前", "「截止」是不及物动词，表示到某期限停止；「截至」才是「到……为止」"),
    ("截止到", "截至", "同上"),
    ("即使是", None, None),   # 占位：保持列表结构，避免误伤
]
_ENUMERATION = [
    (re.compile(r",(?![0-9])"), "英文逗号", "应使用中文逗号「，」"),
    (re.compile(r"[;:!?]"), "英文标点", "应使用中文全角标点"),
    (re.compile(r"\.(?![0-9])"), "英文句点", "正文里应使用中文句号「。」"),
    (re.compile(r"[ \t]{2,}"), "多余空格", "删除多余空格"),
    (re.compile(r"。{2,}"), "重复句号", "保留一个句号"),
]


def _rules_words(text: str) -> list[dict]:
    out = []
    for wrong, right, reason in _WORD_RULES:
        if right is None or wrong not in text:
            continue
        out.append({"type": "错别字/用词", "quote": wrong, "suggestion": right,
                    "reason": reason, "source": "规则"})
    return out


def _rules_punct(text: str) -> list[dict]:
    out = []
    for pat, kind, advice in _ENUMERATION:
        for m in list(pat.finditer(text))[:20]:
            frag = text[max(0, m.start() - 8):m.start() + 8]
            out.append({"type": kind, "quote": frag, "suggestion": advice,
                        "reason": "中文正文里的标点规范", "source": "规则"})
    if text.count("“") != text.count("”"):
        out.append({"type": "引号不配对", "quote": "（全文）",
                    "suggestion": "补齐引号", "reason": "左引号与右引号数量不一致",
                    "source": "规则"})
    return out


def _rules_numbers(text: str) -> list[dict]:
    out = []
    if "％" in text:
        out.append({"type": "数字符号", "quote": "％", "suggestion": "%",
                    "reason": "百分号统一用半角「%」", "source": "规则"})
    has_wan = "万元" in text
    has_yuan = bool(re.search(r"(?<!万)元", text))
    if has_wan and has_yuan:
        out.append({"type": "单位混用", "quote": "（全文）",
                    "suggestion": "统一到「万元」或「元」",
                    "reason": "同一份文件里同时出现「万元」和「元」，易误读",
                    "source": "规则"})
    # 日期混用：既有「2026年11月30日」又有「十一月三十日」
    if re.search(r"[一二三四五六七八九十]{1,3}月[一二三四五六七八九十]{1,3}日", text) \
            and re.search(r"\d{4}年\d{1,2}月\d{1,2}日", text):
        out.append({"type": "数字混用", "quote": "（全文）",
                    "suggestion": "日期统一用阿拉伯数字",
                    "reason": "公文日期统一用阿拉伯数字，两者混排易出错", "source": "规则"})
    return out


def _rules_structure(blocks: list[dict]) -> list[dict]:
    """层次序号：一、/（一）/1./（1）各层不能混用；标题别带句号、别太长。"""
    out = []
    heads = [b["text"].split("\n")[0].strip() for b in blocks
             if b.get("block_type") == "heading" and b.get("text")]
    lvl1 = [h for h in heads if re.match(r"^[一二三四五六七八九十]+、", h)]
    lvl1_alt = [h for h in heads if re.match(r"^第[一二三四五六七八九十]+[章节]", h)]
    if lvl1 and lvl1_alt:
        out.append({"type": "层次序号混用", "quote": lvl1_alt[0][:30],
                    "suggestion": "统一用「一、二、三、」或「第一章」，不要混",
                    "reason": "同一份文件两种一级序号并存", "source": "规则"})
    for h in heads[:1]:
        if len(h) > 40:
            out.append({"type": "标题过长", "quote": h[:40], "suggestion": "精简标题（≤30 字更常见）",
                        "reason": f"标题 {len(h)} 字，公文标题一般不超过 30–40 字", "source": "规则"})
        if h.rstrip().endswith("。"):
            out.append({"type": "标题带句号", "quote": h[-20:], "suggestion": "删掉标题末尾的句号",
                        "reason": "公文标题一般不加句号", "source": "规则"})
    return out


# ── 模型层（病句 / 表述不清 / 前后矛盾） ────────────────────────

_PROMPT = (
    "你是公文审校。下面是一份公文的一个片段。**只找规则查不出的问题**：病句、表述不清、"
    "前后矛盾、指代不明。不要报错别字和标点（那些已经用规则查过了）。\n"
    "输出严格 JSON：{\"issues\":[{\"type\":\"病句|表述不清|前后矛盾|指代不明\","
    "\"quote\":\"原文短句（≤40字，必须原样照抄）\",\"suggestion\":\"改成什么\","
    "\"reason\":\"为什么\"}]}\n"
    "五条硬要求（线上实测踩过，务必遵守）：\n"
    "① **已经用规则查过的不要重复报**：错别字（其它/做为/帐/截止目前）、中英文标点、"
    "多余空格、数字与百分号写法、层次序号 —— 这些交给规则，你只报规则查不出的问题。\n"
    "② **suggestion 必须和原文实质不同**：如果只能改空格、标点、全半角，就不要报（那属于①）。\n"
    "③ **这是审校不是润色**：风格、语气、措辞强弱的建议不要报（例如把「纳入考核」改成"
    "「纳入绩效考核」）。只报会影响理解的错误、矛盾、歧义。\n"
    "④ 没把握就不要报；一句话找不到实质错误就不要硬凑一条。\n"
    "⑤ **不得引入原文没有的事实**：suggestion 只能是「改写 / 删多余 / 明确表述」，"
    "**不许新增原文没有的时间、责任方、办理方式**（例如原文没写「另行报送」，就不能加）。\n"
    "⑥ 报「前后矛盾」必须指出**两处互相冲突的原文**（quote 里并列给出两段），"
    "只有一句话、指不出冲突双方的，不要报成前后矛盾。\n"
    "⑦ quote 必须能在片段里原样找到；一个问题一条，不要合并。"
)


_WS = re.compile(r"[\s\u3000]+")


def _squash(s: str) -> str:
    """去掉空白、把全角标点归一、去掉句末标点 —— 用来判断"建议是否等于原文"。"""
    out = _WS.sub("", str(s or ""))
    for a, b in (("，", ","), ("。", "."), ("；", ";"), ("：", ":"), ("（", "("), ("）", ")"),
                 ("“", '"'), ("”", '"')):
        out = out.replace(a, b)
    return out.rstrip("。.,;:")


def _model_chunk(chunk: dict) -> list[dict]:
    out = _llm([{"role": "system", "content": _PROMPT},
                {"role": "user", "content": f"【位置】{chunk['section']}\n【正文】\n{chunk['text']}"}],
               max_tokens=1000)
    data = _json_of(out)
    issues = data.get("issues") or []
    res = []
    for it in issues:
        if not isinstance(it, dict) or not it.get("quote"):
            continue
        sug = (it.get("suggestion") or "").strip()
        # 过滤两类线上实测的噪声：
        # ① 没给建议（只有抱怨没有改法）；② 建议与原文只差空格/标点 —— 那属于规则层的活，
        #    模型再报一遍会让同一处问题出现两次（实测出现过）。
        if not sug or _squash(sug) == _squash(it["quote"]):
            continue
        res.append({"type": it.get("type") or "表述", "quote": it.get("quote"),
                    "suggestion": sug, "reason": it.get("reason") or "",
                    "source": "模型", "_section": chunk["section"]})
    return res


# ── 汇总 ──────────────────────────────────────────────────────

def _dedupe(issues: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    out = []
    for it in issues:
        key = (it["type"], re.sub(r"\s", "", str(it.get("quote"))[:24]))
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def _verify(issues: list[dict], blocks: list[dict]) -> list[dict]:
    for it in issues:
        q = it.get("quote") or ""
        hit = doc_chunks.locate(blocks, q) if q and len(q) >= 2 else None
        it["verified"] = bool(hit) or it.get("source") == "规则"
        if hit:
            it["section"] = hit.get("section") or it.get("_section")
            it["para_no"] = hit.get("para_no")
        else:
            it["section"] = it.get("_section")
    return issues


def run(file_id: str, user_id: int, options: dict | None = None,
        progress=None, out_dir: Path | None = None) -> dict:
    opts = options or {}
    say = progress or (lambda pct, stage: None)

    say(3, "读取文件")
    meta = doc_files.load(file_id, user_id)
    blocks = doc_files.blocks(file_id, user_id, offset=0, limit=100000)["blocks"]
    full = "\n".join(b.get("text") or "" for b in blocks)
    chunks = doc_chunks.chunk_blocks(blocks, max_chars=1200)
    warnings: list[str] = []
    if len(chunks) > MAX_CHUNKS:
        warnings.append(f"文件过长，只检查了前 {MAX_CHUNKS}/{len(chunks)} 段")
        chunks = chunks[:MAX_CHUNKS]

    say(20, "规则检查")
    issues: list[dict] = []
    issues += _rules_words(full)
    issues += _rules_punct(full)
    issues += _rules_numbers(full)
    issues += _rules_structure(blocks)

    say(35, "模型审校")
    t0 = time.time()
    for i, ch in enumerate(chunks, 1):
        say(35 + int(45 * i / max(1, len(chunks))), f"审校 {i}/{len(chunks)} 段")
        try:
            issues += _model_chunk(ch)
        except Exception as exc:                     # noqa: BLE001
            warnings.append(f"第 {i} 段模型审校失败：{type(exc).__name__}")

    issues = _verify(_dedupe(issues), blocks)
    by_type: dict[str, int] = {}
    for it in issues:
        by_type[it["type"]] = by_type.get(it["type"], 0) + 1

    md = _markdown(meta["name"], issues, by_type, warnings)

    outputs: list[Path] = []
    if out_dir is not None and opts.get("want_docx", True):
        say(90, "生成问题清单")
        rows = [[i, it["type"], (it.get("quote") or "")[:40], it.get("suggestion") or "",
                 it.get("reason") or "", it.get("section") or "—",
                 "是" if it.get("verified") else "待人工确认"]
                for i, it in enumerate(issues, 1)]
        outputs.append(docgen.write_docx(
            out_dir / f"纠错清单_{Path(meta['name']).stem}.docx",
            f"{Path(meta['name']).stem}·审校问题清单",
            [("一、审校说明",
              f"共发现 {len(issues)} 处问题：" + "；".join(f"{k} {v} 处" for k, v in by_type.items())
              + "。\n规则检查覆盖错别字、标点、数字用法与层次序号；模型审校覆盖病句、"
                "表述不清与前后矛盾。逐条对照原文修改，改完请再通读一遍。")],
            [("二、问题清单", ["序号", "类型", "原文（节选）", "建议", "依据", "位置", "已核实"], rows)],
        ))
    if out_dir is not None and opts.get("want_xlsx"):
        outputs.append(docgen.write_xlsx(
            out_dir / f"纠错清单_{Path(meta['name']).stem}.xlsx",
            [("问题清单", ["序号", "类型", "原文", "建议", "依据", "位置", "来源", "已核实"],
              [[i, it["type"], it.get("quote") or "", it.get("suggestion") or "",
                it.get("reason") or "", it.get("section") or "",
                it.get("source") or "", "是" if it.get("verified") else "待确认"]
               for i, it in enumerate(issues, 1)])],
        ))

    say(100, "完成")
    return {
        "task": TASK,
        "file_id": file_id,
        "file_name": meta["name"],
        "answer_markdown": md,
        "issues": issues,
        "stats": {"total": len(issues), "by_type": by_type,
                  "elapsed_ms": int((time.time() - t0) * 1000)},
        "warnings": warnings,
        "outputs": [p.name for p in outputs],
    }


def _markdown(name: str, issues: list[dict], by_type: dict, warnings: list[str]) -> str:
    out = [f"## 审校结果：{name}", ""]
    if not issues:
        out.append("未发现问题。")
    else:
        out.append("| # | 类型 | 原文 | 建议 | 位置 |")
        out.append("|---|---|---|---|---|")
        for i, it in enumerate(issues, 1):
            q = (it.get("quote") or "").replace("|", "｜")[:30]
            s = (it.get("suggestion") or "").replace("|", "｜")[:30]
            out.append(f"| {i} | {it['type']} | {q} | {s} | {it.get('section') or '—'} |")
        out.append("")
        out.append("**分类统计**：" + "；".join(f"{k} {v} 处" for k, v in by_type.items()))
    if warnings:
        out.append("\n> 提示：" + "；".join(warnings))
    return "\n".join(out)
