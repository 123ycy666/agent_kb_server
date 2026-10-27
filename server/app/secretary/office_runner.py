# -*- coding: utf-8 -*-
"""办公技能执行器：让 agent 真的能跑技能包里的脚本（而不是只读说明书）。

为什么需要它（2026-10-21）
--------------------------
技能包（`skills/office/bisheng-*`）是「SKILL.md 说明书 + scripts 小工具」。agent 现在
只会调内置的 4 个查数工具，**没有跑脚本的手**。这个文件就是那只手。

安全边界（内网也要有）
--------------------
- 独立子进程（`sys.executable`），cwd 固定为本次任务目录，只认 `in / scratch / out` 三层
- 单次超时（默认 300 秒）；超时直接杀
- `import` 白名单：只允许文档处理与标准库；`socket/requests/subprocess/ctypes/importlib` 一律拒
- 产物只从 `out/` 取；`..` 越界直接拒
- 结果（stdout/stderr/exitcode）原样返回，落到任务记录里可复现

⚠️ 残余风险（写在这里，不假装没有）：子进程**没有内核级隔离**，白名单是文本级检查，
挡不住刻意绕过。内网单机部署、脚本来源是我们自己接进来的技能包，可接受；若将来要跑
用户上传的脚本，必须换成容器/低权限账户。

自检：py -X utf8 server/tests/agent/test_office_skill.py
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parent / 'skills' / 'office'
PPTX_SCRIPTS = SKILLS_DIR / 'bisheng-pptx' / 'scripts'
DOCX_SCRIPTS = SKILLS_DIR / 'bisheng-docx' / 'scripts'
XLSX_SCRIPTS = SKILLS_DIR / 'bisheng-xlsx' / 'scripts'
DEFAULT_TIMEOUT = 300

ALLOW_IMPORT = {
    'pptx', 'docx', 'openpyxl', 'PIL', 'lxml', 'json', 'os', 'sys', 're', 'math',
    'datetime', 'pathlib', 'shutil', 'typing', 'copy', 'csv', 'io', 'collections',
    'textwrap', 'unicodedata', 'glob', 'random', 'itertools', 'functools', 'decimal',
    'statistics', 'uuid', 'hashlib', 'zipfile', 'calendar', 'string', 'enum', 'time',
    'dataclasses', 'warnings', 'tracemalloc', 'pptx_helpers', 'docx_helpers',
    'style_profiles', 'xlsx_helpers',
}
DENY_IMPORT = {'socket', 'requests', 'urllib', 'http', 'ftplib', 'telnetlib', 'smtplib',
               'subprocess', 'ctypes', 'importlib', 'multiprocessing', 'pty', 'shlex',
               'pickle', 'shelve', 'sqlite3', 'winreg', 'msvcrt'}

_IMPORT_RE = re.compile(r'^\s*(?:from\s+([A-Za-z_][\w.]*)|import\s+([^\n#]+))', re.M)


class RunnerError(RuntimeError):
    pass


def new_job_dir(base) -> Path:
    """建任务目录：in/（用户原件） scratch/（中间件） out/（唯一交付区）。"""
    root = Path(str(base))
    for sub in ('in', 'scratch', 'out'):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def _imports(code: str) -> set[str]:
    roots: set[str] = set()
    for m in _IMPORT_RE.finditer(code):
        if m.group(1):
            roots.add(m.group(1).split('.')[0])
        else:
            for part in m.group(2).split(','):
                name = part.strip().split(' as ')[0].strip()
                if name and name != '*':
                    roots.add(name.split('.')[0])
    return roots


def check_code(code: str) -> None:
    """文本级检查：禁用的 import、越界路径。挡不住刻意绕过（见文件头风险说明）。"""
    if not (code or '').strip():
        raise RunnerError('脚本是空的')
    roots = _imports(code)
    bad = roots & DENY_IMPORT
    if bad:
        raise RunnerError('禁止的模块：%s' % ', '.join(sorted(bad)))
    unknown = {r for r in roots if r and r not in ALLOW_IMPORT and not r.startswith('_')}
    if unknown:
        raise RunnerError('未登记的模块：%s（要加白名单请改 ALLOW_IMPORT）'
                          % ', '.join(sorted(unknown)))
    if '..' in code and re.search(r"['\"][^'\"]*\.\.[/\\]", code):
        raise RunnerError('脚本里有越界路径（..）')


def _env() -> dict:
    env = dict(os.environ)
    env['PYTHONPATH'] = os.pathsep.join(
        [str(PPTX_SCRIPTS), str(DOCX_SCRIPTS), str(XLSX_SCRIPTS), env.get('PYTHONPATH', '')])
    env['PYTHONIOENCODING'] = 'utf-8'
    return env


def _exec(argv: list[str], workdir: Path, timeout: int) -> dict:
    t0 = time.time()
    try:
        cp = subprocess.run(argv, cwd=str(workdir), env=_env(), timeout=timeout,
                            capture_output=True, text=True, encoding='utf-8', errors='replace')
        code, out, err = cp.returncode, cp.stdout or '', cp.stderr or ''
    except subprocess.TimeoutExpired:
        return {'ok': False, 'exit': -1, 'stdout': '', 'stderr': '执行超时（%d 秒）' % timeout,
                'ms': int((time.time() - t0) * 1000), 'error': 'TIMEOUT'}
    return {'ok': code == 0, 'exit': code, 'stdout': out, 'stderr': err,
            'ms': int((time.time() - t0) * 1000), 'error': None if code == 0 else 'EXIT_%d' % code}


def run_code(code: str, workdir, *, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """跑一段 python（模型写的构建脚本走这里）。返回 {ok,exit,stdout,stderr,ms,error}。"""
    check_code(code)
    wd = Path(str(workdir))
    script = wd / 'scratch' / '_run.py'
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(code, encoding='utf-8')
    return _exec([sys.executable, '-X', 'utf8', str(script)], wd, timeout)


def run_script(script, args: list[str] | None = None, workdir=None,
               *, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """跑技能包自带的脚本（如 inspect_deck.py）。脚本必须在本项目的 skills/office 下。"""
    p = Path(str(script)).resolve()
    try:
        p.relative_to(SKILLS_DIR.resolve())
    except ValueError:
        raise RunnerError('只允许跑 skills/office 下的脚本：%s' % p.name)
    if not p.exists():
        raise RunnerError('脚本不存在：%s' % p.name)
    wd = Path(str(workdir))
    return _exec([sys.executable, '-X', 'utf8', str(p)] + [str(a) for a in (args or [])], wd, timeout)


def outputs(workdir) -> list[dict]:
    """交付物只从 out/ 取。"""
    out = Path(str(workdir)) / 'out'
    if not out.is_dir():
        return []
    return [{'name': f.name, 'bytes': f.stat().st_size}
            for f in sorted(out.iterdir()) if f.is_file()]
