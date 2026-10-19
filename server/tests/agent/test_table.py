"""表格审查回归（`table_audit`）：每类问题都要能定位到 sheet + 单元格。

跑法（仓库根）：
    py -X utf8 server/tests/agent/test_table.py

造一张"故意有问题"的表：合计与分项差 10、重复行、空值、万元与纯数字混写、
把 1,234 存成文本、日期格式混用，外加一张干净的表（不该报错）。
"""

from __future__ import annotations

import os
import shutil
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[3]
TMP = ROOT / ".dsh" / "tmp" / "table_test"
if TMP.exists():
    shutil.rmtree(TMP, ignore_errors=True)
TMP.mkdir(parents=True, exist_ok=True)
os.environ["DOC_FILES_DIR"] = str(TMP / "files")
sys.path.insert(0, str(ROOT / "server"))

from app.secretary import doc_files, pipeline_table        # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(f"{name} {detail}".strip())
    print(("  PASS " if cond else "  FAIL ") + name + (f" | {detail}" if detail else ""))


def make_xlsx(path: Path) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "预算执行"
    ws.append(["供电所", "预算(万元)", "已执行", "执行率", "上报日期"])
    ws.append(["城东", 120, 60, "50%", date(2026, 9, 30)])
    ws.append(["城东", 120, 60, "50%", date(2026, 9, 30)])     # 重复行
    ws.append(["城西", None, 40, "40%", "2026/10/1"])          # 空值 + 日期文本
    ws.append(["城南", "50万元", 25, "50%", date(2026, 10, 1)])  # 单位混写
    ws.append(["城北", "1,234", 30, "30%", date(2026, 10, 1)])   # 文本数字
    ws.append(["合计", 300, 155, "", ""])                       # 合计：应为 120+120+40+25+30=? 故意写 300

    clean = wb.create_sheet("干净表")
    clean.append(["项目", "金额"])
    clean.append(["甲", 10])
    clean.append(["乙", 20])
    clean.append(["合计", 30])
    wb.save(str(path))


def find(issues, typ: str):
    return [it for it in issues if it["type"] == typ]


def main() -> int:
    src = TMP / "预算表.xlsx"
    make_xlsx(src)
    f = doc_files.save(31, "预算表.xlsx", src.read_bytes())

    res = pipeline_table.run(f["file_id"], 31, {}, lambda p, s: None, TMP / "out")
    issues = res["issues"]

    print("[1] 定位到单元格")
    dup = find(issues, "重复行")
    check("查出重复行", bool(dup), str([i["cell"] for i in dup]))
    check("重复行指向 A3", any(i["cell"] == "A3" for i in dup),
          str([(i["sheet"], i["cell"]) for i in dup]))
    empt = find(issues, "空值")
    check("查出空值", bool(empt), str([i["cell"] for i in empt]))
    check("空值定位到 B4", any(i["cell"] == "B4" for i in empt), str([i["cell"] for i in empt]))

    print("[2] 数值与合计")
    bad = find(issues, "合计不平")
    check("查出合计不平", bool(bad), str([i["message"] for i in bad]))
    check("差额说清楚", any("相差" in i["message"] for i in bad),
          str([i["message"] for i in bad])[:80])
    unit = find(issues, "单位混写")
    check("查出单位混写", any(i["sheet"] == "预算执行" for i in unit), str([i["cell"] for i in unit]))
    txt = find(issues, "文本数字")
    check("查出文本数字", bool(txt), str([i["message"] for i in txt])[:80])
    dt = find(issues, "日期格式混用")
    check("查出日期格式混用", bool(dt), str([i["cell"] for i in dt]))

    print("[3] 干净表不误报")
    clean_issues = [i for i in issues if i["sheet"] == "干净表"]
    check("干净表 0 问题", not clean_issues, str(clean_issues))
    check("表统计有两张", len(res["stats"]["sheets"]) == 2,
          str([s["sheet"] for s in res["stats"]["sheets"]]))
    # 存成文本的百分比（"50%"）不该报成"文本数字"——那是表里很常见的写法，报了是噪声
    check("百分数文本不报文本数字",
          all("执行率" not in i["message"] for i in find(issues, "文本数字")),
          str([i["message"] for i in find(issues, "文本数字")]))

    print("[4] 产出")
    check("两个成果文件", len(res["outputs"]) == 2, str(res["outputs"]))
    docx_path = TMP / "out" / res["outputs"][0]
    xlsx_path = TMP / "out" / res["outputs"][1]
    check("docx 非空", docx_path.exists() and docx_path.stat().st_size > 5000,
          f"{docx_path.stat().st_size if docx_path.exists() else 0}B")
    import docx as _docx
    d = _docx.Document(str(docx_path))
    check("docx 有清单表", len(d.tables) >= 1)
    if d.tables:
        head = [c.text for c in d.tables[0].rows[0].cells]
        check("表头含单元格列", "单元格" in head, str(head))
    from openpyxl import load_workbook
    wb = load_workbook(str(xlsx_path))
    check("xlsx 有 问题清单 + 表统计",
          [ws.title for ws in wb.worksheets] == ["问题清单", "表统计"],
          str([ws.title for ws in wb.worksheets]))

    print("[5] 非 xlsx 给出人话")
    import docx as _d
    dd = _d.Document()
    dd.add_paragraph("这是一份说明。")
    dd.save(str(TMP / "说明.docx"))
    bad_f = doc_files.save(31, "说明.docx", (TMP / "说明.docx").read_bytes())
    try:
        pipeline_table.run(bad_f["file_id"], 31, {}, lambda p, s: None, None)
        check("非表格文件被拒", False)
    except Exception as exc:                     # noqa: BLE001
        check("非表格文件被拒", "另存为 .xlsx" in str(exc) or "不支持" in str(exc), str(exc)[:60])

    print()
    print(f"结果：{len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        print("失败项：")
        for x in FAIL:
            print("  -", x)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
