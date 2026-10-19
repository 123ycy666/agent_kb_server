"""表格审查（`table_audit`）：空值 / 重复行 / 合计不平 / 单位混写 / 文本数字 / 日期混用。

为什么这一类必须用 openpyxl 直读单元格，不走知识库那条"切片"链路：
切片出来是 `"值 | 值 | 值"` 的字符串，**行列坐标没了**。而表格审查的价值恰恰是
"第 3 张表 B7 跟合计对不上"——定位不到单元格的审查结果没人会去看。

v1 只出**问题清单**（docx + xlsx），不改用户原表：
写回"清洗后的表"风险太大（可能把别人的公式/格式搞坏），需要时人工按清单改。
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

from app.secretary import doc_files, docgen

TASK = "table_audit"
SUM_WORDS = ("合计", "小计", "总计", "累计")
_NUM_TEXT = re.compile(r"^[¥￥]?-?[\d,]+(\.\d+)?%?$")
_DATE_TEXT = re.compile(r"^\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?$")
_UNIT_TEXT = re.compile(r"[¥￥]?\d[\d,]*(\.\d+)?\s*(万|亿|元|万元|亿元)")


def _num(v):
    """能当数字就直接转，转不了返回 None（文本数字单独报，不在求和里算）。"""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip().replace(",", "").replace("¥", "").replace("￥", "")
        if _NUM_TEXT.match(v.strip()):
            try:
                return float(s.rstrip("%"))
            except ValueError:
                return None
    return None


def _expand_merged(ws) -> list[list]:
    """把合并单元格的值铺满整个区域 —— 否则合并区会被当成"一堆空值"误报。"""
    grid = [[c.value for c in row] for row in ws.iter_rows()]
    for rng in ws.merged_cells.ranges:
        top = ws.cell(row=rng.min_row, column=rng.min_col).value
        for r in range(rng.min_row, rng.max_row + 1):
            for c in range(rng.min_col, rng.max_col + 1):
                if r - 1 < len(grid) and c - 1 < len(grid[r - 1]):
                    grid[r - 1][c - 1] = top
    return grid


def _cell(col: int, row: int) -> str:
    from openpyxl.utils import get_column_letter

    return f"{get_column_letter(col)}{row}"


def _audit_sheet(name: str, grid: list[list]) -> list[dict]:
    issues: list[dict] = []
    if len(grid) < 2:
        return issues

    header = grid[0]
    ncol = len(header)

    # ① 空值（有内容的行里，中间列缺值）
    for r, row in enumerate(grid[1:], start=2):
        filled = [v for v in row if v not in (None, "")]
        if not filled:
            continue
        for c in range(ncol):
            if c < len(row) and row[c] in (None, "") and c < len(header) - 1:
                issues.append({"sheet": name, "cell": _cell(c + 1, r), "type": "空值",
                               "message": f"「{header[c] or '未命名列'}」为空，而本行其它列有内容",
                               "suggestion": "补全或标注「无」"})

    # ② 重复行
    seen: dict[str, int] = {}
    for r, row in enumerate(grid[1:], start=2):
        key = "|".join("" if v is None else str(v).strip() for v in row)
        if not key.replace("|", "").strip():
            continue
        if key in seen:
            issues.append({"sheet": name, "cell": f"A{r}", "type": "重复行",
                           "message": f"与第 {seen[key]} 行完全相同",
                           "suggestion": "确认是否重复录入"})
        else:
            seen[key] = r

    # ③ 合计校验：合计行的数字 vs 上方同列分项和
    for r, row in enumerate(grid[1:], start=2):
        label_idx = next((c for c, v in enumerate(row)
                          if isinstance(v, str) and any(w in v for w in SUM_WORDS)), None)
        if label_idx is None:
            continue
        for c, val in enumerate(row):
            total = _num(val)
            if total is None:
                continue
            parts = [_num(grid[rr - 1][c]) for rr in range(2, r)
                     if c < len(grid[rr - 1])]
            parts = [p for p in parts if p is not None]
            if not parts:
                continue
            s = sum(parts)
            if abs(s - total) > 0.01:
                issues.append({"sheet": name, "cell": _cell(c + 1, r), "type": "合计不平",
                               "message": f"分项和 {s:g} 与合计 {total:g} 相差 {s - total:g}",
                               "suggestion": "核对分项或合计数"})

    # ④ 单位混写 / 文本数字 / 日期混用（按列）
    for c in range(ncol):
        col_vals = [(row[c], r) for r, row in enumerate(grid[1:], start=2)
                    if c < len(row) and row[c] not in (None, "")]
        if not col_vals:
            continue
        has_num = any(isinstance(v, (int, float)) and not isinstance(v, bool)
                      for v, _ in col_vals)
        unit_vals = [(v, r) for v, r in col_vals
                     if isinstance(v, str) and _UNIT_TEXT.search(v)]
        if has_num and unit_vals:
            issues.append({"sheet": name, "cell": _cell(c + 1, unit_vals[0][1]),
                           "type": "单位混写",
                           "message": f"「{header[c] or '未命名列'}」里既有纯数字又有带单位的值"
                                      f"（如 {unit_vals[0][0]}）",
                           "suggestion": "统一单位（建议单独一列写单位）"})

        # 百分数文本（"50%"）不报：表里存成文本的百分比很常见，报了是噪声
        text_nums = [(v, r) for v, r in col_vals
                     if isinstance(v, str) and _NUM_TEXT.match(v.strip())
                     and not v.strip().endswith("%")]
        if text_nums:
            issues.append({"sheet": name, "cell": _cell(c + 1, text_nums[0][1]),
                           "type": "文本数字",
                           "message": f"「{header[c] or '未命名列'}」有 {len(text_nums)} 个数字被存成文本"
                                      f"（如 {text_nums[0][0]}），求和会漏",
                           "suggestion": "转成数值格式"})

        dts = [v for v, _ in col_vals if isinstance(v, (datetime, date))]
        dtext = [(v, r) for v, r in col_vals
                 if isinstance(v, str) and _DATE_TEXT.match(v.strip())]
        if dts and dtext:
            issues.append({"sheet": name, "cell": _cell(c + 1, dtext[0][1]),
                           "type": "日期格式混用",
                           "message": f"「{header[c] or '未命名列'}」既有日期格式又有文本日期"
                                      f"（如 {dtext[0][0]}）",
                           "suggestion": "统一为日期格式"})
    return issues


def run(file_id: str, user_id: int, options: dict | None = None,
        progress=None, out_dir: Path | None = None) -> dict:
    opts = options or {}
    say = progress or (lambda pct, stage: None)

    say(5, "读取表格")
    meta = doc_files.load(file_id, user_id)
    path = doc_files.orig_path(meta)
    if meta["ext"] not in (".xlsx", ".xlsm", ".xltx"):
        raise RuntimeError(f"{meta['ext']} 不支持单元格级检查，请另存为 .xlsx 后重试")

    from openpyxl import load_workbook

    wb = load_workbook(str(path), data_only=True)
    issues: list[dict] = []
    sheet_stats: list[dict] = []
    for i, ws in enumerate(wb.worksheets, start=1):
        say(10 + int(70 * i / max(1, len(wb.worksheets))), f"检查第 {i} 张表：{ws.title}")
        grid = _expand_merged(ws)
        found = _audit_sheet(ws.title, grid)
        issues += found
        sheet_stats.append({"sheet": ws.title,
                            "rows": max(0, len(grid) - 1),
                            "cols": len(grid[0]) if grid else 0,
                            "issues": len(found)})

    by_type: dict[str, int] = {}
    for it in issues:
        by_type[it["type"]] = by_type.get(it["type"], 0) + 1

    md = _markdown(meta["name"], issues, by_type, sheet_stats)

    outputs: list[Path] = []
    if out_dir is not None:
        stem = Path(meta["name"]).stem
        say(85, "生成问题清单")
        if opts.get("want_docx", True):
            rows = [[i, it["sheet"], it["cell"], it["type"], it["message"], it["suggestion"]]
                    for i, it in enumerate(issues, 1)]
            outputs.append(docgen.write_docx(
                out_dir / f"表格审查_{stem}.docx",
                f"{stem}·表格审查问题清单",
                [("一、审查说明",
                  f"共检查 {len(sheet_stats)} 张表、{sum(s['rows'] for s in sheet_stats)} 行，"
                  f"发现 {len(issues)} 处问题："
                  + ("；".join(f"{k} {v} 处" for k, v in by_type.items()) or "无")
                  + "。\n检查项：空值、重复行、合计与分项是否对得上、单位混写、"
                    "数字存成文本、日期格式混用。逐条对照单元格修改即可。")],
                [("二、问题清单", ["序号", "工作表", "单元格", "类型", "说明", "建议"], rows)],
            ))
        if opts.get("want_xlsx", True):
            outputs.append(docgen.write_xlsx(
                out_dir / f"表格审查_{stem}.xlsx",
                [("问题清单", ["序号", "工作表", "单元格", "类型", "说明", "建议"],
                  [[i, it["sheet"], it["cell"], it["type"], it["message"], it["suggestion"]]
                   for i, it in enumerate(issues, 1)]),
                 ("表统计", ["工作表", "数据行", "列数", "问题数"],
                  [[s["sheet"], s["rows"], s["cols"], s["issues"]] for s in sheet_stats])],
            ))

    say(100, "完成")
    return {
        "task": TASK,
        "file_id": file_id,
        "file_name": meta["name"],
        "answer_markdown": md,
        "issues": issues,
        "stats": {"total": len(issues), "by_type": by_type, "sheets": sheet_stats},
        "warnings": [],
        "outputs": [p.name for p in outputs],
    }


def _markdown(name: str, issues: list[dict], by_type: dict, sheets: list[dict]) -> str:
    out = [f"## 表格审查：{name}", ""]
    for s in sheets:
        out.append(f"- 工作表「{s['sheet']}」：{s['rows']} 行 × {s['cols']} 列，问题 {s['issues']} 处")
    out.append("")
    if not issues:
        out.append("未发现明显问题。")
        return "\n".join(out)
    out += ["| # | 工作表 | 单元格 | 类型 | 说明 |", "|---|---|---|---|---|"]
    for i, it in enumerate(issues, 1):
        out.append(f"| {i} | {it['sheet']} | {it['cell']} | {it['type']} | "
                   f"{it['message'].replace('|', '｜')} |")
    out.append("")
    out.append("**分类统计**：" + "；".join(f"{k} {v} 处" for k, v in by_type.items()))
    return "\n".join(out)
