# -*- coding: utf-8 -*-
"""一次性导出：把线上 `ai_data.ai_prompt` 的 5 道提示词写成站点包里的 md 文件。

为什么要这个脚本（2026-10-21）
------------------------------
站点包是规则真源，但随州的业务口径正文现在还在库里。内网连得上库的机器跑一次
本脚本，把正文落到 `server/app/secretary/sites/<SITE>/prompts/*.md`，之后就以文件为准（可 diff、可 review）。

用法
----
    py -X utf8 server/scripts/export_prompts.py              # 只预览：打印-key/字数/前几行
    py -X utf8 server/scripts/export_prompts.py --apply      # 真写文件
    py -X utf8 server/scripts/export_prompts.py --apply --site xiantao

只读库，不写库。导出后请人工删掉文件里的 `【待导入】` 标记（本脚本会自动清掉那一行）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))

from app.secretary import tools_db  # noqa: E402,F401  （保留导入，证明同源配置；实际用 pymysql 直连）

ROOT = SERVER.parent
# 库里的 key → 站点包里的文件名
# ⚠️ answer_agent 刻意导成 `answer_data_from_db.md`（**原样留档**），不覆盖我们改写过的
#    `answer_data.md`：库版第一句是"基于已执行的 SQL 结果…"，正是要拆掉的那个前提。
KEYS = {
    'business_rules': 'business_rules.md',
    'caliber_rules': 'caliber_rules.md',
    'sql_plan_rules': 'sql_plan_rules.md',
    'work_order_rules': 'work_order_rules.md',
    'answer_agent': 'answer_data_from_db.md',
}
HEADER = ('<!-- 来源：ai_data.ai_prompt（prompt_key=%s，id=%s，更新于 %s）。'
          '由 server/scripts/export_prompts.py 导出，人工复核后使用。 -->\n\n')


def _strip_placeholder(text: str) -> str:
    lines = [ln for ln in (text or '').splitlines() if '【待导入】' not in ln]
    return '\n'.join(lines).lstrip('\n')


def _connect():
    """直连（只读）：用根目录 .env 的 MYSQL_*，不做任何写操作。"""
    import pymysql
    env = {}
    env_file = ROOT / '.env'
    for line in env_file.read_text(encoding='utf-8', errors='ignore').splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip()
    return pymysql.connect(host=env['MYSQL_HOST'], port=int(env.get('MYSQL_PORT', 3306)),
                           user=env['MYSQL_USER'], password=env['MYSQL_PASSWORD'],
                           charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor,
                           connect_timeout=10)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='真的写文件（默认只预览）')
    ap.add_argument('--site', default='suizhou')
    args = ap.parse_args()

    out_dir = ROOT / 'config' / 'sites' / args.site / 'prompts'
    if not out_dir.parent.is_dir():
        print('站点目录不存在：%s' % out_dir.parent)
        return 2

    sql = ('SELECT id, prompt_key, prompt_content, update_time FROM ai_data.ai_prompt '
           'WHERE IS_new=1 AND deleted_flag=0 AND prompt_key IN (%s) ORDER BY id'
           % ','.join(['%s'] * len(KEYS)))
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, list(KEYS))
            rows = cur.fetchall()
    finally:
        conn.close()
    if not rows:
        print('库里没有这 5 道提示词（检查 ai_data.ai_prompt 的 IS_new/deleted_flag）')
        return 4

    seen = set()
    for r in rows:
        key = r.get('prompt_key')
        body = _strip_placeholder(r.get('prompt_content') or '')
        if not key or not body.strip():
            continue
        seen.add(key)
        name = KEYS[key]
        head = HEADER % (key, r.get('id'), r.get('update_time'))
        text = head + body.rstrip() + '\n'
        print('%-20s id=%-5s %5d 字 → %s' % (key, r.get('id'), len(body), name))
        if args.apply:
            (out_dir / name).write_text(text, encoding='utf-8')

    missing = [k for k in KEYS if k not in seen]
    if missing:
        print('\n⚠️ 没导出到的 key：%s（检查库里是否 IS_new=1）' % '、'.join(missing))
    print('\n%s：%s' % ('已写入' if args.apply else '预览（未写）', out_dir))
    if not args.apply:
        print('确认无误后加 --apply 再跑一次')
    return 0


if __name__ == '__main__':
    sys.exit(main())
