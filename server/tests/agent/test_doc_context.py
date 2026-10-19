"""问答带文件（doc_context + /api/ask 两种请求形态）的回归。

不连模型：把 `agent.ask` 换成一个只记录入参的假函数，验证"文件真的进到了
模型上下文的 kwargs 里、返回体里带 files"。真实模型效果另由 e2e 脚本验。

跑法（仓库根）：
    py -X utf8 server/tests/agent/test_doc_context.py
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[3]
TMP = ROOT / ".dsh" / "tmp" / "doc_ctx_test"
if TMP.exists():
    shutil.rmtree(TMP, ignore_errors=True)
TMP.mkdir(parents=True, exist_ok=True)

os.environ["DOC_FILES_DIR"] = str(TMP)
sys.path.insert(0, str(ROOT / "server"))

from app.secretary import doc_context                     # noqa: E402
from app.secretary import doc_files                       # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(f"{name} {detail}".strip())
    print(("  PASS " if cond else "  FAIL ") + name + (f" | {detail}" if detail else ""))


def make_long_docx(path: Path) -> None:
    """造一份"多章节"的公文：总字数上万（要真的超过 FULL_CAP 才会触发节选）。"""
    import docx

    d = docx.Document()
    d.add_heading("关于开展2026年配网缺陷治理工作的通知", level=1)
    d.add_paragraph("公司各单位：为落实上级要求，现将有关事项通知如下。")

    filler = ("各单位要结合实际制定实施方案，明确责任分工，细化工作措施，"
              "建立台账管理机制，做到闭环管控，确保按期完成各项目标任务。")

    d.add_heading("一、总体要求", level=2)
    for _ in range(60):
        d.add_paragraph(filler)

    d.add_heading("二、时间安排", level=2)
    d.add_paragraph("请于2026年10月31日前将治理台账报送运检部，逾期未报的将纳入考核。")
    for _ in range(40):
        d.add_paragraph(filler)

    d.add_heading("三、责任分工", level=2)
    d.add_paragraph("运检部负责统筹协调，各供电所负责具体实施，财务部负责资金保障。")
    for _ in range(40):
        d.add_paragraph(filler)
    d.save(str(path))


def main() -> int:
    long_doc = TMP / "长文件.docx"
    make_long_docx(long_doc)
    f_long = doc_files.save(101, "长文件.docx", long_doc.read_bytes())

    short = TMP / "短信.docx"
    import docx
    d = docx.Document()
    d.add_heading("会议通知", level=1)
    d.add_paragraph("定于10月20日9时在三楼会议室召开预算执行分析会，请各单位准时参加。")
    d.save(str(short))
    f_short = doc_files.save(101, "短信.docx", short.read_bytes())

    print("[1] 长文件：按问题召回")
    ctx = doc_context.build("上报时限是什么", [f_long["file_id"]], 101)
    info = ctx["files"][0]
    check("有上下文", len(ctx["context"]) > 200, f"chars={len(ctx['context'])}")
    check("命中时限那一节", "2026年10月31日前" in ctx["context"])
    check("标记为节选", info["truncated"] is True, info.get("mode", ""))
    check("预算内", len(ctx["context"]) <= doc_context.TOTAL_BUDGET + 800,
          f"used={len(ctx['context'])}")
    check("给了目录", "该文件目录" in ctx["context"])
    check("cited_sections 非空", bool(info["cited_sections"]), str(info["cited_sections"])[:60])

    print("[2] 短文件：整篇直给")
    ctx2 = doc_context.build("会议几点开", [f_short["file_id"]], 101)
    check("短文件不节选", ctx2["files"][0]["truncated"] is False)
    check("短文件内容在", "三楼会议室" in ctx2["context"])

    print("[3] 别人的文件：不给看")
    ctx3 = doc_context.build("时限", [f_long["file_id"]], 999)
    check("越权文件不进上下文", ctx3["context"] == "" and bool(ctx3["warnings"]),
          str(ctx3["warnings"])[:60])

    print("[4] /api/ask 两种形态（假模型，只验接线）")
    from fastapi.testclient import TestClient

    from app.core.deps import CurrentActor, get_current_actor
    from app.main import app
    from app.secretary import agent as agent_mod

    captured: dict = {}

    def fake_ask(question, **kwargs):
        captured.clear()
        captured.update({"question": question, **kwargs})
        return {"answer": "（假模型）10月31日前上报", "session_id": kwargs.get("session_id"),
                "trace": [], "files": kwargs.get("doc_files") or []}

    # 本机沙箱里 PG 起不来（pg_ctl 创建受限令牌被拦、postgres.exe 也不监听），
    # 所以把「按登录用户解析知识库范围」这一步也打桩 —— 它只是查库，与本轮要验的
    # 「文件有没有进上下文」无关。真库环境的验证走 启动全部.bat + 现有回归。
    import app.api.v1.agent as agent_api

    orig = agent_mod.ask
    orig_scope = agent_api._resolve_kb_scope
    agent_mod.ask = fake_ask
    agent_api._resolve_kb_scope = lambda ids: ([], False)
    app.dependency_overrides[get_current_actor] = lambda: CurrentActor(
        user_id=101, username="zhangsan", display_name="张三")
    client = TestClient(app)
    try:
        # ① multipart：文件 + 问题一起发
        with open(long_doc, "rb") as fh:
            r = client.post("/api/ask", data={"question": "上报时限是什么"},
                            files={"files": ("长文件.docx", fh.read())})
        check("multipart 200", r.status_code == 200, str(r.status_code))
        body = r.json()
        check("模型拿到了文档上下文", bool(captured.get("doc_context")))
        check("上下文含时限原句", "2026年10月31日前" in (captured.get("doc_context") or ""))
        check("响应带 files", bool(body.get("files")), str(body.get("files"))[:80])
        check("假答案透传", "10月31日前" in (body.get("answer") or ""))

        # ② JSON + file_ids：对话中追问同一份文件
        r2 = client.post("/api/ask", json={"question": "责任分工怎么定的",
                                           "file_ids": [f_long["file_id"]]})
        check("file_ids 200", r2.status_code == 200, str(r2.status_code))
        check("追问也带上下文", bool(captured.get("doc_context")))
        check("file_ids 生效", captured.get("doc_files") and
              captured["doc_files"][0]["file_id"] == f_long["file_id"])

        # ③ 不带文件：行为与现状一致（doc_context 为空）
        r3 = client.post("/api/ask", json={"question": "今年预算执行怎么样"})
        check("纯 JSON 不受影响", r3.status_code == 200 and not captured.get("doc_context"))
    finally:
        agent_mod.ask = orig
        agent_api._resolve_kb_scope = orig_scope
        app.dependency_overrides.clear()

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
