# -*- coding: utf-8 -*-
"""五份提示词材料的读写（2026-11-01）。

规则从知识库搬出来：知识库只放资料，规则进 `prompt_material` 表，按 slot 分四份。
    fetch  查数口径
    judge  判定标准
    answer 作答规则
    check  自检清单

**基础提示词不在表里**：红线 / 优先级 / 身份 / 模式做法 / 汇报口吻仍是代码常量
（`prompt_stack.L0_REDLINES`、`agent.RULES_PRIORITY`、`prompts_data.*`），只读。

任何读失败都返回空 —— 规则读不到不许让问答崩，由调用方按"没有这段"处理。
"""
from __future__ import annotations

import time

from app.secretary import pgdb

SLOTS = ('fetch', 'judge', 'answer', 'check')

SLOT_NAMES = {
    'fetch': '查数口径',
    'judge': '判定标准',
    'answer': '作答规则',
    'check': '自检清单',
}

_TTL = 60.0          # 秒：改了规则最多 1 分钟生效，不必重启
_CACHE: dict = {}


def _conn():
    return pgdb.connect(autocommit=True)


def clear_cache() -> None:
    """保存/导入后调用，让改动立刻生效（不用等 TTL）。"""
    _CACHE.clear()


def _cached(key, loader):
    now = time.time()
    hit = _CACHE.get(key)
    if hit and (now - hit[0]) < _TTL:
        return hit[1]
    val = loader()
    _CACHE[key] = (now, val)
    return val


def sections(slot: str) -> list[dict]:
    """某一份的全部小节：[{id, section, body, sort_no, version_no, pinned}]（按顺序）。"""
    slot = (slot or '').strip()
    if slot not in SLOTS:
        return []

    def load():
        sql = ('SELECT id, section, body, sort_no, version_no, pinned FROM prompt_material '
               'WHERE slot = %s AND enabled ORDER BY sort_no, id')
        try:
            conn = _conn()
            try:
                cur = conn.cursor()
                try:
                    cur.execute(sql, (slot,))
                    return [dict(r) for r in (cur.fetchall() or [])]
                finally:
                    cur.close()
            finally:
                try:
                    conn.close()
                except Exception:                  # noqa: BLE001
                    pass
        except Exception as exc:                   # noqa: BLE001
            print('[prompt_material] 读 %s 失败（按空处理）：%s: %s'
                  % (slot, type(exc).__name__, str(exc)[:120]), flush=True)
            return []

    return _cached('slot:' + slot, load)


def text(slot: str, pinned_only: bool = True) -> str:
    """拼成给模型看的一段正文；没有内容返回空串。

    pinned_only=True（默认）：只拼「必带」小节 —— 注入提示词用，字数受控。
    pinned_only=False：全部小节 —— 页面展示用。
    """
    rows = sections(slot)
    if pinned_only:
        rows = [r for r in rows if r.get('pinned')]
    parts = [str(s.get('body') or '').strip() for s in rows]
    parts = [p for p in parts if p]
    if not parts:
        return ''
    return '\n\n'.join(parts)


def chars(slot: str) -> int:
    return len(text(slot))


# 材料段的统一标记：换阶段时靠它把上一段切掉（换，不是叠）
MARK = '\n\n===== 规则材料（本阶段） =====\n'


def index_text(slots) -> str:
    """「没带上的规则」清单 —— 让模型知道还有哪些可取。

    为什么必须有（2026-11-02）：非必带的节正文不进提示词，如果不给清单，模型
    根本不知道有这些主题，`get_rule` 永远不会被调用 —— 等于那 39 节白存。
    """
    names = []
    for slot in slots:
        for r in sections(slot):
            if not r.get('pinned') and str(r.get('section') or '').strip():
                names.append(str(r['section']))
    if not names:
        return ''
    return ('【下面这些规则没直接带上；要做判断时用 get_rule(主题) 取原文，'
            '取不到就说明没有这条标准】\n- ' + '\n- '.join(names))


def block(slots, pinned_only: bool = True, with_index: bool = True) -> str:
    """把几份材料拼成一段（带标记）；没有内容返回空串。

    with_index=True 时，末尾附「没带上的规则」清单 —— 不然模型不知道还有哪些可取。
    """
    parts = []
    for slot in slots:
        t = text(slot, pinned_only=pinned_only)
        if t:
            parts.append(t)
    idx = index_text(slots) if with_index else ''
    if idx:
        parts.append(idx)
    if not parts:
        return ''
    return MARK + '\n\n'.join(parts)


def strip_block(system: str) -> str:
    """去掉 system 里的材料段，留下基础提示词。"""
    s = str(system or '')
    i = s.find(MARK)
    return s[:i] if i >= 0 else s


def summary() -> list[dict]:
    """四份的概览：[{slot, name, sections, chars}]（页面用）。"""
    out = []
    for slot in SLOTS:
        rows = sections(slot)
        out.append({
            'slot': slot,
            'name': SLOT_NAMES.get(slot, slot),
            'sections': len(rows),
            'chars': sum(len(str(r.get('body') or '')) for r in rows),
        })
    return out


def get_section(sec_id: int) -> dict:
    try:
        conn = _conn()
        try:
            cur = conn.cursor()
            try:
                cur.execute('SELECT id, slot, section, body, sort_no, version_no '
                            'FROM prompt_material WHERE id = %s', (int(sec_id),))
                row = cur.fetchone()
                return dict(row) if row else {}
            finally:
                cur.close()
        finally:
            try:
                conn.close()
            except Exception:                      # noqa: BLE001
                pass
    except Exception as exc:                       # noqa: BLE001
        print('[prompt_material] 取小节失败：%s: %s'
              % (type(exc).__name__, str(exc)[:120]), flush=True)
        return {}


