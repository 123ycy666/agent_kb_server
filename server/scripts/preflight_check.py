#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""部署前环境自检（preflight）。

在**装完依赖、写好 .env 之后，启动后端之前**跑一次：

    source .venv/bin/activate
    python scripts/preflight_check.py

它把"这套系统能不能跑"拆成可验证的 8 组检查，逐项打印 ✓ / ! / ✗：

    1. 运行环境（Python 版本、目录结构、.env）
    2. PostgreSQL + pgvector（本系统自己的库）
    3. 数据库表与纠错词典（t_nc_*，缺了只会静默失效）
    4. MySQL dlj_data（登录认证，缺了**谁都登不上**）
    5. MySQL ai_data（语义层 + 系统提示词，缺了问数是瞎猜）
    6. 内网 LLM（问答作答）
    7. 内网 Embedding（文档入库，维度必须与 EMBEDDING_DIM 一致）
    8. 静态资源（前端 dist、技能文档）

为什么需要它：这 4 个外部依赖（两个 MySQL 库、PG、LLM/Embedding）都在代码之外，
打包再好也带不走。上线前一次性验清楚，比事后对着报错猜要快得多。

退出码：0 = 全部通过（可能有 WARN）；1 = 有 FAIL。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # server/
DRIVER_SRC = ROOT.parent                               # 仓库根（dist、docs 在这）
sys.path.insert(0, str(ROOT))

RESULTS = []      # (level, title, detail)
FAILS = WARN_CNT = 0


def add(level, title, detail=''):
    global FAILS, WARN_CNT
    if level == 'FAIL':
        FAILS += 1
    elif level == 'WARN':
        WARN_CNT += 1
    RESULTS.append((level, title, detail))
    icon = {'OK': '✓', 'WARN': '!', 'FAIL': '✗'}[level]
    print('  [%s] %-38s %s' % (icon, title, detail))


def brief(e, limit=90):
    """异常信息压缩成一行：SQLAlchemy/psycopg 的原始消息会带多行 SQL 与提示。"""
    msg = str(e).splitlines()[0] if str(e) else ''
    msg = msg.split('(Background on this error')[0].strip()
    return '%s: %s' % (type(e).__name__, msg[:limit])


def section(name):
    print('\n' + '-' * 74)
    print(name)
    print('-' * 74)


# ══════════════════════════════════════════════════════════════
# 1. 运行环境
# ══════════════════════════════════════════════════════════════
section('1/8  运行环境与配置安全')

v = sys.version_info
ver = '%d.%d.%d' % (v.major, v.minor, v.micro)
if (v.major, v.minor) >= (3, 12):
    add('OK', 'Python 版本', ver)
elif (v.major, v.minor) >= (3, 10):
    add('WARN', 'Python 版本', '%s（建议 3.12；wheel 需与版本匹配）' % ver)
else:
    add('FAIL', 'Python 版本', '%s 过低，需要 3.10+' % ver)

env_file = DRIVER_SRC / '.env'
if env_file.is_file():
    add('OK', '.env 配置', str(env_file))
else:
    add('FAIL', '.env 配置', '不存在！cp .env.example .env 后再改')

# 依赖是否装全（挑几个关键包，避免逐条 import 出错中断）
missing = []
for mod in ('fastapi', 'uvicorn', 'sqlalchemy', 'psycopg', 'pymysql',
            'pgvector', 'httpx', 'numpy', 'pdfplumber'):
    try:
        __import__(mod)
    except Exception:
        missing.append(mod)
if missing:
    add('FAIL', 'Python 依赖', '缺少: %s（装 wheels）' % ', '.join(missing))
else:
    add('OK', 'Python 依赖', '关键包齐全')

try:
    from app.core.config import settings
    add('OK', '配置加载', 'MODEL_CHANNEL=%s / EMBEDDING_DIM=%d'
        % (settings.model_channel, settings.embedding_dim))
