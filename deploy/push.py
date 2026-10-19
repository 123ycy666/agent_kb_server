#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把本机打好的包推到服务器并执行部署脚本（paramiko，密码或密钥登录）。

用法：
    python deploy/push.py --tar dist/merged.tar.gz --host $DEPLOY_HOST --user root
    # 目标服务器不写死在脚本里：换机器只改 --host / 环境变量 DEPLOY_HOST
    # 密码：--password / --password-file / 环境变量 DEPLOY_PASSWORD / 交互输入

可选：
    --remote-dir /root/agentkownlg   服务器工作目录
    --script deploy/deploy.sh        要执行的服务器脚本（相对仓库根）
    --no-deploy                      只上传，不执行
"""
import argparse
import getpass
import os
import sys
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parent.parent


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--tar", default=str(ROOT / "dist" / "merged.tar.gz"))
    p.add_argument("--host", required=True)
    p.add_argument("--user", default="root")
    p.add_argument("--port", type=int, default=22)
    p.add_argument("--password", default=os.environ.get("DEPLOY_PASSWORD", ""))
    p.add_argument("--password-file", default="")
    p.add_argument("--key-file", default="")
    p.add_argument("--remote-dir", default="/root/agentkownlg")
    p.add_argument("--script", default="deploy/deploy.sh")
    p.add_argument("--no-deploy", action="store_true")
    return p.parse_args()


def get_password(a):
    if a.password:
        return a.password
    if a.password_file:
        return Path(a.password_file).read_text(encoding="utf-8").strip()
    return getpass.getpass("SSH 密码（%s@%s）：" % (a.user, a.host))


def main():
    a = parse_args()
    tar = Path(a.tar)
    if not tar.exists():
        sys.exit("✗ 找不到部署包：%s（先跑 bash deploy/pack.sh）" % tar)
    script = ROOT / a.script
    if not script.exists():
        sys.exit("✗ 找不到部署脚本：%s" % script)

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        c.connect(a.host, port=a.port, username=a.user,
                  password=None if a.key_file else get_password(a),
                  key_filename=a.key_file or None, timeout=20,
                  banner_timeout=25, auth_timeout=25,
                  look_for_keys=bool(a.key_file), allow_agent=bool(a.key_file))
    except Exception as e:  # noqa: BLE001
        sys.exit("✗ 连接失败：%s" % e)

    try:
        sftp = c.open_sftp()
        remote_tar = "%s/merged.tar.gz" % a.remote_dir
        remote_sh = "%s/deploy.sh" % a.remote_dir
        print("↑ 上传 %s (%.1f MB)" % (tar.name, tar.stat().st_size / 1048576))
        sftp.put(str(tar), remote_tar)
        sftp.put(str(script), remote_sh)
        sftp.close()
        if a.no_deploy:
            print("✓ 已上传：%s（未执行部署）" % remote_tar)
            return 0

        cmd = "cd %s && bash deploy.sh %s" % (a.remote_dir, remote_tar)
        print("▶ 远程执行：%s\n" % cmd)
        chan = c.get_transport().open_session()
        chan.exec_command(cmd)
        for raw in iter(chan.makefile("rb").readline, b""):
            print(raw.decode("utf-8", "replace").rstrip())
        code = chan.recv_exit_status()
        print("\n远程退出码：%d" % code)
        return 0 if code == 0 else 1
    finally:
        c.close()


if __name__ == "__main__":
    sys.exit(main())
