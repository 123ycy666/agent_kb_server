#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一次性生成器：把站点包内容**内联成代码模块**（2026-10-30 拆站点包用）。

站点包（`server/app/secretary/sites/`）里剩下的东西按用途分到四个代码模块：
    prompts_data.py    —— 模式提示词 / 身份 / 作答要求 / 文档版式默认值 / 预设问题 / 判模式关键词
    project.py         —— 项目事实（单位名、部门锚点、县公司清单、开关、预算、默认所、定时任务）
    rules_defaults.py  —— 预警/派单规则的兜底值（原 rules/*.yml）
    lexicon_defaults.py—— 业务词表补充（原 lexicon/business_terms.yml）

生成完即可删 `sites/` 与 `site_pack.py`。生成器保留，便于以后重导。
用法：py -X utf8 tools/gen_site_defaults.py
"""
from __future__ import annotations

import pprint
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SITES = ROOT / 'server' / 'app' / 'secretary' / 'sites'
OUT = ROOT / 'server' / 'app' / 'secretary'

HEAD = ('# -*- coding: utf-8 -*-\n'
        '# ⚠️ 本文件由 tools/gen_site_defaults.py 生成，内容来自原站点包（2026-10-30 拆解）。\n')


def _dump(obj) -> str:
    return pprint.pformat(obj, width=100, sort_dicts=False)


def _read(p: Path) -> str:
    return p.read_text(encoding='utf-8') if p.is_file() else ''


def _yml(p: Path) -> dict:
    if not p.is_file():
        return {}
    return yaml.safe_load(_read(p)) or {}


def main() -> int:
    site_yml = _yml(SITES / 'suizhou' / 'site.yml')
    plat = SITES / '_platform'
    modes = {p.stem.replace('mode_', ''): _read(p) for p in sorted(plat.glob('mode_*.md'))}
    identity = _read(SITES / 'suizhou' / 'prompts' / 'identity.md')
    answer_data = _read(SITES / 'suizhou' / 'prompts' / 'answer_data.md')
    doc_defaults = _yml(plat / 'doc_defaults.yml')
    mode_rules = _yml(plat / 'mode_rules.yml')
    presets_yml = _yml(SITES / 'suizhou' / 'presets.yml').get('presets') or {}
    scopes = _yml(SITES / 'suizhou' / 'scopes.yml')
    jobs = _yml(SITES / 'suizhou' / 'jobs.yml')
    alerts = _yml(SITES / 'suizhou' / 'rules' / 'alerts.yml')
    dispatch = _yml(SITES / 'suizhou' / 'rules' / 'dispatch.yml')
    lexicon = _yml(SITES / 'suizhou' / 'lexicon' / 'business_terms.yml')

    (OUT / 'prompts_data.py').write_text(
        HEAD
        + '"""产品提示词正文（原站点包 prompts/ 与 _platform/，2026-10-30 内联进代码）。"""\n'
        + 'from __future__ import annotations\n\n'
        + 'MODE_TEXTS = ' + _dump(modes) + '\n\n'
        + 'IDENTITY = ' + _dump(identity) + '\n\n'
        + 'ANSWER_DATA = ' + _dump(answer_data) + '\n\n'
        + 'DOC_DEFAULTS = ' + _dump(doc_defaults) + '\n\n'
        + 'MODE_RULES = ' + _dump(mode_rules) + '\n\n'
        + 'PRESET_GROUPS = ' + _dump(presets_yml.get('GROUPS') or []) + '\n\n'
        + 'PRESET_ITEMS = ' + _dump(presets_yml.get('PRESETS') or []) + '\n',
        encoding='utf-8')

    (OUT / 'project.py').write_text(
        HEAD
        + '"""项目事实（原站点包 site.yml / scopes.yml / jobs.yml，2026-10-30 内联）。\n\n'
        + '只放"部署时才定、改它要重启"的常量；业务规则不在这里。\n'
        + '"""\n'
        + 'from __future__ import annotations\n\n'
        + 'import os\n\n'
        + 'SITE = (os.environ.get("SITE") or "suizhou").strip()\n'
        + 'SITE_NAME = (os.environ.get("SECRETARY_SITE_NAME") or %r).strip()\n'
        % (site_yml.get('name') or '')
        + 'SITE_FULL = (os.environ.get("SECRETARY_SITE_FULL") or %r).strip()\n'
        % (site_yml.get('full_name') or '')
        + 'SITE_CODE = (os.environ.get("SECRETARY_SITE_CODE") or %r).strip()\n\n'
        % (site_yml.get('code') or '')
        + 'FEATURES = ' + _dump(site_yml.get('features') or {}) + '\n'
        + 'TOOL_BUDGET = ' + str((site_yml.get('model') or {}).get('tool_budget') or 12) + '\n'
        + 'DEFAULT_STATION = ' + _dump((site_yml.get('report') or {}).get('default_station') or '') + '\n\n'
        + 'DEPARTMENT = ' + _dump(scopes.get('department') or {}) + '\n\n'
        + 'JOBS = ' + _dump(jobs) + '\n\n\n'
        + 'def department() -> dict:\n'
        + '    """部门锚点 + 县公司清单（原 scopes.yml 的 department 节）。"""\n'
        + '    return dict(DEPARTMENT)\n',
        encoding='utf-8')

    (OUT / 'rules_defaults.py').write_text(
        HEAD
        + '"""预警/派单规则的产品默认值（原站点包 rules/*.yml，2026-10-30 内联）。\n\n'
        + '改这些值 = 改判定口径，必须走评审；按 key 读取见 `get()`。\n'
        + '"""\n'
        + 'from __future__ import annotations\n\n'
        + 'ALERTS = ' + _dump(alerts) + '\n\n'
        + 'DISPATCH = ' + _dump(dispatch) + '\n\n\n'
        + 'def get(key: str) -> dict:\n'
        + '    """按 `alerts` / `dispatch` 取一组默认值（取不到返回空 dict）。"""\n'
        + '    ks = [str(k).lower() for k in ALERTS] + [str(k).lower() for k in DISPATCH]\n'
        + '    for src in (ALERTS, DISPATCH):\n'
        + '        for k, v in src.items():\n'
        + '            if str(k).lower() == str(key).lower():\n'
        + '                return dict(v) if isinstance(v, dict) else {}\n'
        + '    _ = ks\n'
        + '    return {}\n',
        encoding='utf-8')

    (OUT / 'lexicon_defaults.py').write_text(
        HEAD
        + '"""业务词表补充（原站点包 lexicon/business_terms.yml，2026-10-30 内联）。"""\n'
        + 'from __future__ import annotations\n\n'
        + 'BUSINESS_TERMS = ' + _dump(lexicon) + '\n',
        encoding='utf-8')

    print('已生成：prompts_data.py / project.py / rules_defaults.py / lexicon_defaults.py')
    print('  modes =', list(modes), '｜ identity', len(identity), '字 ｜ answer_data',
          len(answer_data), '字')
    print('  jobs =', len((jobs.get('jobs') or [])), '条 ｜ county_depts =',
          len((scopes.get('department') or {}).get('county_depts') or []))
    print('  alerts keys =', list(alerts), '｜ dispatch keys =', list(dispatch))
    print('  lexicon =', list(lexicon))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
