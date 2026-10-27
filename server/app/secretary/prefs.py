# -*- coding: utf-8 -*-
"""个人偏好：每个员工自己的文风与格式（覆盖站点默认，不覆盖红线）。

三级覆盖（谁具体听谁）
--------------------
    站点包 doc_defaults.yml（产品/单位默认）  →  t_user_pref（个人）  →  本次请求参数

为什么要这张表（2026-10-21）
--------------------------
公文格式每个人习惯不同（有人要 28 磅行距，有人要 26；有人落款写"XX供电所"，有人不写）。
每次让模型问一遍太蠢；写一次存下来，以后出文档默认按他的来。

边界：**只允许白名单键**（字体/字号/行距/单位/日期格式/落款）。人设、提示词不在这里 ——
那属于会话人设，且都压不住 L0 红线。
"""
from __future__ import annotations

import json

# 白名单：键 → (类型, 合法范围说明)
WHITELIST = {
    'body_font_cn': ('str', '正文字体名，如 仿宋_GB2312'),
    'title_font_cn': ('str', '标题字体名'),
    'body_pt': ('num', '正文字号（磅），12–22'),
    'title_pt': ('num', '标题字号（磅），16–36'),
    'line_spacing_pt': ('num', '固定行距（磅），20–36'),
    'amount_unit': ('str', '金额单位：元 / 万元'),
    'date_format': ('str', '日期格式：YYYY-MM-DD / YYYY年M月D日'),
    'sign_off': ('str', '常用落款，如 XX供电所'),
}
_RANGES = {'body_pt': (12, 22), 'title_pt': (16, 36), 'line_spacing_pt': (20, 36)}


class PrefError(ValueError):
    pass


def normalize(prefs: dict | None) -> dict:
    """只留白名单键，校验类型与范围；不认识/越界的直接报错（不静默丢）。"""
    out = {}
    for k, v in (prefs or {}).items():
        if k not in WHITELIST:
            raise PrefError('不认识的偏好项：%s（可用：%s）' % (k, '、'.join(WHITELIST)))
        if v is None or str(v).strip() == '':
            continue
        kind = WHITELIST[k][0]
        if kind == 'num':
            try:
                num = float(v)
            except (TypeError, ValueError):
                raise PrefError('%s 必须是数字：%r' % (k, v)) from None
            lo, hi = _RANGES.get(k, (0, 999))
            if not (lo <= num <= hi):
                raise PrefError('%s 超出范围（%s–%s）：%s' % (k, lo, hi, num))
            out[k] = int(num) if num == int(num) else num
        else:
            s = str(v).strip()
            if len(s) > 60:
                raise PrefError('%s 太长（≤60 字）' % k)
            out[k] = s
    return out


def _conn():
    try:
        from app.secretary import pgdb
        return pgdb.connect(autocommit=True)
    except Exception:                              # noqa: BLE001
        return None


def get(user_id: int) -> dict:
    """读个人偏好；没有 / 库不可用返回 {}。"""
    conn = _conn()
    if conn is None:
        return {}
    try:
        cur = conn.cursor()
        try:
            cur.execute('SELECT prefs FROM t_user_pref WHERE user_id=%s', (int(user_id),))
            row = cur.fetchone()
        finally:
            cur.close()
    except Exception:                              # noqa: BLE001  表还没建也降级
        return {}
    finally:
        try:
            conn.close()
        except Exception:                          # noqa: BLE001
            pass
    if not row:
        return {}
    val = row[0] if not isinstance(row, dict) else row.get('prefs')
    if isinstance(val, str):
        try:
            val = json.loads(val)
        except Exception:                          # noqa: BLE001
            return {}
    return val if isinstance(val, dict) else {}


def set(user_id: int, prefs: dict | None) -> dict:
    """写个人偏好（整体替换）。返回 {'user_id','prefs','saved'[, 'warning']}。"""
    clean = normalize(prefs)
    conn = _conn()
    if conn is None:
        return {'user_id': int(user_id), 'prefs': clean, 'saved': False,
                'warning': '数据库不可用，偏好未保存'}
    try:
        cur = conn.cursor()
        try:
            cur.execute('INSERT INTO t_user_pref (user_id, prefs, updated_at) '
                        'VALUES (%s, %s::jsonb, now()) '
                        'ON CONFLICT (user_id) DO UPDATE '
                        'SET prefs=EXCLUDED.prefs, updated_at=now()',
                        (int(user_id), json.dumps(clean, ensure_ascii=False)))
        finally:
            cur.close()
    except Exception as exc:                       # noqa: BLE001
        return {'user_id': int(user_id), 'prefs': clean, 'saved': False,
                'warning': '偏好未保存：%s' % str(exc)[:160]}
    finally:
        try:
            conn.close()
        except Exception:                          # noqa: BLE001
            pass
    return {'user_id': int(user_id), 'prefs': clean, 'saved': True}


def merge_fmt(base: dict | None, user_id: int | None = None,
              override: dict | None = None) -> dict:
    """文档格式三级覆盖：站点默认 ← 个人 ← 本次请求。"""
    docx = dict(((base or {}).get('docx') or {}))
    for layer in (get(user_id) if user_id else {}, override or {}):
        for k, v in (layer or {}).items():
            if k in WHITELIST and str(v).strip() != '':
                docx[k] = v
    return docx
