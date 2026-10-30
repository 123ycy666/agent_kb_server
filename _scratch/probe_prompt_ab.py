# -*- coding: utf-8 -*-
"""线上 A/B：表目录"全量注入" vs "只给表名清单"，对模型表现有什么影响。

做法（只调模型，不写库、不执行 SQL）：
  1. 取线上真实 system（含 26 张表的全量目录 + 码表）
  2. 造一个"精简版"：表目录只留「表名 ｜ 中文名 ｜ 一句话」
  3. 同一批问题，两套 system 各问一次，要求模型**只输出准备执行的 SQL**
  4. 打印：usage(prompt/completion tokens)、耗时、SQL 原文
  5. correctness 由脚本按关键词自动判定（表名/排除草稿条件/时间条件）

见 docs/04-计划/2026-10-29-个人知识库未生效与运行日志排查.md
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[1]
HOST = '47.121.183.81'
USER = 'root'

IN_CONTAINER = r'''# -*- coding: utf-8 -*-
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
'''


def env(name: str) -> str:
    txt = (ROOT / '.env').read_text(encoding='utf-8')
    m = re.search(r'^%s\s*=\s*(.+)$' % re.escape(name), txt, re.M)
    return (m.group(1).strip().strip('"').strip("'") if m else '')


def main() -> int:
    pwd = env('SERVER_SSH_PASSWORD')
    if not pwd:
        print('[X] 缺少 SERVER_SSH_PASSWORD')
        return 2
    local = ROOT / '_scratch/_probe_ab_container.py'
    local.write_text(IN_CONTAINER, encoding='utf-8')

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=pwd, timeout=20,
              banner_timeout=25, auth_timeout=25, look_for_keys=False, allow_agent=False)
    try:
        sftp = c.open_sftp()
        sftp.put(str(local), '/tmp/_probe_ab.py')
        sftp.close()
        chan = c.get_transport().open_session()
        chan.settimeout(600)
        chan.set_combine_stderr(True)
        chan.exec_command('docker cp /tmp/_probe_ab.py kb-merged:/tmp/_probe_ab.py && '
                          'docker exec -w /app -e PYTHONPATH=/app kb-merged python /tmp/_probe_ab.py')
        out = b''
        while True:
            b = chan.recv(65535)
            if not b:
                break
            out += b
        code = chan.recv_exit_status()
        text = out.decode('utf-8', 'replace')
        dest = ROOT / 'output/test_results/2026-10-29-个人库与步骤日志/ab-table-catalog.txt'
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding='utf-8')
        print(text)
        print('[exit]', code, '| 已存', dest.name)
        return 0 if code == 0 else 1
    finally:
        c.close()


if __name__ == '__main__':
    sys.exit(main())
