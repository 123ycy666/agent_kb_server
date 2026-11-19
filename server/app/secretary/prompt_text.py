# -*- coding: utf-8 -*-
"""代码里的提示词文本，允许在页面上覆盖（2026-11-01）。

原来这几份只读、改要发版：
    身份 / 四个模式做法 / 业务词表+题型词表 / 时间地点规则
现在表里有一条就用表里的，没有就用代码常量（`prompts_data` / `modes`）。

**红线（S0）和优先级（S1）不在这里**，那两条永远只读 —— 允许改「数字必须有出处」等于允许编数。

任何读失败都回退到代码常量，不许让问答崩。
"""
from __future__ import annotations

import json
import time

from app.secretary import pgdb

TEXT_KEYS = {
    'identity': '身份（整个智能体就这一份）',
    # 补问法：整份规则是页面上的 JSON —— 哪些词算"已经说清在问什么"、默认补哪句，
    # 以及 `"on": false` 把这一步整个关掉（关掉 = 一字不加）。
    'askfill': '补问法（JSON：on / default / asked / topic / how）',
    'confirm': '问题确定（空 = 不归整，原样往下传）',
    # 主智能体（2026-11-18）：风格是用户的配置，代码不写死；工具开关也是配置。
    'style': '回答风格（主智能体；空 = 中性）',
    'tools': '工具开关（JSON：关掉的工具名数组，如 ["query_data"]）',
}

# 这些 key 存 JSON，保存时校验结构。
_JSON_KEYS = {'askfill', 'tools'}

_TTL = 60.0
_CACHE: dict = {}


def clear_cache() -> None:
    _CACHE.clear()


def _conn():
    return pgdb.connect(autocommit=True)


def _all() -> dict:
    now = time.time()
    hit = _CACHE.get('all')
    if hit and (now - hit[0]) < _TTL:
        return hit[1]
    sql = 'SELECT k, body, version_no, updated_at FROM prompt_text'
    try:
        conn = _conn()
        try:
            cur = conn.cursor()
            try:
                cur.execute(sql)
                rows = cur.fetchall() or []
            finally:
                cur.close()
        finally:
            try:
                conn.close()
            except Exception:                      # noqa: BLE001
                pass
        data = {str(r['k']): r for r in rows}
    except Exception as exc:                       # noqa: BLE001
        print('[prompt_text] 读表失败（按代码默认值）：%s: %s'
              % (type(exc).__name__, str(exc)[:120]), flush=True)
        prev = _CACHE.get('all')
        data = prev[1] if prev else {}
    _CACHE['all'] = (now, data)
    return data


def get(key: str, default: str = '') -> str:
    row = _all().get(key)
    if not row:
        return default
    body = str(row.get('body') or '')
    return body if body.strip() else default


def all_texts() -> list[dict]:
    """给页面：每个 key 的当前正文、是否被覆盖、字数。"""
    from app.secretary import preprocess, prompts_data
    defaults = {
        'identity': prompts_data.IDENTITY,
        'askfill': json.dumps(preprocess._ASKFILL_DEFAULT, ensure_ascii=False, indent=2),
        'confirm': '',
    }
    rows = _all()
    out = []
    for k, name in TEXT_KEYS.items():
        row = rows.get(k)
        body = str((row or {}).get('body') or '')
        used = body if body.strip() else defaults.get(k, '')
        out.append({
            'key': k,
            'name': name,
            'chars': len(used or ''),
            'overridden': bool(body.strip()),
            'body': used,
            'default': defaults.get(k, ''),
        })
    return out


def save(key: str, body: str, user: str = '') -> dict:
    if key not in TEXT_KEYS:
        return {'error': '未知的提示词：%s' % key}
    body = str(body or '')
    if key in _JSON_KEYS and body.strip():
        try:
            parsed = json.loads(body)
        except Exception as exc:                   # noqa: BLE001
            return {'error': 'JSON 格式不对：%s' % str(exc)[:120]}
        # 形状校验：这两组是 JSON **对象**，其余是 JSON **数组**。
        # ★ 2026-11-03 修：原来只放行 `time_year_off` 是对象，`modes`（四个模式做法，
        #   本身就是 {"data": …, "doc": …}）被判成"要 JSON 数组"→ 页面保存模式做法
        #   100% 失败。真库实测：test_prompt_crud 的「合法 JSON 能真的写进 PG」4 项全红。
        if key == 'modes':
            if not isinstance(parsed, dict):
                return {'error': '这个 key 要 JSON 对象（{"说法": 值}）'}
        elif key == 'keywords':
            # 词表既是 {"说法": ["…"]} 的对象，也可以直接给数组 —— 两种都收（2026-11-05）。
            if not isinstance(parsed, (dict, list)):
                return {'error': '这个 key 要 JSON 对象或数组'}
        elif key == 'askfill':
            if not isinstance(parsed, dict):
                return {'error': '补问法要 JSON 对象（{"on": true, "default": "情况怎么样", …}）'}
        elif not isinstance(parsed, list):
            return {'error': '这个 key 要 JSON 数组（["说法", …]）'}
    try:
        conn = _conn()
        try:
            cur = conn.cursor()
            try:
                cur.execute('SELECT version_no FROM prompt_text WHERE k = %s', (key,))
                row = cur.fetchone()
                ver = int((row or {}).get('version_no') or 0) + 1
                cur.execute(
                    'INSERT INTO prompt_text (k, body, version_no, updated_by, updated_at) '
                    'VALUES (%s,%s,%s,%s,now()) '
                    'ON CONFLICT (k) DO UPDATE SET body = EXCLUDED.body, '
                    'version_no = EXCLUDED.version_no, updated_by = EXCLUDED.updated_by, '
                    'updated_at = now()',
                    (key, body, ver, str(user or '')[:64]))
                clear_cache()
                return {'ok': True, 'key': key, 'version_no': ver}
            finally:
                cur.close()
        finally:
            try:
                conn.close()
            except Exception:                      # noqa: BLE001
                pass
    except Exception as exc:                       # noqa: BLE001
        print('[prompt_text] 保存失败：%s: %s' % (type(exc).__name__, str(exc)[:200]), flush=True)
        return {'error': '保存失败：%s' % str(exc)[:200]}


def reset(key: str) -> dict:
    """恢复成代码默认值（删掉覆盖行）。"""
    if key not in TEXT_KEYS:
        return {'error': '未知的提示词：%s' % key}
    try:
        conn = _conn()
        try:
            cur = conn.cursor()
            try:
                cur.execute('DELETE FROM prompt_text WHERE k = %s', (key,))
                clear_cache()
                return {'ok': True, 'key': key}
            finally:
                cur.close()
        finally:
            try:
                conn.close()
            except Exception:                      # noqa: BLE001
                pass
    except Exception as exc:                       # noqa: BLE001
        return {'error': '恢复失败：%s' % str(exc)[:200]}
