#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把《判据规则》按四份材料切好写进 `prompt_material`（2026-11-01）。

规则从知识库搬出来：知识库只放资料，规则进表，页面按阶段注入。
这份脚本只做一件事——**按章节归属把 60 个主题分到四份里**，不是重写内容。

用法：
    py -X utf8 tools/import_rules_to_materials.py --dry-run
    py -X utf8 tools/import_rules_to_materials.py --apply
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))

SRC = ROOT / 'server' / 'app' / 'secretary' / 'skills' / '判据规则.md'

_HEAD = re.compile(r'^(#{1,4})\s+(.+?)\s*$', re.M)

# 章节归属：按标题开头判断，顺序敏感（先判具体的，再判笼统的）
_CHECK = ('十四、', '0.9', '22.5')
_JUDGE = ('五、', '六、', '七、', '八、', '九、', '十、', '十一、', '十二、')
_ANSWER = ('十五、', '十八、', '十九、', '二十、', '二十一、', '二十二、')
_NUM = re.compile(r'^(\d+)\.(\d+)')

# 「必带」白名单：注入提示词的那几节。名字对不上就按预算顺序补。
_PIN = {
    'fetch': ('零、', '0.1', '一、', '二、', '三、', '四、', '十六、', '十七、',
              '17.1', '17.2', '17.3', '17.5'),
    'judge': ('五、', '七、', '八、', '九、', '十一、'),
    'answer': ('十五、', '十八、', '19.2', '20.0', '20.1', '20.3', '20.4', '20.5', '20.10'),
    'check': ('十四、', '0.9', '22.5'),
}
_BUDGET = {'fetch': 6600, 'judge': 3200, 'answer': 4000, 'check': 1000}

# 补全表种子：从《判据规则》19.2 问法映射表抽出来的（code 读它做归一，不进提示词）
COMPLETE_SEED = (
    ('norm', '情况怎么样', '六项治理成效指标（售电量、台区线损率、故障报修工单、意见工单、用户平均停电时长、'
                           '故障跳闸次数）＋ 预算安排与执行进度', 10),
    ('norm', '整体情况', '六项治理成效指标 ＋ 预算安排与执行进度', 20),
    ('norm', '有什么要我办的', '停在我这个审批节点、状态为「待处理」的审批任务（不含已完成/已驳回/处理中）', 30),
    ('norm', '我这儿多少待审批', '停在我这个审批节点、状态为「待处理」的审批任务', 31),
    ('norm', '卡得最久', '待我审批集合按停留天数降序，逐条给工单名/所属所/金额/停留天数/分档（≤15 正常、16–90 偏慢、>90 积压）', 40),
    ('norm', '挂了多久', '待我审批集合按停留天数降序，逐条给工单名/所属所/金额/停留天数/分档', 41),
    ('norm', '今天我先办哪几张', '待我审批按提问角色的关注口径排序取前 5（财务按金额、运检按风险、领导按总量），每张给「为什么先办它」', 50),
    ('norm', '有没有漏掉的', '遗漏：上一节点已完成且未驳回、但本节点没生成「待处理」任务；与积压分开报，没有就写「无遗漏」', 60),
    ('norm', '哪些逾期了', '先分三种逾期：审批停留看第八节、一般环节看第十一节、超计划完工日看第七节；没超阈就写「未逾期」', 70),
    ('norm', '钱花了没动', '已进入执行阶段（施工中/施工完成待验收）却没有对应施工或完工记录；报落后多少、卡在哪个环节、卡在谁', 80),
    ('norm', '哪些申请资金明显不合理', '三项依据一起给：该所当年可用余额、单笔金额占余额比例、同类项目历史单价', 90),
    ('norm', '哪个所最多', '同一口径分组、降序给数、给第二名做参照', 100),
    ('norm', '这个数是多少', '纯查数：取到数直接答，不给判断、不加分析、不编标准', 110),
)


