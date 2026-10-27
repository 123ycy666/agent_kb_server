# -*- coding: utf-8 -*-
"""站点包装载器：规则从 `config/sites/<SITE>/` 读，代码只认结构。

为什么要有它（2026-10-21）
--------------------------
现在业务规则散在 11 个 py 文件里（部门锚点、预警判据、派单规则、业务词表…）。
换一个单位（仙桃）就得改代码。装载器把它们变成**目录**：换单位 = 换目录 + 改 `.env SITE`。

装载规则（三条）
--------------
1. `_platform/` 是产品基线（红线、身份骨架、模式做法），**站点不许覆盖红线**；
2. `<SITE>/` 覆盖同名条目；
3. **缺必填文件 / 留了 `【待导入】` 占位 → 严格模式直接报错并指名**，绝不静默回落。

本机没有 yaml 会怎样：`PyYAML` 已在 requirements，缺了启动就报错（不猜）。
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

PLACEHOLDER = '【待导入】'
PLATFORM_NAME = '_platform'

# 产品基线必填（少了就不成产品）
REQUIRED_PLATFORM = ('redlines.md', 'identity_base.md',
                     'mode_general.md', 'mode_data.md', 'mode_doc.md', 'mode_office.md')
# 站点必填
REQUIRED_SITE = ('site.yml', 'prompts/identity.md')
# 只在 data/doc 模式注入的业务口径（缺了要报，但不阻塞 general）
BUSINESS_PROMPTS = ('business_rules.md', 'caliber_rules.md',
                    'sql_plan_rules.md', 'work_order_rules.md', 'answer_data.md')
# 站点包没声明顺序时的兜底（照现状；声明了就听站点的）
DEFAULT_L3_ORDER = ('business_rules', 'caliber_rules', 'sql_plan_rules',
                    'work_order_rules', 'answer_data')
DEFAULT_DB_KEYS = ('answer_agent', 'business_rules', 'sql_plan_rules',
                   'work_order_rules', 'caliber_rules')


class PackError(RuntimeError):
    """站点包不完整 / 站点不存在。带上"缺什么"，别只报一句失败。"""


def sites_root() -> Path:
    env = (os.environ.get('SITE_PACK_DIR') or '').strip()
    if env:
        return Path(env)
    # server/app/secretary/site_pack.py → 仓库根
    return Path(__file__).resolve().parents[3] / 'config' / 'sites'


def list_sites() -> list[str]:
    root = sites_root()
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir()
                  if p.is_dir() and not p.name.startswith(('_', '.')))


def _read(path: Path) -> str:
    return path.read_text(encoding='utf-8') if path.is_file() else ''


def _render(text: str, vars_: dict) -> str:
    out = text or ''
    for k, v in (vars_ or {}).items():
        out = out.replace('{%s}' % k, str(v))
    return out


def load(site: str | None = None, *, strict: bool = True) -> dict:
    """装载一个站点包。strict=True 时缺件/占位抛 PackError。"""
    root = sites_root()
    name = (site or os.environ.get('SITE') or 'suizhou').strip()
    site_dir, plat_dir = root / name, root / PLATFORM_NAME
    if not site_dir.is_dir():
        raise PackError('站点不存在：%s（可选：%s）'
                        % (name, '、'.join(list_sites()) or '（无）'))

    missing: list[str] = []
    placeholders: list[str] = []

    def take(base: Path, rel: str, *, required: bool) -> str:
        p = base / rel
        txt = _read(p)
        if not txt.strip():
            if required:
                missing.append('%s/%s' % (base.name, rel))
            return ''
        if PLACEHOLDER in txt:
            placeholders.append('%s/%s' % (base.name, rel))
        return txt

    platform = {f: take(plat_dir, f, required=True) for f in REQUIRED_PLATFORM}
    site_yml = take(site_dir, 'site.yml', required=True)
    try:
        cfg = yaml.safe_load(site_yml) or {}
        if not isinstance(cfg, dict):
            raise ValueError('site.yml 顶层必须是键值')
    except Exception as exc:                       # noqa: BLE001
        raise PackError('site.yml 解析失败（%s）：%s' % (exc, name))

    v = {'SITE_NAME': cfg.get('name') or '', 'SITE_FULL': cfg.get('full_name') or '',
         'SITE_CODE': cfg.get('code') or name}
    if not v['SITE_NAME']:
        missing.append('%s/site.yml:name' % name)
    v['SITE_FULL'] = v['SITE_FULL'] or v['SITE_NAME']

    # 口令类：站点 prompts/*.md；同名的站点版覆盖 _platform 版
    prompts: dict[str, str] = {}
    for base in (plat_dir / 'prompts', site_dir / 'prompts'):
        if base.is_dir():
            for p in sorted(base.glob('*.md')):
                raw = _read(p)
                if PLACEHOLDER in raw:
                    placeholders.append('%s/prompts/%s' % (base.parent.name, p.name))
                prompts[p.stem] = _render(raw, v)
    if not prompts.get('identity'):
        missing.append('%s/prompts/identity.md' % name)

    business_missing = [f for f in BUSINESS_PROMPTS if not (prompts.get(f[:-3]) or '').strip()]
    modes = {m.replace('mode_', '').replace('.md', ''): _render(platform.get(m, ''), v)
             for m in REQUIRED_PLATFORM if m.startswith('mode_')}

    scopes = {}
    sp = site_dir / 'scopes.yml'
    if sp.is_file():
        try:
            scopes = yaml.safe_load(_read(sp)) or {}
        except Exception:                          # noqa: BLE001
            scopes = {}

    doc_defaults = {}
    dd = plat_dir / 'doc_defaults.yml'
    if dd.is_file():
        try:
            doc_defaults = yaml.safe_load(_read(dd)) or {}
        except Exception:                          # noqa: BLE001
            doc_defaults = {}
    sd = site_dir / 'doc_defaults.yml'
    if sd.is_file():
        try:
            site_dd = yaml.safe_load(_read(sd)) or {}
            for k, vv in (site_dd or {}).items():
                if isinstance(vv, dict) and isinstance(doc_defaults.get(k), dict):
                    doc_defaults[k] = {**doc_defaults[k], **vv}
                else:
                    doc_defaults[k] = vv
        except Exception as exc:                   # noqa: BLE001
            print('[site_pack] site doc_defaults.yml parse failed:', exc)

    pcfg = cfg.get('prompts') if isinstance(cfg.get('prompts'), dict) else {}
    l3 = [str(k) for k in (pcfg.get('l3') or DEFAULT_L3_ORDER) if str(k).strip()]
    db_keys = [str(k) for k in (pcfg.get('db') or DEFAULT_DB_KEYS) if str(k).strip()]

    # rules/*.yml and lexicon/*.yml: business tables live in the pack, not in code
    def _load_dir(sub: str) -> dict:
        out = {}
        d = site_dir / sub
        if d.is_dir():
            for f in sorted(d.glob('*.yml')) + sorted(d.glob('*.yaml')):
                try:
                    data = yaml.safe_load(_read(f)) or {}
                    if isinstance(data, dict):
                        out.update(data)
                except Exception as exc:               # noqa: BLE001
                    print('[site_pack] %s/%s parse failed: %s' % (sub, f.name, exc))
        return out

    presets_cfg = {}
    pj = site_dir / 'presets.yml'
    if pj.is_file():
        try:
            presets_cfg = yaml.safe_load(_read(pj)) or {}
        except Exception as exc:                   # noqa: BLE001
            print('[site_pack] presets.yml parse failed:', exc)

    rules = _load_dir('rules')
    lexicon = _load_dir('lexicon')

    result = {'site': name, 'vars': v, 'config': cfg, 'platform': platform,
              'prompts': prompts, 'modes': modes, 'doc_defaults': doc_defaults,
              'missing': missing, 'placeholders': placeholders,
              'business_prompts_missing': business_missing,
              'l3_order': l3, 'db_prompt_keys': db_keys, 'scopes': scopes,
              'rules': rules, 'lexicon': lexicon, 'presets': presets_cfg}

    if strict and (missing or placeholders):
        raise PackError('站点包不完整（%s）：缺 %s；占位未填 %s'
                        % (name, '、'.join(missing) or '无', '、'.join(placeholders) or '无'))
    return result


def info(site: str | None = None) -> dict:
    """给运维页/启动日志看的摘要（不抛：缺件也照实报）。"""
    try:
        p = load(site, strict=False)
        return {'site': p['site'], 'vars': p['vars'], 'missing': p['missing'],
                'placeholders': p['placeholders'],
                'business_prompts_missing': p['business_prompts_missing'],
                'sites': list_sites()}
    except PackError as exc:
        return {'site': site or os.environ.get('SITE') or 'suizhou',
                'error': str(exc), 'sites': list_sites()}
