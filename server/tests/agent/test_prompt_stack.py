# -*- coding: utf-8 -*-
"""P0 验收：站点包装载 + 七层提示词装配（纯文件，不连库、不打模型）。

钉死的七条
----------
1. **S1**：P0 新增的三个模块里没有单位名（老代码的 50 处是 P3 的活，先打印计数）。
2. 随州包能装载，`{SITE_FULL}` 等变量被 `site.yml` 渲染。
3. **业务口径已落地**：5 道 key 有正文、全包无 `【待导入】` 占位。
4. **S3**：严格模式对"占位包"必须报错并指名（用临时假包验证，不依赖真包状态）。
5. **S2（装载侧）**：换一个站点目录（demo）→ 规则随之变，**不改一行代码**。
6. **G6**：`general` 的层里没有 L3；`data` 里有，且 system 里能见到真实业务口径。
7. **G5（结构侧）**：L4 人设永远排在 L0 之后；L0 文本含出处要求。

运行：py -X utf8 server/tests/agent/test_prompt_stack.py
"""
from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2]
ROOT = SERVER.parent
sys.path.insert(0, str(SERVER))

from app.secretary import modes, prompt_stack as PS, site_pack as SP  # noqa: E402

_checks: list[tuple[str, bool, str]] = []


def chk(label: str, cond: bool, extra: str = "") -> None:
    _checks.append((label, bool(cond), extra))


def _fake_sites(base: Path) -> Path:
    """造假站点根：一份正常 demo，一份故意留占位。"""
    root = base / 'sites'
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    shutil.copytree(ROOT / 'config' / 'sites' / '_platform', root / '_platform')
    for name, title in (('demo', '演示供电中心'), ('broken', '残缺中心')):
        d = root / name / 'prompts'
        d.mkdir(parents=True)
        (root / name / 'presets.yml').write_text(
            'presets:\n  GROUPS:\n  - [demo, demo-group]\n  PRESETS:\n  - {id: p1, group: demo, text: demo question}\n', encoding='utf-8')
        (root / name / 'doc_defaults.yml').write_text(
            'docx:\n  body_pt: 14\n  body_font_cn: DemoFont\nxlsx:\n  header_fill: "AAAFFF"\n  border_color: "111111"\n', encoding='utf-8')
        (root / name / 'rules').mkdir(exist_ok=True)
        (root / name / 'rules' / 'alerts.yml').write_text(
            'alerts:\n  UNDERSPEND_COEF: 0.4\n  STAY_SLOW_DAYS: 7\n', encoding='utf-8')
        (root / name / 'lexicon').mkdir(exist_ok=True)
        (root / name / 'lexicon' / 'business_terms.yml').write_text(
            'business_terms:\n  station_hint: "demo-station|station2"\n  biz_words: "demobiz"\n', encoding='utf-8')
        (root / name / 'scopes.yml').write_text(
            'department:\n  city_dept_id: 9\n  city_label: demo city\n  county_roles: [cto]\n', encoding='utf-8')
        (root / name / 'site.yml').write_text(
            'name: %s\nfull_name: %s\ncode: %s\n' % (name, title, name), encoding='utf-8')
        body = '你是 {SITE_FULL} 的演示助手。\n'
        if name == 'broken':
            body = '【待导入】这里是占位，尚未填写。\n'
        (d / 'identity.md').write_text(body, encoding='utf-8')
    return root


def _strict_ok(site: str) -> bool:
    try:
        SP.load(site, strict=True)
        return True
    except SP.PackError as exc:
        print('  严格装载失败：%s' % exc)
        return False