except Exception as e:
    add('FAIL', '配置加载', brief(e, 80))
    print('\n配置都加载不了，后面无法检查。先修 .env。')
    sys.exit(1)

# 生产/开发要分开判：开发态这些只是"上线前要改"，不该把本地自检判死
_is_prod = (settings.app_env or '').lower() in ('production', 'prod')
add('OK', 'APP_ENV', settings.app_env or '未设置（默认 development）')
if _is_prod and settings.debug:
    add('FAIL', 'DEBUG', '生产环境仍开着 DEBUG → 必须 DEBUG=false')
elif settings.debug:
    add('WARN', 'DEBUG', 'true（开发态正常，上线前改 false）')
else:
    add('OK', 'DEBUG', 'false')

_jwt = settings.jwt_secret or ''
_jwt_weak = (_jwt.startswith('dev-only')
             or 'change_me' in _jwt.lower()
             or len(_jwt) < 32)
if _jwt_weak:
    # 开发态只提醒；生产态是硬失败：默认密钥可伪造任意用户 token
    add('FAIL' if _is_prod else 'WARN', 'JWT_SECRET',
        '默认值/占位符/过短（%d 字符）→ 上线前 openssl rand -hex 32' % len(_jwt))
else:
    add('OK', 'JWT_SECRET', '已自定义（%d 字符）' % len(_jwt))

_cors = getattr(settings, 'cors_origins', '') or ''
if '*' in _cors:
    add('FAIL' if _is_prod else 'WARN', 'CORS_ORIGINS', '允许任意来源（*），生产必须写死域名')
elif _is_prod and ('localhost' in _cors or '127.0.0.1' in _cors):
    add('WARN', 'CORS_ORIGINS', '生产仍含 localhost → 改成实际域名')
else:
    add('OK', 'CORS_ORIGINS', _cors or '未设置（同源部署可忽略）')

# ══════════════════════════════════════════════════════════════
# 2 & 3. PostgreSQL：连接、扩展、表、纠错词典
# ══════════════════════════════════════════════════════════════
section('2-3/8  PostgreSQL + pgvector / 应用表 / 纠错词典')

