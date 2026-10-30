# -*- coding: utf-8 -*-
import sys, json, time
sys.path.insert(0, "/app")
from app.secretary import agent, agent_v2, semantic, config

TODAY = "2026-10-30"
full = agent_v2.system("data", agent._system, TODAY)
codes_block = "\n\n" + semantic.codes_menu()
tables_block = "\n\n【可用数据表（只能查这些，字典外的表一律不可用）】\n" + semantic.table_menu()
assert full.endswith(tables_block), "system 结构与预期不同，先看尾部：\n" + full[-500:]

lean_tables = "\n".join(
    "- %s ｜ %s ｜ %s" % (t["table_name"], t["table_cn_name"] or "",
                         (t["business_desc"] or "").strip()[:60])
    for t in semantic.table_list())
lean = full.replace(codes_block, "\n\n【字典值】略（需要时再查）")
lean = lean.replace(tables_block,
                    "\n\n【可用数据表（只能查这些，字典外的表一律不可用）】\n" + lean_tables)

print("[system 长度] full=%d  lean=%d  省掉 %d 字" % (len(full), len(lean), len(full) - len(lean)))

QUESTIONS = [
    "环潭供电所 2025 年的项目一共几个？",
    "环潭供电所 2025 年的项目一共几个？",
    "环潭供电所 2025 年的项目一共几个？",
    "t_power_work_order 这张表现在一共有多少行数据？",
]
USER_TAIL = ("\n\n只输出你准备执行的 SQL（可以多条），不要解释、不要写结论。")

def run(sys_text, q, model):
    msgs = [{"role": "system", "content": sys_text},
            {"role": "user", "content": q + USER_TAIL}]
    t0 = time.time()
    resp = agent._chat(msgs, model, use_tools=False, max_tokens=700, think=False)
    ms = int((time.time() - t0) * 1000)
    msg = resp["choices"][0]["message"]
    return {"ms": ms, "usage": resp.get("usage") or {}, "text": (msg.get("content") or "").strip()}

def judge(text, need_status_filter):
    t = text.lower()
    return {
        "表名正确": ("t_power_work_order" in t) if "工单" in text or "work_order" in t or True else None,
        "排除草稿": ("work_status" in t and ("<> 0" in t or "!= 0" in t or "<>0" in t or "!=0" in t)),
        "带时间条件": ("create_time" in t or "year(" in t or "2026" in text or "2025" in text),
        "字符数": len(text),
    }

model = config.MODEL
out = {"model": model, "full_chars": len(full), "lean_chars": len(lean), "runs": []}
for i, q in enumerate(QUESTIONS, 1):
    for name, txt in (("FULL", full), ("LEAN", lean)):
        r = run(txt, q, model)
        r.update({"q": q, "sys": name})
        r["judge"] = judge(r["text"], True)
        out["runs"].append(r)
        print("\n--- Q%d %s ｜ %d ms ｜ usage=%s ---" % (i, name, r["ms"], r["usage"]))
        print(r["text"][:700])

print("\n===JSON===")
print(json.dumps(out, ensure_ascii=False))
