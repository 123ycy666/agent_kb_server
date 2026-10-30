# -*- coding: utf-8 -*-
"""远程执行运维/只读命令（paramiko，密码从 .env 读，不打印不落盘）。

用法：
    py -X utf8 _scratch/remote.py "tail -60 /root/agentkownlg/deploy.log"
    py -X utf8 _scratch/remote.py --sql "SELECT count(1) FROM document"     # 走 PG 容器
    py -X utf8 _scratch/remote.py --exec "python /tmp/x.py"                 # 走 app 容器
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[1]
HOST = '47.121.183.81'
USER = 'root'


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    mode = 'shell'
    if args[0] == '--sql':
        mode = 'sql'
        args = args[1:]
    elif args[0] == '--exec':
        mode = 'exec'
        args = args[1:]
    elif args[0] == '--upload':
        mode = 'upload'
        args = args[1:]
    elif args[0] == '--put-into':
        mode = 'putinto'
        args = args[1:]
    payload = ' '.join(args)
    txt = (ROOT / '.env').read_text(encoding='utf-8')
    pwd = re.search(r'^SERVER_SSH_PASSWORD\s*=\s*(.+)$', txt, re.M).group(1).strip()
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=pwd, timeout=20, look_for_keys=False,
              allow_agent=False)
    try:
        if mode == 'upload':
            local, remote_path = args[0], args[1]
            sftp = c.open_sftp()
            sftp.put(local, remote_path)
            sftp.close()
            print('已上传 %s → %s' % (local, remote_path))
            return 0
        if mode == 'putinto':
            local, remote_path = args[0], args[1]
            sftp = c.open_sftp()
            sftp.put(local, remote_path)
            sftp.close()
            chan = c.get_transport().open_session()
            chan.set_combine_stderr(True)
            chan.exec_command('docker cp %s kb-merged:%s' % (remote_path, remote_path))
            out = b''
            while True:
                b = chan.recv(65535)
                if not b:
                    break
                out += b
            print(out.decode('utf-8', 'replace'))
            print('已放进容器：%s' % remote_path)
            return chan.recv_exit_status()
        chan = c.get_transport().open_session()
        chan.settimeout(900)
        chan.set_combine_stderr(True)
        if mode == 'sql':
            chan.exec_command('docker exec -i kb-postgres psql -U kb -d ad_new -tA -f -')
            chan.sendall((payload + '\n').encode('utf-8'))
            chan.shutdown_write()
        elif mode == 'exec':
            chan.exec_command('docker exec -w /app -e PYTHONPATH=/app kb-merged ' + payload)
        else:
            chan.exec_command(payload)
        out = b''
        while True:
            b = chan.recv(65535)
            if not b:
                break
            out += b
        print(out.decode('utf-8', 'replace'))
        return chan.recv_exit_status()
    finally:
        c.close()


if __name__ == '__main__':
    sys.exit(main())
