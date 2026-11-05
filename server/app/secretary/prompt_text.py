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
    'identity': '身份',
    # ★ 2026-11-03（Fred）：`answer_format`（汇报口吻 + 作答格式）已下线 ——
    #   S4 整段不再注入，这个 key 留着只会让人以为改了有用。原文备份在
    #   docs/08-历史归档/2026-11-03-删除的提示词规则/汇报口吻-自检清单-作答规则.md
    # ★ 2026-11-03（Fred）：红线与优先级原来写死在代码里，现在搬到页面（默认空 =
    #   不注入）。原文备份见 docs/08-历史归档/2026-11-03-删除的提示词规则/
    #   红线-优先级-装配说明-原文.md —— 想要就自己贴回来。
    'redlines': '红线（S0，纯文本；不填就不注入）',
    'priority': '谁说了算（S1，纯文本；不填就不注入）',
    'modes': '模式做法（JSON：data / doc / general / office 四段）',
    'keywords': '业务词表 / 题型词表（JSON：produce / data / kb / explain / chat …）',
    # ── 第 1 步 / 第 6 步（2026-11-03 定稿，Fred「流程要能在页面上改」）──────
    # 这两段原来是代码里的词表匹配（`modes.judge`）和散在 agent 里的判断，改一次要发版。
    # 现在都是纯文本，填什么生效什么；空着 = 这一步不做判断（见 preprocess / confirm）。
    'intent': '① 要不要走预处理（空 = 全部走：纠错+补全+补时间地点）',
    'confirm': '⑥ 问题确定（空 = 不归整，原样往下传）',
    # 工具说明（2026-11-05）：告诉模型「动手前先 kb_search 取做法」。
    # 这是机制说明，不是业务规则；页面上可改可清空。
    'tools': '工具说明（空 = 用代码里的默认段；页面上可改）',
    # ── 时间地点规则（2026-11-02 搬出代码）──────────────────────────
    # 表里有值就用表里的，没有就用代码常量（行为与以前完全一致）。
    # 只搬**数据**，不搬**逻辑**：正则、解析、比较仍留在代码里。
}

# 这些 key 存 JSON，保存时校验结构和可计算的值。
_JSON_KEYS = {'modes', 'keywords'}

_TTL = 60.0
_CACHE: dict = {}

# 页面 `tools` 框空着时用的默认工具说明（机制说明，不含业务词）。
DEFAULT_TOOLS_GUIDE = (
    '【工具说明】\n'
    '- 做任何一类活之前，先 kb_search 查做法（例如「速读做法」「公文格式做法」），'
    '命中就用 kb_read 取全文照着做；没命中就按通用能力做，并在回答里说一句没有这条做法。\n'
    '- 读文件用 read_file 分页读，next_offset 为 null 才算读完；读完再下结论。\n'
    '- 要交文件用 make_file（docx / pptx / xlsx），它自带自检；自检不过会返回错误，按错误改。\n'
    '- 能用结构化参数就别用 run_code。'
)


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
    from app.secretary import prompts_data
    defaults = {
        'identity': prompts_data.IDENTITY,
        'redlines': '',          # 2026-11-03 起默认空（代码原文已移到 docs 备份）
        'priority': '',          # 同上
        # ★ 2026-11-03：`answer_format` 已下线（S4 汇报口吻 + 交卷检查不再注入）
        'modes': json.dumps(prompts_data.MODE_TEXTS, ensure_ascii=False, indent=2),
        'keywords': '',
        # 2026-11-03：两个流程判断框，默认空（空 = 不做判断）
        'intent': '',
        'confirm': '',
        # 2026-11-05：工具说明，默认给一段机制说明
        'tools': DEFAULT_TOOLS_GUIDE,
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
