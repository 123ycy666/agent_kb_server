# -*- coding: utf-8 -*-
"""只读验证：线上「按 user_id 并入个人库」这条 id 链到底成不成立。

做的事：SSH 到服务器 → 把一段小脚本丢进容器跑 → 打印
`with_personal([1], 1)` / `with_personal([1], 6)` / `stats(1)` / `stats(6)` / `list_docs(1)`。
全部是 SELECT（ensure() 命中已存在的库就直接返回，不会建库）。
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

IN_CONTAINER = '''# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, "/app")
from app.secretary import personal_kb as K

print("with_personal([1], user_id=1) =", K.with_personal([1], 1))
print("with_personal([1], user_id=6) =", K.with_personal([1], 6))
print("stats(user=1) =", K.stats(1))
print("stats(user=6) =", K.stats(6))
print("list_docs(user=1) =", K.list_docs(1))
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
    local = ROOT / '_scratch/_probe_pkb_container.py'
    local.write_text(IN_CONTAINER, encoding='utf-8')

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=pwd, timeout=20,
              banner_timeout=25, auth_timeout=25, look_for_keys=False, allow_agent=False)
    try:
        sftp = c.open_sftp()
        sftp.put(str(local), '/tmp/_probe_pkb.py')
        sftp.close()
        chan = c.get_transport().open_session()
        chan.settimeout(120)
        chan.set_combine_stderr(True)
        chan.exec_command('docker cp /tmp/_probe_pkb.py kb-merged:/tmp/_probe_pkb.py && '
                          'docker exec -w /app -e PYTHONPATH=/app kb-merged python /tmp/_probe_pkb.py')
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