def slot_of(title: str) -> str:
    """按章节号分四份。数字前缀（0.1 / 17.2 / 19.3 / 20.5 / 22.5）必须单独判，
    否则 19.x 和 20.x 全会掉进 fetch（这是第一版分错的原因）。"""
    t = (title or '').strip()
    m = _NUM.match(t)
    if m:
        major, minor = int(m.group(1)), int(m.group(2))
        if major == 0:
            # 0.x 按内容归位，别一律塞 fetch（第一版全塞进 fetch，等于把「怎么分析」
            # 混进「怎么取数」里 —— 2026-11-02 Fred 指出的分工混乱）
            if minor == 9:
                return 'check'        # 0.9 禁止的反模式 → 自查
            if minor == 4:
                return 'judge'        # 0.4 三个「逾期」不要混 → 判定
            if minor == 1:
                return 'fetch'        # 0.1 先分流 → 取数前要先知道是查数还是分析
            return 'answer'           # 0.2/0.3/0.5/0.6/0.7/0.8 都是「怎么想、怎么写」
        if major == 17:
            return 'fetch'
        if major in (19, 20, 21):
            return 'answer'
        if major == 22:
            return 'check' if minor == 5 else 'answer'
        return 'fetch'
    if t.startswith(_CHECK):
        return 'check'
    if t.startswith(_JUDGE):
        return 'judge'
    if t.startswith(_ANSWER):
        return 'answer'
    # 零节（分流/拆解方法）、一二三四、十三、十七阈值 → 查数时要用
    return 'fetch'


def mark_pinned(slot: str, rows: list[dict]) -> None:
    """按白名单 + 预算标出「必带」。注入时只拼必带，其余靠 get_rule 兜底。"""
    budget = _BUDGET.get(slot, 3000)
    hints = _PIN.get(slot, ())
    used = 0
    for r in rows:
        hit = any(r['section'].startswith(h) or h in r['section'][:10] for h in hints)
        if hit and used + len(r['body']) <= budget:
            r['pinned'] = True
            used += len(r['body'])
        else:
            r['pinned'] = False
    for r in rows:
        if r['pinned']:
            continue
        if used + len(r['body']) <= budget:
            r['pinned'] = True
            used += len(r['body'])


def split(text: str) -> list[tuple[str, str, str]]:
    """返回 [(slot, section, body)]。"""
    marks = [(m.start(), m.end(), m.group(2).strip())
             for m in _HEAD.finditer(text) if len(m.group(1)) >= 2]
    if not marks:
        return [('fetch', '全文', text.strip())]
    out = []
    for i, (start, end, title) in enumerate(marks):
        nxt = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        body = text[end:nxt].strip()
        if body:
            out.append((slot_of(title), title[:160], body))
    return out


