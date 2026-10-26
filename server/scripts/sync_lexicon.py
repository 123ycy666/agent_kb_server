# -*- coding: utf-8 -*-
"""把业务库里的机构/台区，增量同步进纠错词典 `t_nc_catalog`。

    python tools/sync_lexicon.py              # 只看差异（dry-run，默认）
    python tools/sync_lexicon.py --apply      # 真的写库（只增，不改不删）

为什么需要它（2026-10-26）
纠错词典是**离线 CSV 一次性导入的快照**，与业务库之间没有任何同步机制：
业务库新上的台区问不到、改过名的旧名还能命中。详见 docs 里「词典是快照」的说明。

三条纪律
  1. **只增不删、不改**：已存在的名字一律跳过（词典里可能有运维手工补的标注，
     重算会覆盖掉）。业务库没有、词典里却有的，只报告 —— 业务表的推送可能不全，
     按它删会误删两万多条。
  2. **编码口径必须与词典一致**：py_full / py_strict / core_code 都是离线编译产物，
     这里复用 name_corrector 自己的 encode/_scode，**不另写一套**。口径一变，
     同音匹配会静默失效（比漏同步严重得多）。
  3. `initials` 用 pypinyin 生成（与词典同源；依赖里已有 pypinyin 0.55）。

数据来源（MySQL dlj_data）
  t_power_supply_company  → kind=供电公司（4 行）
  t_power_company         → kind=供电所  （47 行）
  t_power_push_area       → kind=台区    （2 万行，area_name 最长 66 字）
"""
from __future__ import annotations

import os
import sys

SERVER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # server/
REPO = os.path.dirname(SERVER)                                        # 仓库根（.env 在这）
sys.path.insert(0, SERVER)                                            # app 包在 server/ 下

import pymysql                     # noqa: E402
from dotenv import dotenv_values   # noqa: E402
from pypinyin import Style, lazy_pinyin  # noqa: E402

from app.secretary import name_fix                       # noqa: E402
from app.secretary.libs.name_correction_lib import name_corrector as nc  # noqa: E402

ENV = dotenv_values(os.path.join(REPO, '.env'))


def mysql():
    return pymysql.connect(
        host=ENV['MYSQL_HOST'], port=int(ENV['MYSQL_PORT']),
        user=ENV['MYSQL_USER'], password=ENV['MYSQL_PASSWORD'],
        database=ENV.get('MYSQL_DB_BUSINESS', 'dlj_data'),
        charset='utf8mb4', connect_timeout=15)


def fetch_business() -> list[tuple[str, str, str, int]]:
    """返回 [(名称, 类别, 归属, 业务主键)]，顺序=公司→所→台区。"""
    out: list[tuple[str, str, str, int]] = []
    c = mysql()
    try:
        cur = c.cursor()
        cur.execute("SELECT id, name FROM t_power_supply_company "
                    "WHERE COALESCE(deleted_flag,0)=0 AND name IS NOT NULL AND name<>''")
        comp = {r[0]: r[1] for r in cur.fetchall()}
        for cid, nm in comp.items():
            out.append((nm.strip(), '供电公司', '', cid))

        cur.execute("SELECT id, name, parent_id FROM t_power_company "
                    "WHERE COALESCE(deleted_flag,0)=0 AND name IS NOT NULL AND name<>''")
        for cid, nm, pid in cur.fetchall():
            out.append((nm.strip(), '供电所', comp.get(pid, ''), cid))

        cur.execute("SELECT id, area_name, station_name FROM t_power_push_area "
                    "WHERE COALESCE(deleted_flag,0)=0 AND area_name IS NOT NULL AND area_name<>''")
        for aid, nm, st in cur.fetchall():
            out.append((nm.strip(), '台区', (st or '').strip(), aid))
    finally:
        c.close()
    return out


def make_row(name: str, kind: str, owner: str, sid: int) -> dict:
    """按词典口径生成一行。单位名的 core_code 为空（与现有 4 万行一致）。"""
    core = '' if kind in ('供电公司', '供电所', '供电服务站') else _scode(name)
    return {
        # owner / source_id 在表里都是 NOT NULL varchar，不能传 None / int
        'name': name, 'kind': kind, 'owner': owner or '',
        'py_full': nc.encode(name, True),
        'py_strict': nc.encode(name, False),
        'initials': ''.join(lazy_pinyin(name, style=Style.FIRST_LETTER)).lower(),
        'core_code': core, 'source_id': str(sid),
    }


def _scode(name: str) -> str:
    try:
        return corrector._scode(nc.canon_digits(nc._area_core(name)))
    except Exception:                    # noqa: BLE001 - 编不出来就留空，别阻塞同步
        return ''


# 单位名的等价前缀：业务库写「广水市供电公司」，词典写「国网广水市供电公司」，
# 是同一家。比对时必须剥掉前缀，否则每跑一次就会插一条重复公司，
# 让「广水公司」出现两个公司级候选（反而把归一搞成歧义）。
_UNIT_PREFIX = ('国网湖北省电力有限公司', '湖北省电力有限公司', '国网', '国家电网')


