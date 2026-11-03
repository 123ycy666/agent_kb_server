# -*- coding: utf-8 -*-
"""一次性脚本：把要从提示词链路里删掉的规则材料原文导出到 docs 备份目录。

背景（2026-11-03 Fred 要求）：主分支要做成空壳（不预设规则），
删掉的这些必须留一份可恢复的原文，所以按**模块导出的真实内容**落盘，
不是手抄。跑法：py -X utf8 _scratch/dump_deleted_rules.py
"""
from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))

OUT = ROOT / 'docs' / '08-历史归档' / '2026-11-03-删除的提示词规则'
OUT.mkdir(parents=True, exist_ok=True)


def _load_tools_importer():
    p = ROOT / 'tools' / 'import_rules_to_materials.py'
    spec = importlib.util.spec_from_file_location('imp_rules', p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> None:
    from app.secretary import agent, modes, preprocess, prompts_data

    # ① 四份材料的来源原文（查数口径 / 判定标准 / 作答规则 / 自检清单都是它切出来的）
    src = ROOT / 'server' / 'app' / 'secretary' / 'skills' / '判据规则.md'
    shutil.copyfile(src, OUT / '判据规则-原文.md')

    # ② 汇报口吻（COMMON）+ 自检清单（CHECK）+ 作答规则（ANSWER_DATA）
    (OUT / '汇报口吻-自检清单-作答规则.md').write_text(
        '# 汇报口吻 / 自检清单 / 作答规则（代码常量原文，2026-11-03 删除前备份）\n\n'
        '来源：`server/app/secretary/agent.py` 的 `COMMON`、`CHECK`；'
        '`server/app/secretary/prompts_data.py` 的 `ANSWER_DATA`。\n\n'
        '## COMMON（汇报口吻与格式，agent.py）\n\n```text\n%s\n```\n\n'
        '## CHECK（交卷前对一遍，agent.py）\n\n```text\n%s\n```\n\n'
        '## ANSWER_DATA（L3 作答要求，prompts_data.py）\n\n```text\n%s\n```\n'
        % (agent.COMMON, agent.CHECK, prompts_data.ANSWER_DATA),
        encoding='utf-8')

    # ③ 补全表种子（complete_rule 的 13 条 norm 行）
    imp = _load_tools_importer()
    lines = ['# 补全表（complete_rule 种子原文，2026-11-03 删除前备份）', '',
             '来源：`tools/import_rules_to_materials.py` 的 `COMPLETE_SEED`'
             '（从《判据规则》19.2 问法映射表抽出）。', '',
             '| 类型 | 匹配问法 | 补成什么 | 优先级 |', '| --- | --- | --- | --- |']
    for kind, match, fill, pri in imp.COMPLETE_SEED:
        lines.append('| %s | %s | %s | %s |' % (kind, match, fill, pri))
    (OUT / '补全表.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')

    # ④ 业务相关问题词表（三处：modes 兜底 / prompts_data / preprocess 粗闸）
    bt_modes = modes._FALLBACK_RULES.get('business_topics', [])
    bt_data = (getattr(prompts_data, 'MODE_RULES', {}) or {}).get('business_topics', [])
    (OUT / '业务相关问题词表.md').write_text(
        '# 业务相关问题词表（2026-11-03 删除前备份）\n\n'
        '删除原因：改成「**没命中业务词表里的直通词 → 一律算业务问题**」的默认判断，'
        '不再靠一张业务问题词表去认业务问题。\n\n'
        '## ① modes._FALLBACK_RULES["business_topics"]\n\n```text\n%s\n```\n\n'
        '## ② prompts_data.MODE_RULES["business_topics"]\n\n```text\n%s\n```\n\n'
        '## ③ preprocess._BIZ（粗闸，业务问题硬编码词表）\n\n```text\n%s\n```\n'
        % ('、'.join(bt_modes), '、'.join(bt_data), '、'.join(preprocess._BIZ)),
        encoding='utf-8')

    # ⑤ 时间地点规则的旧默认（系统当月）
    (OUT / '时间地点规则-改前.md').write_text(
        '# 时间地点规则（2026-11-03 改前备份）\n\n'
        '改的是**默认期间**这一条：\n\n'
        '```text\n'
        '改前：本轮没提时间、上一轮也没有 → 取系统当月（2026-11 → 2026-11）\n'
        '改后：本轮没提时间、上一轮也没有 → 取**上一个月**（2026-11 → 2026-10）\n'
        '```\n\n'
        '代码位置：`server/app/secretary/time_scope.py` 的 `resolve()` 第 ⑦ 段；'
        '展示文案在 `prompt_tree.py` 的 `time.scope`。\n',
        encoding='utf-8')

    print('OK ->', OUT)
    for f in sorted(OUT.iterdir()):
        print('  %-40s %8d B' % (f.name, f.stat().st_size))


if __name__ == '__main__':
    main()