def save_section(sec_id: int, body: str, user: str = '') -> dict:
    """改一个小节正文，版本 +1。返回 {'ok','version_no'} 或 {'error'}。"""
    body = str(body if body is not None else '')
    try:
        conn = _conn()
        try:
            cur = conn.cursor()
            try:
                cur.execute('SELECT id, version_no FROM prompt_material WHERE id = %s',
                            (int(sec_id),))
                row = cur.fetchone()
                if not row:
                    return {'error': '小节不存在：%s' % sec_id}
                ver = int(row['version_no'] or 1) + 1
                cur.execute('UPDATE prompt_material SET body = %s, version_no = %s, '
                            'updated_by = %s, updated_at = now() WHERE id = %s',
                            (body, ver, str(user or '')[:64], int(sec_id)))
                clear_cache()
                return {'ok': True, 'id': int(sec_id), 'version_no': ver}
            finally:
                cur.close()
        finally:
            try:
                conn.close()
            except Exception:                      # noqa: BLE001
                pass
    except Exception as exc:                       # noqa: BLE001
        print('[prompt_material] 保存小节失败：%s: %s'
              % (type(exc).__name__, str(exc)[:200]), flush=True)
        return {'error': '保存失败：%s' % str(exc)[:200]}


def add_section(slot: str, section: str, body: str = '', pinned: bool = True,
                user: str = '') -> dict:
    """新增一节（自己加判断类型：审批条件、缺陷类型对应这种）。

    新节排在最后；`pinned=True` 正文直接进提示词，`False` 则只出现在「可取规则清单」里。
    """
    slot = (slot or '').strip()
    section = str(section or '').strip()[:160]
    if slot not in SLOTS:
        return {'error': '未知 slot：%s' % slot}
    if not section:
        return {'error': '节标题不能为空'}
    try:
        conn = _conn()
        try:
            cur = conn.cursor()
            try:
                cur.execute('SELECT COALESCE(MAX(sort_no), -1) + 1 AS n FROM prompt_material '
                            'WHERE slot = %s', (slot,))
                row = cur.fetchone()
                sort_no = int((row or {}).get('n') or 0)
                cur.execute(
                    'INSERT INTO prompt_material (slot, section, body, sort_no, enabled, '
                    'pinned, version_no, updated_by) VALUES (%s,%s,%s,%s,true,%s,1,%s) '
                    'RETURNING id',
                    (slot, section, str(body or ''), sort_no, bool(pinned), str(user or '')[:64]))
                got = cur.fetchone()
                new_id = int((got or {}).get('id') or 0)
                clear_cache()
                return {'ok': True, 'id': new_id, 'slot': slot, 'section': section}
            finally:
                cur.close()
        finally:
            try:
                conn.close()
            except Exception:                      # noqa: BLE001
                pass
    except Exception as exc:                       # noqa: BLE001
        print('[prompt_material] 新增节失败：%s: %s'
              % (type(exc).__name__, str(exc)[:200]), flush=True)
        return {'error': '新增失败：%s' % str(exc)[:200]}


def delete_section(sec_id: int) -> dict:
    """真删一节（不是软删 —— 软删还占位置、还会出现在清单里）。

    前端有二次确认；删的是这一行，`get_rule` 之后也取不到它。
    """
    try:
        conn = _conn()
        try:
            cur = conn.cursor()
            try:
                cur.execute('DELETE FROM prompt_material WHERE id = %s', (int(sec_id),))
                clear_cache()
                return {'ok': True, 'id': int(sec_id), 'deleted': True}
            finally:
                cur.close()
        finally:
            try:
                conn.close()
            except Exception:                      # noqa: BLE001
                pass
    except Exception as exc:                       # noqa: BLE001
        return {'error': '删除失败：%s' % str(exc)[:200]}


def replace_slot(slot: str, rows: list[dict], user: str = 'import') -> dict:
    """整份覆盖（导入脚本用）：清掉原 slot 的行，按 rows 重写。

    rows = [{'section','body','sort_no'}]（可选 pinned / version_no）
    """
    slot = (slot or '').strip()
    if slot not in SLOTS:
        return {'error': '未知 slot：%s' % slot}
    rows = [r for r in (rows or []) if str(r.get('body') or '').strip()]
    if not rows:
        return {'error': '没有内容可写'}
    try:
        conn = _conn()
        try:
            cur = conn.cursor()
            try:
                cur.execute('DELETE FROM prompt_material WHERE slot = %s', (slot,))
                for i, r in enumerate(rows):
                    cur.execute(
                        'INSERT INTO prompt_material (slot, section, body, sort_no, '
                        'enabled, pinned, version_no, updated_by) '
                        'VALUES (%s,%s,%s,%s,true,%s,%s,%s)',
                        (slot, str(r.get('section') or '')[:160], str(r.get('body') or ''),
                         int(r.get('sort_no') if r.get('sort_no') is not None else i),
                         bool(r.get('pinned', True)),
                         int(r.get('version_no') or 1), str(user or '')[:64]))
                clear_cache()
                return {'ok': True, 'slot': slot, 'sections': len(rows)}
            finally:
                cur.close()
        finally:
            try:
                conn.close()
            except Exception:                      # noqa: BLE001
                pass
    except Exception as exc:                       # noqa: BLE001
        print('[prompt_material] 覆盖 %s 失败：%s: %s'
              % (slot, type(exc).__name__, str(exc)[:200]), flush=True)
        return {'error': '写入失败：%s' % str(exc)[:200]}