def _sql_quote(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def write_sql(groups: dict, out: Path) -> int:
    """生成幂等的 db/prompt_materials.sql（建表 + 清空四份 + 写入）。"""
    lines = [
        '-- 由 tools/import_rules_to_materials.py 生成，请勿手改（2026-11-01）',
        '-- 规则从知识库搬出来：知识库只放资料，规则进这两张表。',
        'CREATE TABLE IF NOT EXISTS prompt_material (',
        '  id bigserial PRIMARY KEY,',
        '  slot varchar(16) NOT NULL,',
        '  section varchar(160) NOT NULL,',
        '  body text NOT NULL,',
        '  sort_no int NOT NULL DEFAULT 0,',
        '  enabled boolean NOT NULL DEFAULT true,',
        '  version_no int NOT NULL DEFAULT 1,',
        "  updated_by varchar(64) NOT NULL DEFAULT '',",
        '  updated_at timestamptz NOT NULL DEFAULT now());',
        'CREATE INDEX IF NOT EXISTS idx_material_slot ON prompt_material (slot, enabled, sort_no);',
        'ALTER TABLE prompt_material ADD COLUMN IF NOT EXISTS pinned boolean NOT NULL DEFAULT true;',
        "DELETE FROM prompt_material WHERE slot IN ('fetch','judge','answer','check');",
        '-- 旧通道关闭（2026-11-01）：规则离开知识库，历史规则文档一律降级为资料。',
        "UPDATE document SET doc_type = 'doc' WHERE doc_type = 'rule';",
    ]
    n = 0
    for slot in ('fetch', 'judge', 'answer', 'check'):
        for i, r in enumerate(groups[slot]):
            lines.append(
                'INSERT INTO prompt_material (slot, section, body, sort_no, enabled, pinned, version_no, updated_by) '
                'VALUES (%s, %s, %s, %d, true, %s, 1, %s);'
                % (_sql_quote(slot), _sql_quote(r['section']), _sql_quote(r['body']), i,
                   'true' if r.get('pinned') else 'false', _sql_quote('import')))
            n += 1
    out.parent.mkdir(parents=True, exist_ok=True)
    # 补全表（代码读，不进提示词）
    lines.append('CREATE TABLE IF NOT EXISTS complete_rule (')
    lines.append('  id bigserial PRIMARY KEY,')
    lines.append("  kind varchar(16) NOT NULL DEFAULT 'norm',")
    lines.append('  match_text varchar(200) NOT NULL,')
    lines.append('  fill_text varchar(300) NOT NULL,')
    lines.append('  priority int NOT NULL DEFAULT 100,')
    lines.append('  enabled boolean NOT NULL DEFAULT true,')
    lines.append('  updated_at timestamptz NOT NULL DEFAULT now());')
    lines.append('CREATE INDEX IF NOT EXISTS idx_complete_priority ON complete_rule (enabled, priority);')
    lines.append('DELETE FROM complete_rule;')
    for kind, match, fill, pri in COMPLETE_SEED:
        lines.append(
            'INSERT INTO complete_rule (kind, match_text, fill_text, priority, enabled) '
            'VALUES (%s, %s, %s, %d, true);'
            % (_sql_quote(kind), _sql_quote(match), _sql_quote(fill), pri))
    out.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='真的写库（默认只预览）')
    ap.add_argument('--dry-run', action='store_true', help='只预览（默认行为）')
    ap.add_argument('--sql-out', default='', help='生成 SQL 文件（部署时导入），如 db/prompt_materials.sql')
    ap.add_argument('--file', default=str(SRC), help='规则 md 路径')
    args = ap.parse_args()

    p = Path(args.file)
    if not p.is_file():
        print('[X] 找不到规则文件：%s' % p)
        return 2
    text = p.read_text(encoding='utf-8')
    parts = split(text)
    if not parts:
        print('[X] 没切出任何小节')
        return 2

    groups: dict[str, list[dict]] = {'fetch': [], 'judge': [], 'answer': [], 'check': []}
    for slot, section, body in parts:
        groups[slot].append({'section': section, 'body': body,
                             'sort_no': len(groups[slot])})
    for slot in groups:
        mark_pinned(slot, groups[slot])

    print('规则文件 %s ｜ 共 %d 节' % (p.name, len(parts)))
    for slot in ('fetch', 'judge', 'answer', 'check'):
        rows = groups[slot]
        chars = sum(len(r['body']) for r in rows)
        print('  %-7s %2d 节  %6d 字' % (slot, len(rows), chars))
        if not args.apply:
            for r in rows[:4]:
                print('      · %s（%d 字）' % (r['section'][:40], len(r['body'])))

    if args.sql_out:
        n = write_sql(groups, Path(args.sql_out))
        print('\n✓ 已生成 %s（%d 条 INSERT）' % (args.sql_out, n))
        if not args.apply:
            return 0

    if not args.apply:
        print('\n（预览模式，未写库。加 --apply 才写）')
        return 0

    from app.secretary import prompt_material
    for slot in ('fetch', 'judge', 'answer', 'check'):
        res = prompt_material.replace_slot(slot, groups[slot], user='import')
        print('  写入 %-7s %s' % (slot, res))
    print('\n✓ 完成。验证：py -X utf8 -c "from app.secretary import prompt_material;print(prompt_material.summary())"')
    return 0


if __name__ == '__main__':
    sys.exit(main())
