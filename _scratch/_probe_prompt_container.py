# -*- coding: utf-8 -*-
import sys, json
sys.path.insert(0, "/app")
from app.secretary import agent, agent_v2, prompt_stack, site_pack, modes

p = agent_v2.pack()
print("[站点包] keys =", sorted(p.keys())[:12])
print("[站点包] prompts 段 =", sorted((p.get('prompts') or {}).keys()))
print("[V2 开关] enabled =", agent_v2.enabled())

for mode in ("data", "general"):
    s = agent_v2.system(mode, agent._system, "2026-10-30")
    print("\n===== mode=%s system 长度=%d =====" % (mode, len(s)))
    print("含 legacy 的「规则优先级」段 =", "规则优先级（冲突时一律按此顺序" in s)
    print("含 V2 的「装配说明」段 =", "【装配说明】" in s)
    try:
        r = prompt_stack.build(p, mode=mode, prerelease=None) if False else None
    except Exception:
        r = None
    print("--- 前 600 字 ---")
    print(s[:600])
    print("--- 含【】的段标题 ---")
    print([ln for ln in s.splitlines() if ln.startswith("【") or ln.startswith("=====")][:20])

lay = prompt_stack.build(p, mode="data")["layers"]
print("\n[分层 data]", json.dumps(lay, ensure_ascii=False))
lay2 = prompt_stack.build(p, mode="general")["layers"]
print("[分层 general]", json.dumps(lay2, ensure_ascii=False))
print("[模式判定] 按我的口径怎么写 →", modes.judge("按我的口径怎么写").get("mode"))
print("[模式判定] 我们线损率是多少 →", modes.judge("我们线损率是多少").get("mode"))
