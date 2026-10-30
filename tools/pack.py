#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""无 bash 环境下的打包：等价于 deploy/pack.sh，产出 dist/merged.tar.gz。

为什么要有它（2026-10-30）
--------------------------
部署机上只有 Windows（没有 bash/tar），而 `deploy/pack.sh` 是 bash 脚本。
这里用 Python 的 tarfile 复刻同一份文件清单与排除规则，产出可直接
`python deploy/push.py --tar dist/merged.tar.gz --host ...` 上传的包。

用法：
    py -X utf8 tools/pack.py                 # 打包到 dist/merged.tar.gz
    py -X utf8 tools/pack.py out.tar.gz      # 指定输出
"""
from __future__ import annotations

import subprocess
import sys
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDE_DIRS = {'.git', 'node_modules', '__pycache__', 'output', 'data',
                '_scratch', '.dsh', '.venv', 'venv', 'A924-POC', 'research_raw',
                '.tmp_docs', '.tmp_repos', '.tmp_skills', '.tmp_unused'}
# ⚠️ 只排掉**仓库根**的 dist，不能把 web/dist 一起排掉 ——
#    那正是镜像里 COPY web/dist/ 要用的前端产物（2026-10-30 第一次部署就栽在这）。
EXCLUDE_PREFIX = ('server/tests/output', 'server/.tmp', '.tmp_', 'docs/06-口径与数据/ER',
                  'dist/')
EXCLUDE_SUFFIX = ('.pyc', '.tar.gz', '.dump', '.zip')
EXCLUDE_NAMES = {'.env', '.env.local'}


def _skip(rel: str) -> bool:
    p = rel.replace('\\', '/')
    if not p:
        return False
    parts = p.split('/')
    if any(x in EXCLUDE_DIRS for x in parts):
        return True
    if p.startswith(EXCLUDE_PREFIX) or p in EXCLUDE_NAMES:
        return True
    if p.endswith(EXCLUDE_SUFFIX) or p.startswith('.env.bak'):
        return True
    return False


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'dist' / 'merged.tar.gz'
    out.parent.mkdir(parents=True, exist_ok=True)

    # 语法自检（和 pack.sh 第 1 步一致）
    r = subprocess.run([sys.executable, '-m', 'compileall', '-q', str(ROOT / 'server' / 'app')],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print('✗ python 语法错误，已中止\n' + (r.stdout + r.stderr)[-1500:])
        return 1
    for need in ('web/dist/index.html', 'server/app/main.py'):
        if not (ROOT / need).is_file():
            print('✗ 缺少必需文件：%s' % need)
            return 1

    # 版本号
    try:
        vc = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=ROOT,
                            capture_output=True, text=True).stdout.strip() or 'unknown'
    except Exception:                              # noqa: BLE001
        vc = 'unknown'
    (ROOT / '.deploy_version').write_text('%s %s\n' % (vc, time.strftime('%Y-%m-%d %H:%M:%S')),
                                          encoding='utf-8')

    n = 0
    with tarfile.open(out, 'w:gz') as tf:
        for path in sorted(ROOT.rglob('*')):
            rel = str(path.relative_to(ROOT)).replace('\\', '/')
            if _skip(rel) or not path.is_file():
                continue
            tf.add(path, arcname=rel)
            n += 1
    size = out.stat().st_size / 1048576
    print('✓ 打包完成：%s\n  文件 %d 个，%.1f MB，版本 %s' % (out, n, size, vc))
    print('\n下一步：python deploy/push.py --tar "%s" --host 47.121.183.81 --user root' % out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
