# -*- coding: utf-8 -*-
"""代码里的提示词文本，允许在页面上覆盖（2026-11-01）。

原来这几份只读、改要发版：
    身份 / 汇报口吻+作答格式 / 四个模式做法 / 业务词表+题型词表
现在表里有一条就用表里的，没有就用代码常量（`prompts_data` / `agent` / `modes`）。

**红线（S0）和优先级（S1）不在这里**，那两条永远只读 —— 允许改「数字必须有出处」等于允许编数。

任何读失败都回退到代码常量，不许让问答崩。
"""
from __future__ import annotations

import json
import time

from app.secretary import pgdb

TEXT_KEYS = {
    'identity': '身份',
    'answer_format': '汇报口吻 + 作答格式',
    'modes': '模式做法（JSON：data / doc / general / office 四段）',
    'keywords': '业务词表 / 题型词表（JSON：produce / data / business_topics / kb / explain / chat …）',
}

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


def mode_texts(defaults: dict) -> dict:
    """四个模式做法：表里有就覆盖对应的那几个，其余用默认。"""
    raw = get('modes', '')
    if not raw.strip():
        return dict(defaults or {})
    try:
        extra = json.loads(raw)
    except Exception:                              # noqa: BLE001 - JSON 写坏就退回默认
        return dict(defaults or {})
    out = dict(defaults or {})
    if isinstance(extra, dict):
        for k, v in extra.items():
            if isinstance(v, str) and v.strip():
                out[str(k)] = v
    return out


def keywords() -> dict:
    """词表覆盖（modes.load_rules 叠加上去）。"""
    raw = get('keywords', '')
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except Exception:                              # noqa: BLE001
        return {}
    out = {}
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, list):
                out[str(k)] = [str(x) for x in v]
    return out


def all_texts() -> list[dict]:
    """给页面：每个 key 的当前正文、是否被覆盖、字数。"""
    from app.secretary import agent, prompts_data
    defaults = {
        'identity': prompts_data.IDENTITY,
        'answer_format': (agent.COMMON + '\n\n' + agent.CHECK).strip(),
        'modes': json.dumps(prompts_data.MODE_TEXTS, ensure_ascii=False, indent=2),
        'keywords': '',
    }
    try:
        from app.secretary import modes
        defaults['keywords'] = json.dumps(modes.load_rules(), ensure_ascii=False, indent=2)
    except Exception:                              # noqa: BLE001
        pass
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
    if key in ('modes', 'keywords') and body.strip():
        try:
            json.loads(body)
        except Exception as exc:                   # noqa: BLE001
            return {'error': 'JSON 格式不对：%s' % str(exc)[:120]}
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