def main() -> int:
    print("P0：站点包装载 + 提示词装配")

    # ── 1 S1：新增代码里不该有单位名 ────────────────────────
    pat = re.compile(r'随州|环潭|国网|曾都|广水|随县')
    NEW_MODULES = ('site_pack.py', 'prompt_stack.py', 'modes.py')
    legacy, bad_new = [], []
    for p in (SERVER / 'app').rglob('*.py'):
        if '__pycache__' in p.parts or 'skills' in p.parts:
            continue                   # 技能包是别人写的文件，不算我们的代码
        for i, line in enumerate(p.read_text(encoding='utf-8', errors='ignore').splitlines(), 1):
            if not pat.search(line):
                continue
            where = '%s:%d' % (p.relative_to(SERVER), i)
            (bad_new if p.name in NEW_MODULES else legacy).append(where)
    chk("S1 P0 新增的三处模块无单位名", not bad_new, '、'.join(bad_new[:5]))
    print("  [后补] 老代码里仍写死单位名的位置 %d 处（P3 外置后清零）：%s"
          % (len(legacy), '、'.join(legacy[:6])))

    # ── 2 随州包能装载 + 变量渲染 ───────────────────────────
    p = SP.load('suizhou', strict=False)
    chk("随州包可装载", p['site'] == 'suizhou' and bool(p['prompts'].get('identity')))
    chk("变量渲染生效（{SITE_FULL} → 国网随州供电公司）",
        '国网随州供电公司' in (p['prompts'].get('identity') or ''), str(p['vars']))

    # ── 3 业务口径已落地 ────────────────────────────────────
    need = ('business_rules', 'caliber_rules', 'sql_plan_rules', 'work_order_rules', 'answer_data')
    missing_body = [k for k in need if not (p['prompts'].get(k) or '').strip()]
    chk("五道业务口径都有正文", not missing_body, str(missing_body))
    chk("全包已无【待导入】占位", not p['placeholders'], str(p['placeholders']))
    chk("真包能通过严格装载", _strict_ok('suizhou'), '见上面打印的报错')

    # ── 4 S3 + 5 S2：用假包验 ───────────────────────────────
    fake = _fake_sites(SERVER / '.tmp' / 'site_demo')
    old = os.environ.get('SITE_PACK_DIR')
    os.environ['SITE_PACK_DIR'] = str(fake)
    try:
        try:
            SP.load('broken', strict=True)
            err = ''
        except SP.PackError as exc:
            err = str(exc)
        chk("S3 占位包被严格模式拒绝且指名 identity.md",
            '占位未填' in err and 'identity.md' in err, err[:120])

        d = SP.load('demo', strict=False)
        chk("S2 假站点装载成功且身份随包变",
            '演示供电中心' in (d['prompts'].get('identity') or ''), str(d['vars']))
        chk("S2 站点列表能列出它", 'demo' in SP.list_sites(), str(SP.list_sites()))
        chk("S2 假站点的模式做法来自 _platform（代码未改）",
            '通用模式' in (d['modes'].get('general') or ''), str(list(d['modes'])))
    finally:
        if old is None:
            os.environ.pop('SITE_PACK_DIR', None)
        else:
            os.environ['SITE_PACK_DIR'] = old

    # ── 6 G6：general 不带 L3，data 带 ──────────────────────
    g = PS.build(p, mode='general', persona='你是我的参谋长，只挑毛病。')
    ids_g = PS.layer_ids(g)
    chk("G6 general 的层里没有 L3", 'L3' not in ids_g, str(ids_g))
    chk("G6b general 有 L0/L1/L2/L4", {'L0', 'L1', 'L2', 'L4'} <= set(ids_g), str(ids_g))
    dt = PS.build(p, mode='data')
    ids_d = PS.layer_ids(dt)
    chk("G6c data 模式确有 L3", 'L3' in ids_d, str(ids_d))
    chk("G6d 层顺序：L0 最前、人设在 L2 之后",
        ids_g[:2] == ['L0', 'L1'] and ids_g.index('L4') > ids_g.index('L2'), str(ids_g))
    chk("G6e data 模式 system 里含真实业务口径（元→万元）",
        '万元' in dt['system'], '未找到"万元"')
    # L3 的"注入哪几段、什么顺序"必须由站点包决定，而不是代码写死
    l3_ids = [x for x in ids_d if x == 'L3']
    chk("L3 数量 = 站点包 prompts.l3 声明的项数",
        len(l3_ids) == len(p.get('l3_order') or []), '%d vs %s' % (len(l3_ids), p.get('l3_order')))
    chk("站点包声明了 prompts.l3 与 prompts.db",
        bool(p.get('l3_order')) and bool(p.get('db_prompt_keys')),
        '%s / %s' % (p.get('l3_order'), p.get('db_prompt_keys')))

    # ── 7 G5（结构侧）：红线不可覆盖 ────────────────────────
    chk("L0 红线含出处要求", '追不到出处的数字不许写' in g['system'])
    chk("装配说明声明了冲突优先级", '冲突时以序号小的为准' in g['system'])
    chk("人设文本出现在 system 里", '参谋长' in g['system'])

    # ── 8 判模式 ────────────────────────────────────────────
    cases = [('把这份台账做成PPT', {}, 'office'),
             ('帮我写一份廉洁从业的倡议书', {}, 'general'),
             ('帮我写一篇工作总结，主题是本月配网改造推进情况', {}, 'data'),
             ('环潭所上个月线损率多少', {}, 'data'),
             ('请假流程怎么规定的', {}, 'data'),
             ('解释一下什么叫线损', {}, 'general'),
             ('你好', {}, 'general'),
             # 只提业务对象、没有问数词，也必须查库：落 general 会把工具禁掉，
             # 模型只能凭提示词编（线上表现："问整体情况一律答未查到"，2026-10-28）
             ('2025年两水供电所整体情况', {}, 'data'),
             ('两水供电所怎么样', {}, 'data'),
             ('白鹤台区整体情况', {}, 'data'),
             ('', {}, 'general')]
    for q, kw, want in cases:
        got = modes.judge(q, **kw)['mode']
        chk("判模式 %-18r → %s" % (q[:16], want), got == want, got)
    chk("显式任务 → doc", modes.judge('x', task='deck')['mode'] == 'doc')

    # ── 9 P1 接线：V2 入口（判模式 / 按模式装提示词 / 按模式给工具）──
    from app.secretary import agent, agent_v2
    legacy = agent._system
    chk("P1 judge_mode 做PPT → office", agent_v2.judge_mode('把这份台账做成PPT') == 'office')
    chk("P1 judge_mode 业务写作 → data（不查会编业务情况）",
        agent_v2.judge_mode('帮我写一篇工作总结，主题是本月配网改造推进情况') == 'data')
    chk("P1 judge_mode 纯文字 → general", agent_v2.judge_mode('帮我写一份廉洁从业的倡议书') == 'general')
    chk("P1 judge_mode 问数 → data", agent_v2.judge_mode('环潭所上个月线损率多少') == 'data')
    chk("P1 general 不给工具", agent_v2.use_tools('general') is False)
    chk("P1 data/office 照给工具",
        agent_v2.use_tools('data') is True and agent_v2.use_tools('office') is True)

    sys_general = agent_v2.system('general', legacy)
    chk("P1 general 的 system 有 L0 红线", '诚信与安全红线' in sys_general)
    chk("P1 general 的 system 不含业务口径（万元）", '万元' not in sys_general,
        '出现了业务口径')
    chk("P1 general 的 system 不含数据表目录", '可用数据表' not in sys_general)
    sys_data = agent_v2.system('data', legacy)
    chk("P1 data 的 system 含业务口径（万元）", '万元' in sys_data)
    chk("P1 data 的 system 含数据表目录", '可用数据表' in sys_data)
    chk("P1 general 的 system 比 data 短得多（少了一整层业务上下文）",
        len(sys_general) < len(sys_data), '%d vs %d' % (len(sys_general), len(sys_data)))

    # ── 10 G3：编数字仍会被拦（tool_choice 放开后靠这道兜底）──
    claim = agent._untraceable_money('本月预算执行率 85.3%，金额 120 万元。', set())
    chk("G3 无出处的业务数字被拦下", claim == [120.0], str(claim))
    ok_txt = agent._untraceable_money('好的，这段文字我按你的要求写完了。', set())
    chk("G3b 纯文字不误伤", ok_txt == [], str(ok_txt))

    # ── 11 总开关：SECRETARY_AGENT_V2=0 回到旧行为 ──────────
    old_flag = os.environ.get('SECRETARY_AGENT_V2')
    os.environ['SECRETARY_AGENT_V2'] = '0'
    try:
        chk("开关关掉后判模式固定 data（等于旧行为）",
            agent_v2.judge_mode('把这份台账做成PPT') == 'data')
        chk("开关关掉后 system 走旧装配",
            agent_v2.system('general', legacy) == legacy(None))
    finally:
        if old_flag is None:
            os.environ.pop('SECRETARY_AGENT_V2', None)
        else:
            os.environ['SECRETARY_AGENT_V2'] = old_flag

    # -- 12 P3-1: dept anchor + county roles come from site pack --
    from app.secretary import alert_scope
    alert_scope.reset_cache()
    cfg = alert_scope._cfg()
    chk('P3-1 suizhou city_dept_id = 1', cfg['city_dept_id'] == 1, str(cfg))
    chk('P3-1 county_roles from pack', cfg['county_roles'] == ('cto', 'yxb', 'xgscwb'),
        str(cfg['county_roles']))
    chk('P3-1 pack scopes 被装载', (SP.load('suizhou', strict=False).get('scopes') or {})
        .get('department', {}).get('city_dept_id') == 1)
    old_env2 = os.environ.get('SITE_PACK_DIR')
    old_site = os.environ.get('SITE')
    os.environ['SITE_PACK_DIR'] = str(fake)
    os.environ['SITE'] = 'demo'
    try:
        alert_scope.reset_cache()
        cfg2 = alert_scope._cfg()
        chk('P3-1 换包即换锚点（9）', cfg2['city_dept_id'] == 9, str(cfg2))
        chk('P3-1 换包即换县级角色', cfg2['county_roles'] == ('cto',), str(cfg2['county_roles']))
    finally:
        if old_env2 is None:
            os.environ.pop('SITE_PACK_DIR', None)
        else:
            os.environ['SITE_PACK_DIR'] = old_env2
        if old_site is None:
            os.environ.pop('SITE', None)
        else:
            os.environ['SITE'] = old_site
        alert_scope.reset_cache()

    # -- 13 P3-2/P3-3/P3-4: business tables come from rules/*.yml + lexicon/*.yml --
    import importlib
    import app.secretary.alert_rules as AR
    import app.dispatch_rules as DR
    import app.secretary.time_scope as TS
    chk('P3-2 默认值来自代码兜底（0.6）', AR.UNDERSPEND_COEF == 0.6, str(AR.UNDERSPEND_COEF))
    chk('P3-2 判据条数 11', len(AR.ALERT_TYPES) == 11, str(len(AR.ALERT_TYPES)))
    chk('P3-3 派单子类 13', len(DR.SUBTYPES) == 13, str(len(DR.SUBTYPES)))
    old_e3 = os.environ.get('SITE_PACK_DIR')
    old_s3 = os.environ.get('SITE')
    os.environ['SITE_PACK_DIR'] = str(fake)
    os.environ['SITE'] = 'demo'
    try:
        importlib.reload(AR)
        chk('P3-2 换包即换阈值（0.4）', AR.UNDERSPEND_COEF == 0.4, str(AR.UNDERSPEND_COEF))
        chk('P3-2 换包即换停留天数（7）', AR.STAY_SLOW_DAYS == 7, str(AR.STAY_SLOW_DAYS))
        importlib.reload(TS)
        chk('P3-4 换包即换业务词正则', 'demo-station' in TS._STATION_HINT.pattern,
            TS._STATION_HINT.pattern[:40])
    finally:
        if old_e3 is None:
            os.environ.pop('SITE_PACK_DIR', None)
        else:
            os.environ['SITE_PACK_DIR'] = old_e3
        if old_s3 is None:
            os.environ.pop('SITE', None)
        else:
            os.environ['SITE'] = old_s3
        importlib.reload(AR)
        importlib.reload(TS)
    chk('P3-2 恢复后阈值回到 0.6', AR.UNDERSPEND_COEF == 0.6, str(AR.UNDERSPEND_COEF))
    chk('P3-4 恢复后业务词正则回到默认', '供电所' in TS._STATION_HINT.pattern,
        TS._STATION_HINT.pattern[:30])

    # -- 14 P3-5: presets + doc defaults come from pack --
    import app.secretary.presets as PR
    import app.secretary.docgen as DG
    chk('P3-5 预设问题 12 条（来自包）', len(PR.PRESETS) == 12, str(len(PR.PRESETS)))
    chk('P3-5 公文默认字体/字号（来自包）', DG.DOC_FONT == '仿宋_GB2312' and DG.BODY_PT == 16,
        '%s/%s' % (DG.DOC_FONT, DG.BODY_PT))
    old_e5 = os.environ.get('SITE_PACK_DIR')
    old_s5 = os.environ.get('SITE')
    os.environ['SITE_PACK_DIR'] = str(fake)
    os.environ['SITE'] = 'demo'
    try:
        importlib.reload(PR)
        importlib.reload(DG)
        chk('P3-5 换包即换预设问题（1 条）', len(PR.PRESETS) == 1, str(len(PR.PRESETS)))
        chk('P3-5 换包即换公文默认格式',
            float(DG._dd('docx').get('body_pt') or 0) == 14
            and DG._dd('docx').get('body_font_cn') == 'DemoFont', str(DG._dd('docx')))
    finally:
        if old_e5 is None:
            os.environ.pop('SITE_PACK_DIR', None)
        else:
            os.environ['SITE_PACK_DIR'] = old_e5
        if old_s5 is None:
            os.environ.pop('SITE', None)
        else:
            os.environ['SITE'] = old_s5
        importlib.reload(PR)
        importlib.reload(DG)
    chk('P3-5 恢复后预设回到 12 条', len(PR.PRESETS) == 12, str(len(PR.PRESETS)))

    # -- 15 P3-6: city names / org prefixes / report defaults / xlsx colors from pack --
    import app.secretary.alerts as AL
    import app.secretary.alerts4 as A4
    import app.secretary.report as RP
    chk('P3-6 city_names from pack', AL._city_names() == ('随州市', '全市'), str(AL._city_names()))
    chk('P3-6 org_prefixes from pack', A4._org_prefixes() == ('国网', '国网湖北', '湖北省'),
        str(A4._org_prefixes()))
    chk('P3-6 report default_station from pack', RP._default_station() == '环潭供电所',
        RP._default_station())
    chk('P3-6 report identity from pack', '办公智能助手' in RP.BASE_SYSTEM)
    chk('P3-6 xlsx colors from pack', DG._dd('xlsx').get('header_fill') == 'DDEBF7',
        str(DG._dd('xlsx')))
    old_e6 = os.environ.get('SITE_PACK_DIR'); old_s6 = os.environ.get('SITE')
    os.environ['SITE_PACK_DIR'] = str(fake); os.environ['SITE'] = 'demo'
    try:
        # 2026-10-30：版式默认值改成**运行时**从站点包读（`_dd()`），
        # 不再在 import 期改写模块常量 —— 所以这里验的是 _dd()，并顺带钉死
        # "常量不许被 import 改掉"（否则就等于部署文件偷偷改代码）。
        chk('P3-6 换包即换 xlsx 表头色', DG._dd('xlsx').get('header_fill') == 'AAAFFF',
            str(DG._dd('xlsx')))
        chk('P3-6 换包即换边框色', DG._dd('xlsx').get('border_color') == '111111',
            str(DG._dd('xlsx')))
        chk('P3-6 import 不再改写代码常量', DG.XLSX_HEAD_FILL == 'DDEBF7', DG.XLSX_HEAD_FILL)
    finally:
        if old_e6 is None: os.environ.pop('SITE_PACK_DIR', None)
        else: os.environ['SITE_PACK_DIR'] = old_e6
        if old_s6 is None: os.environ.pop('SITE', None)
        else: os.environ['SITE'] = old_s6
        importlib.reload(DG)
    chk('P3-6 恢复后 xlsx 表头色回到 DDEBF7', DG._dd('xlsx').get('header_fill') == 'DDEBF7',
        str(DG._dd('xlsx')))

    from app.secretary import time_scope as TSR
    T_STRIP = tuple(TSR._STRIP_HEAD)
    # -- 16 P3-7: county dept list + name-strip prefixes live in pack --
    dep = (SP.load('suizhou', strict=False).get('scopes') or {}).get('department') or {}
    cds = dep.get('county_depts') or []
    chk('P3-7 pack has 4 county depts', len(cds) == 4, str(len(cds)))
    chk('P3-7 county dept fields complete',
        all({'key', 'dept_id', 'role', 'label'} <= set(c) for c in cds), str(cds[:1]))
    lex = (SP.load('suizhou', strict=False).get('lexicon') or {}).get('business_terms') or {}
    chk('P3-7 pack has strip_head', bool(lex.get('strip_head')), str(lex.get('strip_head')))
    chk('P3-7 pack has org_prefixes', bool(lex.get('org_prefixes')), str(lex.get('org_prefixes')))
    chk('P3-7 pack has city_names', bool(dep.get('city_names')), str(dep.get('city_names')))
    chk('P3-7 代码侧 strip_head 已从包读', T_STRIP == tuple(lex.get('strip_head')),
        str(T_STRIP))

    bad = [c for c in _checks if not c[1]]
    for label, ok, extra in _checks:
        print(("  [OK]   " if ok else "  [FAIL] ") + label + (("   ← " + extra) if not ok and extra else ""))
    print("\n%d 项检查，%d 项失败" % (len(_checks), len(bad)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
