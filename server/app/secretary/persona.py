# -*- coding: utf-8 -*-
"""会话人设：每个对话可以另设身份（"你是我的参谋长"），只对这个对话生效。

设计要点（2026-10-21）
--------------------
1. **只追加、不覆盖**：人设进 L4 层，永远排在 L0 红线之后（见 `prompt_stack`）。
   人设里写"忽略数字必须有出处"不会生效——这是故意的。
2. **有长度上限**：人设是"一句身份"，不是贴材料的地方；超长会把业务规则挤出上下文。
3. **存会话表**：`t_chat_session.persona_name / persona_prompt`（见 `db/chat_persona.sql`）。
4. **读不到库不崩**：本机没装 psycopg 时，存取函数降级返回空，问答照常跑。

自检：py -X utf8 server/tests/agent/test_persona.py
"""
from __future__ import annotations

import re

MAX_NAME = 20          # 人设名（"参谋长"）
MAX_TEXT = 500         # 人设正文（一句话身份 + 几条要求）
_CTRL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')


class PersonaError(ValueError):
    """人设不合法（太长 / 空 / 只有控制字符）。"""


def limits() -> dict:
    return {'max_name': MAX_NAME, 'max_text': MAX_TEXT}


def normalize(name: str | None, text: str | None) -> dict | None:
    """清洗人设；空文本返回 None（等于清空）。

    超过上限**直接报错**而不是静默截断 —— 用户以为设上了、实际被砍半，比报错更糟。
    """
    t = _CTRL.sub('', (text or '')).strip()
    if not t:
        return None
    if len(t) > MAX_TEXT:
        raise PersonaError('人设最多 %d 字（现在 %d 字）：它是一句身份，不是贴材料的地方'
                           % (MAX_TEXT, len(t)))
    n = _CTRL.sub('', (name or '')).strip() or t[:MAX_NAME]
    if len(n) > MAX_NAME:
        raise PersonaError('人设名最多 %d 字（现在 %d 字）' % (MAX_NAME, len(n)))
    return {'name': n, 'prompt': t}


def _conn():
    """延迟导入（本机可能没有 psycopg）；连不上返回 None，由调用方降级。"""
    try:
        from app.secretary import pgdb
        return pgdb.connect(autocommit=True)
    except Exception:                              # noqa: BLE001
        return None


def get_persona(session_id: str) -> dict | None:
    """读某个会话的人设；没有 / 读不到都返回 None。"""
    sid = (session_id or '').strip()
    if not sid:
        return None
    conn = _conn()
    if conn is None:
        return None
    try:
        cur = conn.cursor()
        try:
            cur.execute('SELECT persona_name, persona_prompt FROM t_chat_session '
                        'WHERE session_id=%s LIMIT 1', (sid,))
            row = cur.fetchone()
        finally:
            cur.close()
    except Exception:                              # noqa: BLE001  列不存在/库异常都降级
        return None
    finally:
        try:
            conn.close()
        except Exception:                          # noqa: BLE001
            pass
    if not row:
        return None
    if isinstance(row, dict):
        name, prompt = row.get('persona_name'), row.get('persona_prompt')
    else:
        name, prompt = row[0], row[1]
    if not (prompt or '').strip():
        return None
    return {'name': name or '', 'prompt': prompt}


def set_persona(session_id: str, *, name: str | None = None, text: str | None = None,
                user_id: int | None = None, user_code: str | None = None) -> dict:
    """设/清空某个会话的人设。

    找不到会话就先插一行（会话可能还没被第一次提问创建）。
    库不可用时不抛，返回 saved=False + warning —— 问答不能因为人设存不上就挂。
    """
    sid = (session_id or '').strip()
    if not sid:
        raise PersonaError('缺少 session_id')
    person = normalize(name, text)
    result = {'session_id': sid, 'name': (person or {}).get('name', ''),
              'prompt': (person or {}).get('prompt', ''),
              'cleared': person is None}

    conn = _conn()
    if conn is None:
        return {**result, 'saved': False, 'warning': '数据库不可用，人设未保存'}
    try:
        cur = conn.cursor()
        try:
            cur.execute('INSERT INTO t_chat_session (session_id, user_id, user_code, channel) '
                        'VALUES (%s, %s, %s, %s) ON CONFLICT (session_id) DO NOTHING',
                        (sid, user_id, user_code, 'web'))
            cur.execute('UPDATE t_chat_session SET persona_name=%s, persona_prompt=%s '
                        'WHERE session_id=%s',
                        (result['name'] or None, result['prompt'] or None, sid))
        finally:
            cur.close()
    except Exception as exc:                       # noqa: BLE001
        return {**result, 'saved': False, 'warning': '人设未保存：%s' % exc}
    finally:
        try:
            conn.close()
        except Exception:                          # noqa: BLE001
            pass
    return {**result, 'saved': True}
