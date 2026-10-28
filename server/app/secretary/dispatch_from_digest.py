# -*- coding: utf-8 -*-
"""会议纪要 / 速读结果 → 待办 → 派单（拟办）。

为什么只做"拟办"不给"自动下发"（2026-10-21）
--------------------------------------------
派错人、派错时限是要担责的。所以这里只把 digest 抽出来的事项**翻译成任务草稿**
（谁、干什么、何时前、依据哪句原文），必须人看一眼再调派单接口。

三条不许越界的规矩
------------------
1. **时限解析不出就写"待定"，不许猜**（"近期""尽快""适时"一律不猜）。
2. **没写责任单位就写"待指派"**，不许默认塞给发起人。
3. **原文出处没核实的事项标 `need_review`**，拟办时单独提醒。

自检：py -X utf8 server/tests/agent/test_digest_to_task.py
"""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

TBD = '待定'
NO_OWNER = '待指派'


def _last_day(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def parse_due(text: str | None, today: date | None = None) -> str | None:
    """把中文/数字时限解析成 ISO 日期。解析不出返回 None（调用方写"待定"）。"""
    s = str(text or '').strip()
    if not s:
        return None
    today = today or date.today()

    # 2026-12-31 / 2026/12/31 / 2026年12月31日
    m = re.search(r'(20\d{2})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})', s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            return None

    # 「年底前」「年内」「本年度末」→ 当年 12-31
    if re.search(r'年(底|末|终)', s) or '年内' in s:
        y = re.search(r'(20\d{2})', s)
        return date(int(y.group(1)) if y else today.year, 12, 31).isoformat()

    # 「10月底前」「本月底」→ 当月最后一天
    m = re.search(r'(\d{1,2})\s*月\s*(底|末)', s)
    if m:
        y = re.search(r'(20\d{2})', s)
        return _last_day(int(y.group(1)) if y else today.year, int(m.group(1))).isoformat()
    if '本月底' in s or '当月月底' in s:
        return _last_day(today.year, today.month).isoformat()

    # 「12月31日」「12月31日前」→ 当年
    m = re.search(r'(\d{1,2})\s*月\s*(\d{1,2})\s*日?', s)
    if m:
        y = re.search(r'(20\d{2})', s)
        try:
            return date(int(y.group(1)) if y else today.year,
                        int(m.group(1)), int(m.group(2))).isoformat()
        except ValueError:
            return None

    # 「31日前」这种不带到月的，语义不明 → 不猜
    return None


def _priority(due_iso: str | None, today: date) -> str:
    if not due_iso:
        return '低'
    due = date.fromisoformat(due_iso)
    if due <= today:
        return '紧急'
    if due <= today + timedelta(days=30):
        return '高'
    return '中'


def _title(matter: str) -> str:
    """事项 → 任务标题：去掉语气词、压到 60 字以内。"""
    t = ' '.join(str(matter or '').split())
    t = re.sub(r'^(请|要|需|应|应当|必须|抓紧|尽快)\s*', '', t)
    return t[:60] or '（未写明事项）'


def plan_tasks(items, *, today: date | None = None, owner_fallback: str = NO_OWNER) -> dict:
    """digest 的 items → 拟办清单。

    返回 {'tasks': [...], 'summary': {...}, 'warnings': [...]}
    每条 task：title / owner / due / priority / evidence / need_review / source_ok
    """
    today = today or date.today()
    tasks: list[dict] = []
    warnings: list[str] = []
    seen: set[str] = set()

    for it in items or []:
        matter = str(it.get('matter') or '').strip()
        if not matter:
            continue
        title = _title(matter)
        if title in seen:                     # 同一件事写了两遍，只留一条
            continue
        seen.add(title)

        due = parse_due(it.get('deadline'), today)
        owner = str(it.get('unit') or '').strip() or owner_fallback
        quote = str(it.get('quote') or '').strip()
        verified = it.get('verified')
        need_review = (verified is False) or (not quote)

        tasks.append({
            'title': title,
            'owner': owner,
            'due': due or TBD,
            'priority': _priority(due, today),
            'evidence': quote or '（原文出处缺失）',
            'need_review': need_review,
            'source_ok': bool(quote) and verified is not False,
        })

    if not tasks:
        warnings.append('没抽出可派的事项：这份材料可能只是说明性内容，或者抽取失败')
    no_due = [t['title'] for t in tasks if t['due'] == TBD]
    if no_due:
        warnings.append('%d 条没有可解析的时限，派单前需人工补：%s'
                        % (len(no_due), '；'.join(no_due[:3])))
    no_owner = [t['title'] for t in tasks if t['owner'] == owner_fallback]
    if no_owner:
        warnings.append('%d 条没写责任单位，需人工指派：%s'
                        % (len(no_owner), '；'.join(no_owner[:3])))
    review = [t['title'] for t in tasks if t['need_review']]
    if review:
        warnings.append('%d 条原文出处未核实，必须人工核对后再派：%s'
                        % (len(review), '；'.join(review[:3])))

    order = {'紧急': 0, '高': 1, '中': 2, '低': 3}
    tasks.sort(key=lambda t: (order.get(t['priority'], 9), t['due']))
    return {
        'tasks': tasks,
        'summary': {'total': len(tasks),
                    'urgent': sum(1 for t in tasks if t['priority'] == '紧急'),
                    'need_review': sum(1 for t in tasks if t['need_review']),
                    'no_due': len(no_due), 'no_owner': len(no_owner)},
        'warnings': warnings,
    }


def to_payload(task: dict, *, session_id: str | None = None) -> dict:
    """拟办条目 → 派单入参草稿（字段对齐现有 dispatch 接口；由人确认后才发）。"""
    return {
        'title': task['title'],
        'owner': task['owner'],
        'due': task['due'],
        'priority': task['priority'],
        'evidence': task['evidence'],
        'need_review': task['need_review'],
        'source': 'digest',
        'session_id': session_id,
    }
