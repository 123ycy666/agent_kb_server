# -*- coding: utf-8 -*-
"""线上抓一次 /api/ask 的**完整响应 JSON**（Fred 要"完整返回内容"）。

做法：SSH 到服务器 → 在容器内用 `create_access_token` 给自己签一个 JWT（sub=用户 id）
→ POST 127.0.0.1:8200/api/ask → 把响应原样落盘。

副作用（明说）：会在线上写一条问答记录（会话/消息/轨迹/步骤）+ 消耗一次模型调用。
见 docs/04-计划/2026-10-29-个人知识库未生效与运行日志排查.md
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[1]
HOST = '47.121.183.81'
USER = 'root'
OUT = ROOT / 'output/test_results/2026-10-29-个人库与步骤日志/ask-response-full.json'
QUESTION = '环谭供电所上个季度的工单有多少单'

IN_CONTAINER = '''# -*- coding: utf-8 -*-
import json, sys, urllib.request
from app.core.security import create_access_token

tok = create_access_token(1)          # sub=1 = admin
payload = {
    "question": %r,
    "user_name": "Fred",
    "channel": "probe",
    "history_turns": 0,
}
req = urllib.request.Request(
    "http://127.0.0.1:8200/api/ask",
    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok},
)
with urllib.request.urlopen(req, timeout=300) as r:
    sys.stdout.write(r.read().decode("utf-8"))
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
    local = ROOT / '_scratch/_probe_ask_container.py'
    local.write_text(IN_CONTAINER % QUESTION, encoding='utf-8')

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=pwd, timeout=20,
              banner_timeout=25, auth_timeout=25, look_for_keys=False, allow_agent=False)
    try:
        sftp = c.open_sftp()
        sftp.put(str(local), '/tmp/_probe_ask.py')
        sftp.close()
        cmd = ('docker cp /tmp/_probe_ask.py kb-merged:/tmp/_probe_ask.py && '
               'docker exec -w /app -e PYTHONPATH=/app kb-merged python /tmp/_probe_ask.py')
        chan = c.get_transport().open_session()
        chan.settimeout(320)
        chan.set_combine_stderr(True)
        chan.exec_command(cmd)
        out = b''
        while True:
            chunk = chan.recv(65535)
            if not chunk:
                break
            out += chunk
        code = chan.recv_exit_status()
        text = out.decode('utf-8', 'replace')
        if code != 0 or not text.strip().startswith('{'):
            print('[X] 失败 exit=%s，前 800 字：\n%s' % (code, text[:800]))
            return 1
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding='utf-8')
        d = json.loads(text)
        print('[OK] 已保存 %s（%d 字节）' % (OUT.name, len(text.encode('utf-8'))))
        print('[字段清单 %d 个] %s' % (len(d), ', '.join(sorted(d))))
        return 0
    finally:
        c.close()


if __name__ == '__main__':
    sys.exit(main())
