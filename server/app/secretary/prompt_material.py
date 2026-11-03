# -*- coding: utf-8 -*-
"""五份提示词材料的读写（2026-11-01）。

规则从知识库搬出来：知识库只放资料，规则进 `prompt_material` 表，按 slot 分四份。
    fetch  查数口径
    judge  判定标准
    answer 作答规则
    check  自检清单

★ 2026-11-03（Fred）：主分支空壳化 —— 这四份整体关闭（见下方 `SLOTS`）。
  文件保留：`online` 分支与将来的规则测试还要用它，删代码等于把恢复路也删了。

**基础提示词不在表里**：红线 / 优先级 / 身份 / 模式做法 / 汇报口吻仍是代码常量
（`prompt_stack.L0_REDLINES`、`agent.RULES_PRIORITY`、`prompts_data.*`），只读。

任何读失败都返回空 —— 规则读不到不许让问答崩，由调用方按"没有这段"处理。
"""
from __future__ import annotations

import re
import time

from app.secretary import pgdb

# ★ 2026-11-03（Fred）：主分支做成**空壳** —— 四份规则材料整体关闭。
#   设成 () 之后：system 里不注入任何规则材料、`get_rule` 取不到东西、
#   提示词页面也不再显示这四份（`/prompts/material/{slot}` 一律 404）。
#   为什么用代码开关而不是"记得别跑导入脚本"：库里只要还剩行，部署上去就又带规则了，
#   空壳就不可复现 —— 而这次的验收就是"部署完是空的"。
#   恢复规则：切 `online` 分支；或把这里改回 ('fetch', 'judge', 'answer', 'check')
#   并按 docs/08-历史归档/2026-11-03-删除的提示词规则/README.md 把原文贴回去。
SLOTS: tuple = ()

SLOT_NAMES = {
    'fetch': '查数口径',
    'judge': '判定标准',
    'answer': '作答规则',
    'check': '自检清单',
}

_TTL = 60.0          # 秒：改了规则最多 1 分钟生效，不必重启
_CACHE: dict = {}


def _conn(autocommit: bool = True):
    return pgdb.connect(autocommit=autocommit)


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


# ── 代号（2026-11-02）────────────────────────────────────────────────
# 为什么要有：清单里的标题是 40 字带括号举例，模型不肯原样抄，凭印象编短名
# （「预算执行达标判据」「十七、资金合理性」）→ get_rule 报"没有这条"，白跑一次。
# 代号从标题前缀自动解析，**不需要人维护**，加规则照常写标题就行。
_CODE_RE = re.compile(r'^\s*(\d+(?:\.\d+)*)')
_CN_CODE_RE = re.compile(r'^\s*([一二三四五六七八九十百零〇]{1,4})、')


def code_of(section: str) -> str:
    """标题 → 代号：'20.2 审批待办…' → '20.2'；'六、缺陷类型…' → '六'；认不出返回 ''。"""
    s = str(section or '')
    m = _CODE_RE.match(s)
    if m:
        return m.group(1)
    m = _CN_CODE_RE.match(s)
    if m:
        return m.group(1)
    return ''


def short_name(section: str, limit: int = 18) -> str:
    """标题 → 短名：去掉括号举例、去掉代号、截断。'20.2 审批待办 / 审批积压类（「…」）'
    → '审批待办 / 审批积压类'。"""
    s = str(section or '').strip()
    code = code_of(s)
    if code:
        s = s[len(code):].lstrip(' 、.．　')
    s = re.sub(r'[（(][^（）()]*[）)]', '', s).strip()
    s = re.sub(r'\s+', ' ', s)
    return (s[:limit] + '…') if len(s) > limit else s


def labeled(section: str) -> str:
    """清单里一行：`20.2 ｜ 审批待办 / 审批积压类`；没有代号就只给短名。"""
    code = code_of(section)
    name = short_name(section)
    return ('%s ｜ %s' % (code, name)) if code else (name or str(section or '')[:18])


# 材料段的统一标记：换阶段时靠它把上一段切掉（换，不是叠）
MARK = '\n\n===== 规则材料（本阶段） =====\n'


