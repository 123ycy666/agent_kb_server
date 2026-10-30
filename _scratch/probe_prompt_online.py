# -*- coding: utf-8 -*-
"""只读：打印线上**实际生效的 system 提示词**构成（判断装配走 V2 还是 legacy）。

在容器内执行：
  · `agent_v2.pack()` 看站点包是否加载（决定走 V2 七层还是 legacy）
  · `agent_v2.system('data', agent._system, today)` 打印真实 system 前缀与长度
  · `prompt_stack.build(...)['layers']` 打印分层（id/title/chars/source）
  · 检查 legacy 的 RULES_PRIORITY 在最终 system 里到底在不在
不写任何数据。见 docs/04-计划/2026-10-29-个人知识库未生效与运行日志排查.md
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
    local = ROOT / '_scratch/_probe_prompt_container.py'
    local.write_text(IN_CONTAINER, encoding='utf-8')

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=pwd, timeout=20,
              banner_timeout=25, auth_timeout=25, look_for_keys=False, allow_agent=False)
    try:
        sftp = c.open_sftp()
        sftp.put(str(local), '/tmp/_probe_prompt.py')
        sftp.close()
        chan = c.get_transport().open_session()
        chan.settimeout(120)
        chan.set_combine_stderr(True)
        chan.exec_command('docker cp /tmp/_probe_prompt.py kb-merged:/tmp/_probe_prompt.py && '
                          'docker exec -w /app -e PYTHONPATH=/app kb-merged python /tmp/_probe_prompt.py')
        out = b''
        while True:
            b = chan.recv(65535)
            if not b:
                break
            out += b
        code = chan.recv_exit_status()
        print(out.decode('utf-8', 'replace'))
        print('[exit]', code)
        return 0 if code == 0 else 1
    finally:
        c.close()


if __name__ == '__main__':
    sys.exit(main())