pg_ok = False
try:
    from sqlalchemy import create_engine, text
    eng = create_engine(settings.database_url, connect_args={'connect_timeout': 8})
    with eng.connect() as conn:
        ver_pg = conn.execute(text('SHOW server_version')).scalar()
        dbname = conn.execute(text('SELECT current_database()')).scalar()
        add('OK', 'PostgreSQL 连接', 'v%s / 库=%s' % (ver_pg, dbname))

        ext = [r[0] for r in conn.execute(
            text("SELECT extname FROM pg_extension")).fetchall()]
        if 'vector' in ext:
            add('OK', 'pgvector 扩展', '已安装')
        else:
            add('FAIL', 'pgvector 扩展', '未安装 → CREATE EXTENSION vector;')

        # 应用表是否已建（首次启动会自动建，所以这里只是提示）
        n_tab = conn.execute(text(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema='public'")).scalar()
        if n_tab >= 10:
            add('OK', '应用表', '%d 张' % n_tab)
        else:
            add('WARN', '应用表', '仅 %d 张（首次启动会自动建表，属正常）' % n_tab)

        # 纠错词典 6 张（缺了不报错，只会静默失效）
        LEX = {'t_nc_typo': 100, 't_nc_term': 60, 't_nc_ambiguous': 60,
               't_nc_wordlist': 150, 't_nc_catalog': 15000, 't_nc_pinyin': 15000}
        lex_missing = []
        for t, mini in LEX.items():
            try:
                n = conn.execute(text('SELECT count(*) FROM %s' % t)).scalar()
                if n < mini:
                    lex_missing.append('%s=%s' % (t, n))
            except Exception:
                lex_missing.append('%s(缺表)' % t)
        if lex_missing:
            add('FAIL', '纠错词典 t_nc_*',
                '不全: %s → 导入 db/lexicon.sql' % ', '.join(lex_missing[:3]))
        else:
            add('OK', '纠错词典 t_nc_*', '6 张表就绪（约 4.1 万行）')
    pg_ok = True
except Exception as e:
    add('FAIL', 'PostgreSQL 连接', '%s' % brief(e))
    add('WARN', '已跳过', 'pgvector / 应用表 / 纠错词典三项依赖 PG 连接，未检查')

# ══════════════════════════════════════════════════════════════
# 4 & 5. MySQL：dlj_data（登录）与 ai_data（语义层）
# ══════════════════════════════════════════════════════════════
section('4/8  MySQL dlj_data（统一用户表 / 登录认证）')


def mysql_conn(db):
    import pymysql
    return pymysql.connect(
        host=settings.mysql_host, port=settings.mysql_port,
        user=settings.mysql_user, password=settings.mysql_password,
        database=db, charset=settings.mysql_charset,
        connect_timeout=8, read_timeout=20)


try:
    c = mysql_conn(settings.mysql_db_business)
    try:
        cur = c.cursor()
        cur.execute('SELECT COUNT(*) FROM t_employee')
        n_all = cur.fetchone()[0]
        cur.execute('SELECT COUNT(*) FROM t_employee WHERE administrator_flag=1')
        n_admin = cur.fetchone()[0]
        add('OK', 't_employee 可读', '%d 个账号，其中管理员 %d 个' % (n_all, n_admin))
        if n_admin == 0:
            add('FAIL', '管理员账号', '没有 administrator_flag=1 的账号 → 没人能登录后管系统')
        else:
            add('OK', '管理员账号', '%d 个' % n_admin)
    finally:
        c.close()
except Exception as e:
    add('FAIL', 'MySQL %s' % settings.mysql_db_business,
        '%s' % brief(e))
    add('WARN', '影响', '登录只认 t_employee，且全仓无本地兜底账号 → 登不进去')

section('5/8  MySQL ai_data（语义层 / 系统提示词）')

AI_TABLES = ('ai_table_metadata', 'ai_column_metadata',
             'ai_metric_metadata', 'ai_prompt')
try:
    c = mysql_conn(settings.mysql_db_dict)
    try:
        cur = c.cursor()
        got = {}
        for t in AI_TABLES:
            try:
                cur.execute('SELECT COUNT(*) FROM %s' % t)
                got[t] = cur.fetchone()[0]
            except Exception:
                got[t] = None
        miss = [t for t, v in got.items() if v is None]
        empty = [t for t, v in got.items() if v == 0]
        if miss:
            add('FAIL', 'ai_data 表', '缺失: %s' % ', '.join(miss))
        elif empty:
            add('WARN', 'ai_data 表', '为空: %s（问数会缺口径/表名）' % ', '.join(empty))
        else:
            add('OK', 'ai_data 语义层',
                '表 %d / 字段 %d / 指标 %d / 提示词 %d'
                % (got['ai_table_metadata'], got['ai_column_metadata'],
                   got['ai_metric_metadata'], got['ai_prompt']))
    finally:
        c.close()
except Exception as e:
    add('FAIL', 'MySQL %s' % settings.mysql_db_dict,
        '%s' % brief(e))
    add('WARN', '影响', '语义层缺失 → 问数解析不到表名与口径，回答会退化成猜')

# ══════════════════════════════════════════════════════════════
# 6 & 7. 模型服务
# ══════════════════════════════════════════════════════════════
section('6/8  LLM（问答作答）')

ch = settings.model_channel
if ch == 'local':
    llm_base, llm_model, llm_key = (settings.local_llm_base_url,
                                    settings.local_llm_model,
                                    settings.local_llm_api_key)
elif ch == 'online':
    llm_base, llm_model, llm_key = (settings.online_llm_base_url,
                                    settings.online_llm_model,
                                    settings.online_llm_api_key)
else:
    llm_base = llm_model = llm_key = None
    add('WARN', 'LLM', 'MODEL_CHANNEL=mock（演示模式，不评估真实效果）')

if llm_base:
    import httpx
    url = llm_base.rstrip('/') + '/chat/completions'
    try:
        r = httpx.post(url,
                       headers={'Authorization': 'Bearer %s' % (llm_key or 'x')},
                       json={'model': llm_model, 'messages':
                             [{'role': 'user', 'content': 'ping'}], 'max_tokens': 4},
                       timeout=25)
        if r.status_code == 200:
            add('OK', 'LLM 可用', '%s @ %s' % (llm_model, llm_base))
        elif r.status_code in (401, 403):
            add('FAIL', 'LLM 鉴权失败', 'HTTP %d（检查 API Key）' % r.status_code)
        elif r.status_code == 404:
            add('FAIL', 'LLM 模型不存在',
                'HTTP 404：网关没有「%s」，核对模型名' % llm_model)
        else:
            add('WARN', 'LLM 响应异常', 'HTTP %d: %s'
                % (r.status_code, r.text[:80].replace('\n', ' ')))
    except Exception as e:
        add('FAIL', 'LLM 连不上', '%s（检查 %s）' % (brief(e, 70), llm_base))

section('7/8  Embedding（文档入库）')

if ch == 'local':
    emb_url, emb_model, emb_key = (settings.local_embedding_url,
                                   settings.local_embedding_model,
                                   settings.local_embedding_api_key)
elif ch == 'online':
    # 优先用专门的 embedding 地址；它配的通常是 base（.../v1），必须补 /embeddings
    _ebase = (settings.online_embedding_base_url or settings.online_llm_base_url).rstrip('/')
    if _ebase.endswith('/embeddings') or _ebase.endswith('/embed'):
        emb_url = _ebase
    else:
        emb_url = _ebase + '/embeddings'
    emb_model = settings.online_embedding_model
    emb_key = settings.online_embedding_api_key or settings.online_llm_api_key
else:
    emb_url = emb_model = emb_key = None

if emb_url:
    import httpx
    try:
        r = httpx.post(emb_url,
                       headers={'Authorization': 'Bearer %s' % (emb_key or 'x')},
                       json={'model': emb_model, 'input': ['自检']},
                       timeout=25)
        if r.status_code == 200:
            data = r.json().get('data') or []
            dim = len(data[0].get('embedding') or []) if data else 0
            if dim and dim == settings.embedding_dim:
                add('OK', 'Embedding 可用', '%s / 维度 %d（与 EMBEDDING_DIM 一致）'
                    % (emb_model, dim))
            elif dim:
                add('FAIL', 'Embedding 维度不一致',
                    '实际 %d，配置 EMBEDDING_DIM=%d → 必须先改配置再建表'
                    % (dim, settings.embedding_dim))
            else:
                add('WARN', 'Embedding 返回异常', '响应里没有向量')
        elif r.status_code == 404:
            add('FAIL', 'Embedding 端点不存在',
                'HTTP 404：检查 %s（TEI 是 /embed，OpenAI 兼容是 /v1/embeddings）' % emb_url)
        else:
            add('WARN', 'Embedding 响应异常', 'HTTP %d: %s'
                % (r.status_code, r.text[:80].replace('\n', ' ')))
    except Exception as e:
        add('FAIL', 'Embedding 连不上', '%s（检查 %s）' % (brief(e, 70), emb_url))

# ══════════════════════════════════════════════════════════════
# 8. 静态资源
# ══════════════════════════════════════════════════════════════
section('8/8  静态资源与运行前置（上传目录 / 磁盘 / 端口）')

dist = DRIVER_SRC / 'web' / 'dist' / 'index.html'
if dist.is_file():
    add('OK', '前端 dist', '存在')
else:
    add('FAIL', '前端 dist', '缺 web/dist/index.html → 首页白屏')

skills = list((ROOT / 'app' / 'secretary' / 'skills').glob('*.md'))
if len(skills) >= 15:
    add('OK', '技能文档', '%d 个' % len(skills))
elif skills:
    add('WARN', '技能文档', '仅 %d 个（应为 15）' % len(skills))
else:
    add('FAIL', '技能文档', '0 个 → 技能台空白、判据失效')

lex_sql = DRIVER_SRC / 'db' / 'lexicon.sql'
add('OK' if lex_sql.is_file() else 'FAIL', '纠错词典文件',
    'db/lexicon.sql %s' % ('存在' if lex_sql.is_file() else '缺失'))

# 上传目录：文档原文与解析结果都落这里，不可写 = 上传必失败
try:
    up = settings.upload_path
    probe = up / '.preflight_write_test'
    probe.write_text('ok', encoding='utf-8')
    probe.unlink()
    add('OK', '上传目录可写', str(up))
except Exception as e:
    add('FAIL', '上传目录不可写', '%s（查 UPLOAD_DIR 与卷挂载权限）' % brief(e, 70))

# 磁盘空间：原文 + 切片 + 向量，长期跑会持续增长
try:
    import shutil
    free_gb = shutil.disk_usage(str(settings.upload_path)).free / 1024 ** 3
    if free_gb >= 5:
        add('OK', '磁盘剩余', '%.1f GB' % free_gb)
    elif free_gb >= 1:
        add('WARN', '磁盘剩余', '%.1f GB（建议 ≥5GB）' % free_gb)
    else:
        add('FAIL', '磁盘剩余', '%.1f GB，几乎写不进去' % free_gb)
except Exception as e:
    add('WARN', '磁盘剩余', '无法读取: %s' % brief(e, 50))

# 8200 端口：占用要分清"本服务在跑"还是"别的进程抢了"
import socket
_sk = socket.socket()
_sk.settimeout(1)
_in_use = _sk.connect_ex(('127.0.0.1', 8200)) == 0
_sk.close()
if not _in_use:
    add('OK', '端口 8200', '空闲（启动不会冲突）')
else:
    try:
        import httpx
        _alive = httpx.get('http://127.0.0.1:8200/health', timeout=3).status_code == 200
    except Exception:
        _alive = False
    if _alive:
        add('OK', '端口 8200', '本服务已在运行')
    else:
        add('FAIL', '端口 8200', '被其他进程占用 → netstat -ano | findstr :8200')

# ══════════════════════════════════════════════════════════════
# 总结
# ══════════════════════════════════════════════════════════════
print('\n' + '=' * 74)
print('自检结果：通过 %d 项，警告 %d 项，失败 %d 项'
      % (len(RESULTS) - FAILS - WARN_CNT, WARN_CNT, FAILS))
print('=' * 74)

if FAILS == 0:
    print('\n✅ 没有失败项，可以启动：./start_backend.sh')
    print('   启动后验证：curl -s localhost:8200/api/health')
elif FAILS:
    print('\n✗ 有 %d 项失败，按上面的提示逐项修好再启动。' % FAILS)
    print('   常见修复：')
    print('     · PG/pgvector   → docker compose up -d postgres')
    print('     · 纠错词典      → docker cp db/lexicon.sql kb-postgres:/tmp/ && \\')
    print('                       docker exec kb-postgres psql -U kb -d ad_new -f /tmp/lexicon.sql')
    print('     · MySQL 连不上  → 检查 .env 的 MYSQL_HOST/PORT/USER/PASSWORD 与网络策略')
    print('     · LLM/Embedding → 检查 .env 的 LOCAL_*_URL 与模型名')

print('\n提示：本脚本只验「连得上、有数据、维度对」，不评估回答质量。')
print('      查词典细节请跑：python scripts/check_lexicon.py')
sys.exit(0 if FAILS == 0 else 1)