def index_text(slots) -> str:
    """「没带上的规则」清单 —— 让模型知道还有哪些可取。

    为什么必须有（2026-11-02）：非必带的节正文不进提示词，如果不给清单，模型
    根本不知道有这些主题，`get_rule` 永远不会被调用 —— 等于那 39 节白存。

    2026-11-02 改：**给代号**（`20.2 ｜ 审批待办 / 审批积压类`），不再给 40 字带括号
    举例的长标题 —— 实测模型不肯原样抄长标题，会凭印象编短名，然后 `get_rule` 报
    "没有这条规则"。代号是标题前缀自动解析出来的，不需要人维护。
    """
    lines = []
    for slot in slots:
        for r in sections(slot):
            if not r.get('pinned') and str(r.get('section') or '').strip():
                lines.append(labeled(str(r['section'])))
    if not lines:
        return ''
    return ('【下面这些规则没直接带上；要按某类题作答前，先 get_rule(代号) 取原文，'
            '取不到就说明没有这条标准；代号照抄左边那个，别自己编名字】\n- '
            + '\n- '.join(lines))


def block(slots, pinned_only: bool = True, with_index: bool = True,
          index_slots=None) -> str:
    """把几份材料拼成一段（带标记）；没有内容返回空串。

    with_index=True 时末尾附「没带上的规则」清单 —— 不然模型不知道还有哪些可取。
    index_slots：清单按这几份算（默认同 slots）。查数阶段用它把 answer 的题型清单
    也一起列出来（2026-11-02）：模型在查数阶段就知道这题该按哪类答，可以提前取。
    """
    parts = []
    for slot in slots:
        t = text(slot, pinned_only=pinned_only)
        if t:
            parts.append(t)
    idx = index_text(index_slots if index_slots is not None else slots) if with_index else ''
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
                # 2026-11-02 真库实测：删不存在的 id 影响 0 行，原来也当成功返回，
                # 页面上就显示"已删除"而实际什么也没发生。看 rowcount。
                if not cur.rowcount:
                    return {'error': '小节不存在：%s' % sec_id}
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
    """整份覆盖（导入脚本 / 页面保存）：清掉原 slot 的行，按 rows 重写。

    rows = [{'section','body','sort_no'}]（可选 pinned / version_no）

    ★ 2026-11-02 真库实测修的一次**数据丢失**：原来是「先 DELETE、再逐行 INSERT」+
      autocommit —— 第三行写不进去（正文含 NUL、或 sort_no 传了非数字）时，
      原内容已经删了、新内容只写了一半，页面只看到一句「写入失败」。
      现在：① 参数先全部算好（算不出来直接返回，一行不碰）；
            ② 再开事务整体提交；中途任何失败 rollback，原内容一行不少。
    """
    slot = (slot or '').strip()
    if slot not in SLOTS:
        return {'error': '未知 slot：%s' % slot}
    rows = [r for r in (rows or []) if str(r.get('body') or '').strip()]
    if not rows:
        return {'error': '没有内容可写'}
    try:
        args = []
        for i, r in enumerate(rows):
            args.append((slot, str(r.get('section') or '')[:160], str(r.get('body') or ''),
                         int(r.get('sort_no') if r.get('sort_no') is not None else i),
                         bool(r.get('pinned', True)),
                         int(r.get('version_no') or 1), str(user or '')[:64]))
    except Exception as exc:                       # noqa: BLE001 - 类型不对就别碰库
        return {'error': '内容格式不对（未写入任何数据）：%s' % str(exc)[:160]}
    conn = None
    try:
        conn = _conn(autocommit=False)
        cur = conn.cursor()
        try:
            cur.execute('DELETE FROM prompt_material WHERE slot = %s', (slot,))
            for a in args:
                cur.execute(
                    'INSERT INTO prompt_material (slot, section, body, sort_no, '
                    'enabled, pinned, version_no, updated_by) '
                    'VALUES (%s,%s,%s,%s,true,%s,%s,%s)', a)
        finally:
            cur.close()
        conn.commit()
        clear_cache()
        return {'ok': True, 'slot': slot, 'sections': len(args)}
    except Exception as exc:                       # noqa: BLE001
        try:
            if conn is not None:
                conn.rollback()                    # ★ 回滚：原内容一行不少
        except Exception:                          # noqa: BLE001
            pass
        print('[prompt_material] 覆盖 %s 失败（已回滚，原内容未动）：%s: %s'
              % (slot, type(exc).__name__, str(exc)[:200]), flush=True)
        return {'error': '写入失败（已回滚，原内容未动）：%s' % str(exc)[:200]}
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:                          # noqa: BLE001
            pass