def _dup_key(name: str, kind: str) -> str:
    key = nc.normalize(name)
    if kind in ('供电公司', '供电所', '供电服务站'):
        for p in _UNIT_PREFIX:
            np_ = nc.normalize(p)
            if key.startswith(np_):
                key = key[len(np_):]
                break
    return key


print('加载纠错引擎（约 4 万行，稍等）...')
corrector = name_fix._corrector()
if corrector is None:
    print('纠错词典不可用，无法比对：%s' % name_fix.status().get('reason'))
    sys.exit(1)

APPLY = '--apply' in sys.argv
import psycopg                        # noqa: E402
from app.secretary import pgdb        # noqa: E402

rows = fetch_business()
print('业务库取到 %d 条（公司/所/台区）' % len(rows))

# ⚠️ 用标准 psycopg，不要用 pgdb：后者是 pymysql 兼容层，fetchall() 返回 **dict 行**，
#    按元组解包会静默只取到零星几条（2026-10-26 踩到：词典 20183 条被读成 1 条）。
conn = psycopg.connect(pgdb.DSN, autocommit=True)
cur = conn.cursor()
cur.execute('SELECT name, kind FROM t_nc_catalog WHERE deleted_flag=0')
existing = {_dup_key(n, k): k for n, k in cur.fetchall()}
print('词典现有 %d 条' % len(existing))


def fix_sequences() -> None:
    """把六张表的 id 序列推到 MAX(id)。

    ⚠️ 必须做：`db/lexicon.sql` 是 pg_dump 用 COPY **显式带 id** 导入的，
    序列不会自动前进。不修的话，任何 INSERT 都从 id=1 开始，
    直接撞 `t_nc_catalog_pkey`（2026-10-26 实测报 duplicate key (id)=(1)）。
    曾试过把 setval 写进 lexicon.sql，但 pg_dump 文件开头会把 search_path 清空，
    不带 schema 的表名在文件里会报 relation does not exist；带 schema 又因为
    psql 的 COPY 分段行为没能可靠生效 —— 所以放在这里，用驱动直连，一次做完。
    """
    for t in ('t_nc_typo', 't_nc_term', 't_nc_ambiguous',
              't_nc_wordlist', 't_nc_catalog', 't_nc_pinyin'):
        try:
            cur.execute("SELECT pg_get_serial_sequence('public.%s','id')" % t)
            seq = cur.fetchone()[0]
            if not seq:
                continue
            cur.execute('SELECT COALESCE(MAX(id), 1) FROM public.%s' % t)
            mx = cur.fetchone()[0]
            cur.execute('SELECT setval(%s, %s)', (seq, mx))
        except Exception as e:          # noqa: BLE001 - 单表失败不影响其他
            print('  序列修正失败 %s：%s' % (t, str(e)[:70]))


fix_sequences()
print('六张表的 id 序列已对齐 MAX(id)')

to_add, dup = [], 0
seen = set()
for name, kind, owner, sid in rows:
    key = _dup_key(name, kind)
    if not key or key in existing or key in seen:
        dup += 1
        continue
    seen.add(key)
    to_add.append(make_row(name, kind, owner, sid))

print('\n=== 差异 ===')
print('  待新增 : %d 条' % len(to_add))
print('  已存在 : %d 条（跳过，不改）' % dup)
from collections import Counter
print('  新增分布:', dict(Counter(r['kind'] for r in to_add)))
for r in to_add[:10]:
    print('    + [%s] %s' % (r['kind'], r['name'][:52]))

if not APPLY:
    print('\n[dry-run] 没有写库。确认无误后加 --apply 执行。')
    conn.close()
    sys.exit(0)

if not to_add:
    print('\n没有需要新增的记录。')
    conn.close()
    sys.exit(0)

sql = """INSERT INTO t_nc_catalog
 (name, kind, owner, py_full, py_strict, initials, core_code, source_id, deleted_flag,
  create_time, update_time)
 VALUES (%s,%s,%s,%s,%s,%s,%s,%s,0,NOW(),NOW())"""
n_ok = 0
for r in to_add:
    try:
        cur.execute(sql, (r['name'], r['kind'], r['owner'], r['py_full'],
                          r['py_strict'], r['initials'], r['core_code'], r['source_id']))
        n_ok += 1
    except Exception as e:            # noqa: BLE001 - 单条失败不影响其余
        print('  跳过：%s | %s' % (r['name'][:36], str(e)[:90]))
# autocommit 模式：每条 INSERT 自己提交，一条失败不会毒化后续（之前正是栽在这）
cur.execute('SELECT count(*) FROM t_nc_catalog WHERE deleted_flag=0')
total = cur.fetchone()
total = total['count'] if isinstance(total, dict) else total[0]
conn.close()
print('\n✅ 新增 %d 条，词典现有 %d 条' % (n_ok, total))
print('   服务端会在 60 秒内自动重载（按 MAX(update_time) 比对），无需重启。')
