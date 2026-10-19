#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""零依赖部署体检（standalone）——只需把这一个文件传到服务器。

为什么单独写一个：
    scripts/preflight_check.py 需要项目代码 + pip 依赖（sqlalchemy/psycopg/pymysql/httpx），
    裸机上根本 import 不了。本脚本只用 Python 3 标准库（3.6+ 可跑），
    适合"项目还没上传，先判断这台机器/网络能不能撑起部署"。

用法：
    python3 preflight_standalone.py                          # 只做本机体检
    python3 preflight_standalone.py --env /tmp/.env.production   # 连数据库/模型一起验
    python3 preflight_standalone.py --env .env --skip-llm        # 不实际调用模型（省钱/排网络）

退出码：0 = 无 FAIL；1 = 有 FAIL。
"""
import argparse
import json
import os
import platform
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request

FAILS = WARNS = TOTAL = 0


def add(level, title, detail=''):
    global FAILS, WARNS, TOTAL
    TOTAL += 1
    if level == 'FAIL':
        FAILS += 1
    elif level == 'WARN':
        WARNS += 1
    icon = {'OK': '✓', 'WARN': '!', 'FAIL': '✗'}[level]
    print('  [%s] %-30s %s' % (icon, title, detail))


def section(name):
    print('\n' + '-' * 70)
    print(name)
    print('-' * 70)


def load_env(path):
    """极简 .env 解析：KEY=VALUE，忽略注释与空行，去掉成对引号。"""
    data = {}
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in '\'"':
                    v = v[1:-1]
                data[k.strip()] = v
    except Exception as e:
        add('FAIL', '.env 读取失败', '%s (%s)' % (path, e))
        return {}
    add('OK', '.env 已加载', '%s（%d 项）' % (path, len(data)))
    return data


def tcp_check(host, port, timeout=6):
    """只做 TCP 握手：能连上说明安全组/白名单/服务都在位，与密码无关。"""
    t0 = time.time()
    try:
        s = socket.create_connection((host, int(port)), timeout=timeout)
        s.close()
        return True, (time.time() - t0) * 1000, ''
    except Exception as e:
        return False, 0, '%s: %s' % (type(e).__name__, e)


def http_post(url, payload, key, timeout=25):
    body = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=body, method='POST')
    req.add_header('Content-Type', 'application/json')
    req.add_header('Authorization', 'Bearer %s' % (key or 'x'))
    ctx = ssl.create_default_context()
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            raw = r.read().decode('utf-8', 'replace')
            return r.status, raw, (time.time() - t0) * 1000
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'replace')[:300], (time.time() - t0) * 1000
    except Exception as e:
        return None, '%s: %s' % (type(e).__name__, e), (time.time() - t0) * 1000


def host_port_of(url):
    from urllib.parse import urlparse
    p = urlparse(url if '://' in url else 'http://' + url)
    return p.hostname, p.port or (443 if p.scheme == 'https' else 80), p.scheme


def emb_endpoint(url, channel):
    """把配置里的 base 补成真实端点。线上常见坑：配的是 .../v1，必须再拼 /embeddings。"""
    u = (url or '').rstrip('/')
    if not u:
        return ''
    if u.endswith('/embeddings') or u.endswith('/embed'):
        return u
    return u + ('/embed' if channel == 'local' else '/embeddings')


# ══════════════════════════════════════════════════════════════
# 0. 参数与配置（有 --env 才验数据库/模型）
# ══════════════════════════════════════════════════════════════
ap = argparse.ArgumentParser(description='零依赖部署体检（只传这一个文件即可）')
ap.add_argument('--env', default='', help='.env 文件路径；提供后才检查数据库与模型')
ap.add_argument('--skip-llm', action='store_true', help='不实际调用模型接口（只测网络端口）')
ARGS = ap.parse_args()

ENV = {}
if ARGS.env:
    section('0/5  配置文件')
    ENV = load_env(ARGS.env)

# ══════════════════════════════════════════════════════════════
# 1. 本机基础
# ══════════════════════════════════════════════════════════════
section('1/5  本机基础（CPU / 内存 / 磁盘 / 时区）')

add('OK', '操作系统', '%s %s (%s)' % (platform.system(), platform.release(), platform.machine()))
add('OK', 'Python', platform.python_version() + ('（3.10+ 更稳）' if sys.version_info < (3, 10) else ''))

try:
    ncpu = os.cpu_count() or 0
    add('OK' if ncpu >= 2 else 'WARN', 'CPU 核数', '%d 核%s' % (ncpu, '' if ncpu >= 2 else '（单核跑 embedding 会很慢）'))
except Exception:
    pass

try:
    mem_gb = 0.0
    with open('/proc/meminfo') as f:
        for ln in f:
            if ln.startswith('MemTotal:'):
                mem_gb = int(ln.split()[1]) / 1024.0 / 1024.0
                break
    if mem_gb >= 4:
        add('OK', '内存', '%.1f GB' % mem_gb)
    elif mem_gb >= 2:
        add('WARN', '内存', '%.1f GB（后端 768MB 上限 + PG 256MB，2G 偏紧）' % mem_gb)
    else:
        add('FAIL', '内存', '%.1f GB，不足以跑 PG + 后端' % mem_gb)
except Exception as e:
    add('WARN', '内存', '读取失败: %s' % e)

try:
    du = shutil.disk_usage('/')
    free = du.free / 1024 ** 3
    add('OK' if free >= 5 else ('WARN' if free >= 1 else 'FAIL'), '根分区剩余', '%.1f GB / 共 %.1f GB' % (free, du.total / 1024 ** 3))
except Exception as e:
    add('WARN', '磁盘', '读取失败: %s' % e)

try:
    tz = time.strftime('%Z %z')
    add('OK' if ('+0800' in tz or 'CST' in tz) else 'WARN', '时区',
        '%s%s' % (tz, '' if ('+0800' in tz or 'CST' in tz) else '（建议 Asia/Shanghai，否则审计时间错位）'))
except Exception:
    pass

# 时钟同步：证书校验、JWT 过期判断都依赖准确时间
synced = None
for svc in ('chronyd', 'ntpd', 'systemd-timesyncd'):
    try:
        out = subprocess.run(['systemctl', 'is-active', svc], capture_output=True, text=True, timeout=3)
        if out.stdout.strip() == 'active':
            synced = svc
            break
    except Exception:
        pass
add('OK' if synced else 'WARN', '时间同步', synced or '未发现 chronyd/ntpd/timesyncd（时间漂移会导致 token 异常）')

# 文件句柄：单进程 + 大量上传时会撞这个上限
try:
    import resource
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    add('OK' if soft >= 4096 else 'WARN', 'ulimit -n', '%d / %d' % (soft, hard))
except Exception:
    pass

# ══════════════════════════════════════════════════════════════
# 2. 部署前置工具
# ══════════════════════════════════════════════════════════════
section('2/5  部署前置工具（pip / docker / 端口占用）')

for name, cmd in (('pip3', ['python3', '-m', 'pip', '--version']),
                  ('docker', ['docker', '--version']),
                  ('docker compose', ['docker', 'compose', 'version']),
                  ('nginx', ['nginx', '-v'])):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=6)
        txt = (out.stdout or out.stderr).strip().splitlines()
        first = txt[0] if txt else ''
        add('OK' if out.returncode == 0 else 'WARN', name, first[:60] if first else ('已安装' if out.returncode == 0 else '未安装或不可用'))
    except Exception:
        add('WARN', name, '未安装（若走 docker 部署可忽略部分）')

try:
    import resource
    _ = resource
    is_root = (os.geteuid() == 0) if hasattr(os, 'geteuid') else False
    add('WARN' if is_root else 'OK', '运行身份', 'root（建议专用账号跑服务）' if is_root else '非 root（推荐）')
except Exception:
    pass


def port_busy(port):
    s = socket.socket()
    s.settimeout(1)
    try:
        return s.connect_ex(('127.0.0.1', port)) == 0
    finally:
        s.close()


for p, desc in ((8200, '后端'), (80, 'HTTP'), (443, 'HTTPS')):
    busy = port_busy(p)
    if p == 8200:
        add('WARN' if busy else 'OK', '端口 %d' % p,
            ('已被占用 → 可能是老系统，先停掉再部署' if busy else '空闲，%s可用' % desc))
    else:
        add('OK' if busy else 'WARN', '端口 %d' % p,
            ('已有服务监听（nginx 等）' if busy else '无监听（要 nginx 对外才需要）'))

# ══════════════════════════════════════════════════════════════
# 3. 数据库连通（只做 TCP：先排除网络/安全组问题，再谈密码）
# ══════════════════════════════════════════════════════════════
section('3/5  数据库连通性（TCP 握手）')

if not ENV:
    add('WARN', '已跳过', '未提供 --env，数据库/模型检查跳过')
else:
    pg_url = ENV.get('DATABASE_URL', '')
    if not pg_url:
        add('WARN', 'PostgreSQL', 'DATABASE_URL 未配置')
    else:
        from urllib.parse import urlparse
        u = urlparse(pg_url)
        ok, ms, err = tcp_check(u.hostname or '127.0.0.1', u.port or 5432)
        add('OK' if ok else 'FAIL', 'PostgreSQL',
            ('%s:%s 可连（%.0fms）' % (u.hostname, u.port or 5432, ms)) if ok
            else '%s:%s 连不上 → %s' % (u.hostname, u.port or 5432, err))

    mhost = ENV.get('MYSQL_HOST', '')
    mport = ENV.get('MYSQL_PORT', '3306')
    if not mhost:
        add('WARN', 'MySQL', 'MYSQL_HOST 未配置 → 登录必失败')
    else:
        ok, ms, err = tcp_check(mhost, mport)
        if ok:
            add('OK', 'MySQL 端口可达', '%s:%s（%.0fms）' % (mhost, mport, ms))
            add('WARN', '白名单提醒', '端口通 ≠ 账号可登录；云数据库要在控制台把本机 IP 加入白名单')
        else:
            add('FAIL', 'MySQL 连不上', '%s:%s → %s' % (mhost, mport, err))
            add('WARN', '排查顺序', '① 安全组/防火墙 ② 对方白名单是否加本机公网 IP ③ 端口是否改了')

# ══════════════════════════════════════════════════════════════
# 4. 模型服务（这一步才是真正的"通了没"）
# ══════════════════════════════════════════════════════════════
section('4/5  LLM / Embedding（真实调用，--skip-llm 可跳过）')

if not ENV:
    add('WARN', '已跳过', '未提供 --env')
else:
    ch = (ENV.get('MODEL_CHANNEL') or 'local').strip()
    add('OK', 'MODEL_CHANNEL', ch)
    if ch == 'local':
        llm_base = ENV.get('LOCAL_LLM_BASE_URL', '')
        llm_model = ENV.get('LOCAL_LLM_MODEL', '')
        llm_key = ENV.get('LOCAL_LLM_API_KEY', '')
        emb_url = emb_endpoint(ENV.get('LOCAL_EMBEDDING_URL', ''), 'local')
        emb_model = ENV.get('LOCAL_EMBEDDING_MODEL', '')
        emb_key = ENV.get('LOCAL_EMBEDDING_API_KEY', '')
    elif ch == 'online':
        llm_base = ENV.get('ONLINE_LLM_BASE_URL', '')
        llm_model = ENV.get('ONLINE_LLM_MODEL', '')
        llm_key = ENV.get('ONLINE_LLM_API_KEY', '')
        emb_url = emb_endpoint(
            ENV.get('ONLINE_EMBEDDING_BASE_URL', '') or llm_base, 'online')
        emb_model = ENV.get('ONLINE_EMBEDDING_MODEL', '')
        emb_key = ENV.get('ONLINE_EMBEDDING_API_KEY', '') or llm_key
    else:
        llm_base = emb_url = ''
        add('WARN', 'mock 通道', '演示模式，不验真实模型')

    if ARGS.skip_llm:
        for nm, u in (('LLM', llm_base), ('Embedding', emb_url)):
            if not u:
                continue
            h, p, scheme = host_port_of(u)
            ok, ms, err = tcp_check(h, p)
            add('OK' if ok else 'FAIL', '%s 端口' % nm,
                ('%s:%s 可达（%.0fms）' % (h, p, ms)) if ok else '%s:%s 不可达 → %s' % (h, p, err))
    else:
        if llm_base:
            url = llm_base.rstrip('/') + '/chat/completions'
            code, raw, ms = http_post(url, {'model': llm_model, 'max_tokens': 4,
                                            'messages': [{'role': 'user', 'content': 'ping'}]}, llm_key)
            if code == 200:
                add('OK', 'LLM 可用', '%s（%.0fms）' % (llm_model, ms))
            elif code in (401, 403):
                add('FAIL', 'LLM 鉴权失败', 'HTTP %d → 检查 API Key' % code)
            elif code == 404:
                add('FAIL', 'LLM 模型不存在', 'HTTP 404 → 网关没有「%s」' % llm_model)
            else:
                add('FAIL' if code is None else 'WARN', 'LLM 异常',
                    ('连不上: %s' % raw[:80]) if code is None else 'HTTP %d: %s' % (code, raw[:80]))
        else:
            add('FAIL', 'LLM 未配置', 'LOCAL_LLM_BASE_URL / ONLINE_LLM_BASE_URL 为空 → 问答全废')

        if emb_url:
            code, raw, ms = http_post(emb_url, {'model': emb_model, 'input': ['自检']}, emb_key)
            if code == 200:
                try:
                    data = json.loads(raw).get('data') or []
                    dim = len(data[0].get('embedding') or []) if data else 0
                except Exception:
                    dim = 0
                want = int(ENV.get('EMBEDDING_DIM') or 1024)
                if dim and dim == want:
                    add('OK', 'Embedding 可用', '%s / %d 维（与配置一致）' % (emb_model, dim))
                elif dim:
                    add('FAIL', 'Embedding 维度不符', '实际 %d，配置 %d → 建表前必须先改配置' % (dim, want))
                else:
                    add('WARN', 'Embedding 响应异常', '没有向量: %s' % raw[:80])
            elif code == 404:
                add('FAIL', 'Embedding 端点不存在', 'HTTP 404 → TEI 是 /embed，OpenAI 兼容是 /v1/embeddings')
            else:
                add('FAIL' if code is None else 'WARN', 'Embedding 异常',
                    ('连不上: %s' % raw[:80]) if code is None else 'HTTP %d: %s' % (code, raw[:80]))
        else:
            add('FAIL', 'Embedding 未配置', '没配 Embedding → 上传文档当场失败')

# ══════════════════════════════════════════════════════════════
# 5. 结论
# ══════════════════════════════════════════════════════════════
section('5/5  结论')
total = FAILS + WARNS
print('  失败 %d 项 / 警告 %d 项 / 通过 %d 项' % (FAILS, WARNS, max(0, TOTAL - FAILS - WARNS)))
if FAILS == 0:
    print('\n  ✅ 前置条件满足，可以开始部署项目。')
    print('     下一步：上传代码 → 装依赖 → 在 server/ 下跑 python scripts/preflight_check.py')
else:
    print('\n  ✗ 有 %d 项失败，先解决再传代码，否则部署完也是坏的。' % FAILS)
    print('     常见：安全组未放行 / 云数据库白名单没加本机 IP / 模型网关不通 / 磁盘太小')
print('\n  本脚本只验"机器与网络"，不验项目配置细节；那部分由 preflight_check.py 负责。')
sys.exit(0 if FAILS == 0 else 1)


