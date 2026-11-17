# -*- coding: utf-8 -*-
"""干活线全链路：一次「做活」从进来到交出文件，被哪些输入影响 —— 按处理顺序全部列出，每项都能看原文。

接口与问数线同一套形状：`segments()` 给段与项，`body(key)` 给某一项的原文。
可改的项在 `cfg.inject_text`（身份/红线/优先级/数字纪律/工具说明）。
"""
from __future__ import annotations

import json
import time

from app.secretary.impact import _conn, _empty, _fetch, _rows, _src, _text, _try_fetch


# ── 探针 ────────────────────────────────────────────────────────────
def _p_route_rule(cur):
    rows = _fetch(cur, 'select k, body, version_no from cfg.route_rule order by k')
    if not rows:
        return _empty('cfg.route_rule 没有行 —— 入口只能用代码里写死的词表。')
    return _text('\n\n'.join('## %s（v%s）\n%s'
                             % (r['k'], r.get('version_no'),
                                json.dumps(r['body'], ensure_ascii=False, indent=2)
                                if isinstance(r['body'], (dict, list)) else str(r['body']))
                             for r in rows))


def _p_route_decision(cur):
    from app.secretary import router
    return _text(_src(router, '_wants_deliver') + '\n\n' + _src(router, 'route'))


def _p_product(cur):
    return _text(
        '产物类型按这个顺序定（都空就是 docx）：\n\n'
        '1 入口分流认出来的产物（router.route 的 product：说清 ppt/表格/文档 + 让它做出来）\n'
        '2 命中说明书的 product（说明书说这篇出什么）\n'
        '3 都没有 → docx\n\n'
        '代码：server/app/secretary/loop.py:201-203（兜底）、229-230（说明书覆盖）',
        size='3 级')


def _p_name_fix(cur):
    total = 0
    for t in ('t_nc_catalog', 't_nc_pinyin', 't_nc_term', 't_nc_typo', 't_nc_wordlist',
              't_nc_ambiguous'):
        n = _fetch(cur, 'select count(*) as n from cfg.%s where deleted_flag = 0' % t)
        total += int((n[0] or {}).get('n') or 0)
    from app.secretary import name_fix
    return _text(_src(name_fix, 'fix') +
                 '\n\n（六张词典与问数线同一份，共 %d 条；干活线在入口之后先纠一次，模型感知不到。）'
                 % total, size='%d 条' % total)


def _p_spec_menu(cur):
    from app.secretary import spec as SP
    txt = SP.menu_text(None)
    if not txt.strip():
        return _empty('kb.spec 里没有可见的说明书 —— 模型只能按通用能力做。')
    return _text(txt, size='%d 行' % len(txt.splitlines()),
                 note='常驻 system 的目录：一行一篇（名称 · 什么时候用 · 产物）。')


def _p_spec_pick(cur):
    from app.secretary import spec as SP
    return _text(_src(SP, 'pick'), note='点名 > 适用词命中 > 本人 > 全局 > 最新版。')


def _p_spec_table(cur):
    rows = _fetch(cur, "select id, name, role_code, product, version_no, "
                       "case when owner_id is null then '全局' else '本人' end as owner, "
                       "status, enabled, length(coalesce(body,'')) as chars, "
                       "coalesce(jsonb_array_length(to_jsonb(check_items)), 0) as checks, "
                       "coalesce(when_to_use,'') as when_to_use from kb.spec order by id")
    if not rows:
        return _empty('kb.spec 空 —— 干活线没有说明书可用。')
    return _rows(rows, size='%d 篇' % len(rows),
                 note='一行一篇，正文存在 body；检查项条数决定要不要过自检门。')


def _p_spec_conflict(cur):
    rows = _fetch(cur, 'select id, name, role_code, product, conflict_with from kb.spec '
                       'order by id')
    pairs = []
    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            if (a.get('role_code') or '', str(a.get('product') or '')) == \
                    (b.get('role_code') or '', str(b.get('product') or '')):
                pairs.append({'之一': a['name'], '另一篇': b['name'],
                              '角色': a.get('role_code') or '（空）',
                              '产物': a.get('product') or '（空）',
                              '标注字段': str(a.get('conflict_with') or b.get('conflict_with') or '（没标）')})
    if not pairs:
        return _empty('没有两篇说明书落在同一「角色 + 产物」上 —— 冲突标注不会出现。')
    return _rows(pairs, size='%d 组' % len(pairs),
                 note='冲突时仍只用挑中的那篇，但要求在交付里说明用了哪篇。')


def _p_spec_refs(cur):
    from app.secretary import spec as SP
    names = [r['name'] for r in _fetch(cur, 'select name from kb.spec order by id limit 8')]
    if not names:
        return _empty('没有说明书，谈不上《书名号》引用。')
    hit = SP.load(None, name=names[0]) or {}
    refs = hit.get('refs') or []
    if not refs:
        return _text('《%s》正文里没有《书名号》引用。' % names[0], size='0 条')
    return _rows([{'书名号里的名字': r.get('name'),
                   '能取到吗': '能' if r.get('resolved') else '取不到',
                   'spec_id': r.get('spec_id')} for r in refs],
                 size='%d 条' % len(refs),
                 note='模型读到正文后可以顺着 read_doc 接着读这几篇。')


def _p_session_files(cur):
    rows = _try_fetch(cur,
                      "select chat_session_id, count(*) as n from qa.t_chat_message "
                      "where deleted_flag=0 and meta_json is not null "
                      "and meta_json::text <> '{}' group by chat_session_id "
                      "order by count(*) desc limit 10",
                      "select chat_session_id, count(*) as n from qa.t_chat_message "
                      "group by chat_session_id limit 10")
    base = ('规则：本轮带的文件 + 本会话此前最多 6 个（qa_log.session_files），一律只给清单和 file_id，'
            '正文由模型 read_session_file 分页读。\n'
            '上一轮产出的文件也进清单 —— 下一轮说「这个文件」「再改一版」才找得回来。\n'
            '代码：loop.py:231-242（合并清单）、inject.py files_text（模板）')
    if not rows:
        return _text(base, size='规则')
    return _text(base + '\n\n## 最近的会话（带附件的消息条数）\n' + '\n'.join(
        '- %s：%s 条' % (r.get('chat_session_id'), r.get('n')) for r in rows[:8]))


def _p_inj_text(key, label):
    def probe(cur):
        from app.secretary import inject_store
        body = inject_store.get(key, user=None) or ''
        if not body.strip():
            return _text('（现在没有内容）cfg.inject_text 的 %s 是空的 —— %s 这一段不注入。\n'
                         '这一页可以就地填，存完 60 秒内生效。' % (key, label),
                         size='0 字 · 空 = 不注入')
        return _text(body)
    return probe


def _p_inj_menu(cur):
    from app.secretary import spec as SP
    txt = SP.menu_text(None)
    if not txt.strip():
        return _empty('说明书目录为空 —— system 里没有这一段。')
    return _text(txt, size='%d 行' % len(txt.splitlines()))


def _p_inj_summary(cur):
    from app.secretary import config
    rows = _fetch(cur, "select session_id, left(summary, 300) as summary, summary_upto_seq "
                       "from qa.t_chat_session where coalesce(summary,'') <> '' "
                       "order by last_msg_at desc nulls last limit 6")
    head = ('规则：每 %d 轮做一次摘要，上限 %d 字；会话总轮数 < %d 不做摘要；'
            '最近 %d 轮无论有没有被摘要覆盖都原样带着。\n'
            '问数线只记账、不注入；干活线注入。'
            % (config.SUMMARY_EVERY, config.SUMMARY_MAX_CHARS, config.SUMMARY_MIN_TURNS,
               config.SUMMARY_KEEP_RECENT))
    if not rows:
        return _text(head + '\n\n（现在没有任何会话有摘要。）', size='0 条')
    return _text(head + '\n\n## 最近的摘要\n' + '\n\n'.join(
        '【%s】%s' % (r.get('session_id'), r.get('summary')) for r in rows),
        size='%d 条' % len(rows))


def _p_inj_history(cur):
    from app.secretary import inject
    return _text(
        '最近 %d 轮原样进 system；每轮「上一轮问」截 200 字、「上一轮答」截 1200 字。\n'
        '超预算时先从最早一轮开始丢，只剩一轮还超就整段不注入。\n\n'
        '代码：inject.py history_text（模板）、inject.build（压缩顺序）'
        % inject.HISTORY_TURNS, size='%d 轮' % inject.HISTORY_TURNS)


def _p_inj_files(cur):
    from app.secretary import inject
    return _text(inject.files_text([{'name': '（示例）通知.docx', 'chars': 12000,
                                     'file_id': 'f_示例'}]) +
                 '\n\n上面是模板；真实清单来自本轮 doc_files + 本会话此前 6 个。\n'
                 '没有文件时这一段整段不注入。', size='模板')


def _p_inj_spec(cur):
    from app.secretary import inject, spec as SP
    row = _fetch(cur, 'select name from kb.spec order by id limit 1')
    if not row:
        return _empty('没有说明书 —— 本轮 system 里没有「本轮说明书」这一段。')
    hit = SP.load(None, name=row[0]['name']) or {}
    if not hit:
        return _empty('按名字取不到《%s》。' % row[0]['name'])
    return _text(inject.spec_block(hit),
                 note='用户没点名时这一段落空；命中哪篇由上一项决定。')


def _p_inj_tools(cur):
    from app.secretary import inject_store
    body = inject_store.get('tools_guide', user=None) or ''
    if not body.strip():
        return _empty('工具说明为空（cfg.inject_text.tools_guide）。')
    return _text(body)


def _p_inj_budget(cur):
    from app.secretary import inject_store
    budget = inject_store.budget()
    return _text(
        '常驻上限 %d 字（cfg.inject_budget.resident_chars）。超了按这个顺序压：\n'
        '1 先丢最早一轮历史\n2 只剩一轮还超 → 历史整段不注入\n'
        '3 目录收到 10 篇 → 再收到 3 篇\n4 还超 → 截摘要尾巴\n\n'
        '压了什么、压掉多少字，就是页面上的「超限压缩」。' % budget,
        size='%d 字' % budget)


def _p_sys_extra(cur):
    from app.secretary import loop
    return _text(_src(loop, '_system'),
                 note='这段由代码追加在 inject 之后：交付契约、这轮要交什么、没文件时的提示。')


def _p_user_msg(cur):
    return _text(
        '送进模型的 user 消息只有三段：\n\n'
        '【本轮要办的事】用户原话\n'
        '（名字被纠过时多一行）「系统按错字表与名称表已把业务对象名改对，读作：…」\n'
        '【今天】YYYY-MM-DD\n'
        '【自检要求】命中说明书且有检查项时，把检查项逐条列出来（原文，一项不改）\n\n'
        '代码：server/app/secretary/loop.py:275-285', size='3 段')


def _p_tools(cur):
    from app.secretary import loop
    parts = ['共 %d 只（干活线自己那一套，与问数线的工具不同）。' % len(loop.TOOLS)]
    for t in loop.TOOLS:
        f = t.get('function') or {}
        parts.append('## %s\n%s\n\nparameters: %s'
                     % (f.get('name'), str(f.get('description') or '').strip(),
                        json.dumps(f.get('parameters') or {}, ensure_ascii=False, indent=1)))
    return _text('\n\n'.join(parts), size='%d 只' % len(loop.TOOLS))


def _p_tool_gate(cur):
    return _text(
        '7 只工具里，read_session_file 只在有文件时给；没有文件时从表里删掉，模型想读也读不了。\n'
        '闲聊（lane=chat）一只都不给。\n\n'
        '代码：loop.py:286-288', size='7 只')


def _p_loop_params(cur):
    from app.secretary import config
    return _text(
        '模型 %s（通道 %s）\n'
        'temperature=0；每轮 think=False\n'
        '步数上限 max_steps=14（干活线默认，不是问数线的 40）\n'
        'max_tokens=%s（0 = 不带）\n'
        '没有单题时间预算，也没有「原地打转」判据 —— 只靠步数防死循环。\n'
        '用户点「停止」→ 已产出的文件不给出去。'
        % (config.MODEL, config.MODEL_CHANNEL, config.LLM_MAX_TOKENS or '不设'),
        size='6 项')


def _p_tool_result(cur):
    from app.secretary import config
    return _text(
        '工具结果回给模型分两条路（loop.py:324-328）：\n\n'
        'search_material / query_data / read_session_file → tools_result.deliver：单条上限 %d 字，'
        '超了不砍内容，落盘 + 给骨架，模型用 read_result 接着取。\n'
        'read_doc / make_deliverable / self_check / finish → 直接 JSON，截断到 6000 字。\n'
        '工具报错不抛出去，原样回给模型让它改参数重试。' % config.TOOL_RESULT_CHARS,
        size='%d 字/次' % config.TOOL_RESULT_CHARS)


def _p_q_data(cur):
    from app.secretary import config, semantic, tools_db
    metrics = _fetch(cur, 'select code, label, unit, expr from cfg.caliber_metric order by code')
    bases = _fetch(cur, 'select metric_code, table_name, column_name, agg, must '
                        'from cfg.caliber_base order by metric_code')
    try:
        allowed = sorted(semantic.allowed_tables())
    except Exception:                                 # noqa: BLE001
        allowed = []
    txt = ['## 指标契约（%d 条）\n' % len(metrics) + '\n'.join(
        '- %s：%s（%s）%s' % (m['code'], m['label'], m['unit'] or '', m['expr'] or '')
        for m in metrics),
        '\n## 口径底表（%d 条）\n' % len(bases) + '\n'.join(
            '- %s → %s.%s（%s）%s'
            % (b['metric_code'], b['table_name'], b['column_name'], b['agg'],
               json.dumps(b['must'], ensure_ascii=False)) for b in bases),
        '\n## safe_select（代码）\n' + _src(tools_db, 'safe_select'),
        '\n## 表白名单（现算 %d 张）\n%s' % (len(allowed), '、'.join(allowed) or '（空）'),
        '\n行数上限 SQL_MAX_ROWS=%d；超时 %d ms；只允许 select / with。'
        % (config.SQL_MAX_ROWS, config.SQL_TIMEOUT_MS)]
    return _text('\n'.join(txt), size='%d 指标 / %d 底表' % (len(metrics), len(bases)))


def _p_q_search(cur):
    from app.secretary import config
    return _text(
        'search_material = 在资料库检索制度、办法、流程、判据的原文切片。\n'
        '检索范围：知识库页上打了「参与智能问答」的库；config.KB_IDS 默认 %s'
        '（只有连外部知识库时才用）。\n'
        'top_k 默认 5；不返回说明书（说明书走 read_doc）。\n'
        '切片大小、embedding 模型、重排都在知识库页上配。' % (config.KB_IDS or '（空）'),
        size='1 只')


def _p_read_doc(cur):
    from app.secretary import tools_file
    return _text(
        'read_doc 两种取法：\n'
        '1 名字先在 kb.spec 里找 → 命中就返回说明书正文（版本、检查项、《书名号》引用），'
        '同时把「本轮说明书」记进执行上下文，出文件时按它套版式。\n'
        '2 找不到再按知识库文档名找 → 分页读。\n\n'
        '分页：一次 %d 块、单次正文上限 %d 字；next_offset 为 null 才算读完。'
        % (tools_file.DEFAULT_LIMIT, tools_file.MAX_CHARS), size='2 路')


def _p_read_file(cur):
    from app.secretary import tools_file
    return _text(
        'read_session_file = read_file：按块读本会话的文件，带章节路径与段号。\n'
        '一次 %d 块、正文上限 %d 字；next_offset 按「实际给出的块数」算，不按取回的块数。'
        % (tools_file.DEFAULT_LIMIT, tools_file.MAX_CHARS), size='1 只')


def _p_make_fmt(cur):
    from app.secretary import howto, tools_file
    prefs = _fetch(cur, 'select user_id, prefs from base.t_user_pref order by user_id')
    first = _fetch(cur, 'select name, fmt from kb.spec order by id limit 1')
    fmt_howto = ((first[0].get('fmt') if first else None)
                 or (howto.get('', 'docx') or {}).get('fmt') or {})
    prefs_txt = '；'.join('%s=%s' % (p['user_id'], json.dumps(p['prefs'], ensure_ascii=False))
                         for p in prefs) or '无'
    return _text(
        '版式三层，后一层只覆盖前一层里有值的字段：\n\n'
        '1 说明书 / 做法给的 fmt —— 现在《%s》给的是：%s\n'
        '2 个人偏好（base.t_user_pref，%d 人）：%s\n'
        '3 国标默认档\n\n'
        '## 国标默认（docx）\n%s\n\n## 国标默认（xlsx）\n%s'
        % ((first[0]['name'] if first else '无'),
           json.dumps(fmt_howto, ensure_ascii=False)[:400],
           len(prefs), prefs_txt,
           json.dumps(tools_file._DEFAULT_DOCX_FMT, ensure_ascii=False, indent=1),
           json.dumps(tools_file._DEFAULT_XLSX_FMT, ensure_ascii=False)),
        size='3 层')


def _p_make_gen(cur):
    from app.secretary import howto_build, office_runner, pipeline_deck
    scripts = []
    for p in (office_runner.DOCX_SCRIPTS, office_runner.PPTX_SCRIPTS,
              office_runner.XLSX_SCRIPTS):
        if p.is_dir():
            for f in sorted(p.glob('*.py')):
                n = len(f.read_text(encoding='utf-8', errors='replace').splitlines())
                scripts.append('- %s（%d 行）'
                               % (f.relative_to(office_runner.SKILLS_DIR), n))
    return _text(
        '三种生成器（模型只给内容，版式由脚本套）：\n'
        'docx → howto_build.DOCX_CODE（%d 字模板）+ inspect_docx.py 自检\n'
        'xlsx → howto_build.XLSX_CODE（%d 字模板）+ inspect_workbook.py 自检\n'
        'pptx → pipeline_deck._BUILD_CODE（%d 字模板）+ inspect_deck.py 自检\n\n'
        '跑脚本的解释器：SECRETARY_OFFICE_PYTHON（空 = 当前进程解释器）；单次超时 %d 秒；'
        'import 白名单；只认 in / scratch / out，产物只从 out/ 取。\n\n'
        '## 技能脚本（%d 个）\n%s'
        % (len(howto_build.DOCX_CODE), len(howto_build.XLSX_CODE),
           len(pipeline_deck._BUILD_CODE), office_runner.DEFAULT_TIMEOUT,
           len(scripts), '\n'.join(scripts) or '（没有脚本）'),
        size='%d 个脚本' % len(scripts))


def _p_make_selfcheck(cur):
    return _text(
        '出完文件立刻用技能包的 inspect 脚本读回来查：\n'
        'docx → inspect_docx.py；xlsx → inspect_workbook.py；pptx → inspect_deck.py\n'
        '判据只有一个：stdout 里出现「结论: 通过」才算通过，否则拦住交付、把尾巴回给模型。\n'
        '这一层是文件级自检（版式/结构），与下一段的「自检门」是两道不同的闸。\n\n'
        '代码：server/app/secretary/tools_file.py:307-315', size='3 个脚本')


def _p_gate(cur):
    from app.secretary import loop
    rows = _fetch(cur, "select name, check_items from kb.spec "
                       "where coalesce(jsonb_array_length(to_jsonb(check_items)), 0) > 0 "
                       "order by id")
    body = _src(loop, 'Gate')
    if rows:
        tail = '\n\n## 现在有检查项的说明书（%d 篇）\n%s' % (
            len(rows), '\n'.join('- 《%s》：%s'
                                 % (r['name'], '；'.join(str(x) for x in (r['check_items'] or [])))
                                 for r in rows))
    else:
        tail = '\n\n（现在没有说明书带检查项 —— 自检门不拦，只按默认三项提示。）'
    return _text(body + tail + '\n\n缺省三项（没检查项时用）：%s'
                 % '；'.join(loop.DEFAULT_CHECK), size='%d 篇' % len(rows))


def _p_artifacts(cur):
    from pathlib import Path
    base = Path('data/agent_out')
    dirs = []
    if base.is_dir():
        for d in sorted([p for p in base.iterdir() if p.is_dir()],
                        key=lambda p: p.stat().st_mtime, reverse=True)[:6]:
            dirs.append({'目录': str(d), '文件数': len(list(d.rglob('*.*'))),
                         '改动时间': time.strftime('%Y-%m-%d %H:%M',
                                                   time.localtime(d.stat().st_mtime))})
    return _text(
        '产物记账：本轮 make_deliverable 出的文件 → result.artifacts（名字、字节、file_id、'
        '下载地址、自检结论）；同时写进本会话文件清单，下一轮说「再改一版」能找回来。\n'
        '落盘：data/agent_out/<session>/f<时间戳>/out/；下载走 /api/files/<file_id>/download。\n'
        '用户点停止 → 已出的文件不交付。\n\n## 最近的产物目录\n%s'
        % ('\n'.join('- %s（%s 个文件，%s）' % (d['目录'], d['文件数'], d['改动时间'])
                     for d in dirs) or '（还没有）'),
        size='%d 个目录' % len(dirs))


def _p_answer(cur):
    return _text(
        '收尾文案的兜底顺序：模型给了正文 → 用它；有产物没正文 → 「文件已生成：<名字>」；'
        '模型调 finish 给了说明 → 用那句；都没有 → 「本轮没有给出内容。」；'
        '模型调用失败 → 「模型这次没答上来…可以说「重试」再来一次」。\n\n'
        '代码：server/app/secretary/loop.py:353-366', size='5 种')


# ── 链路（顺序 = loop.run 的执行顺序）────────────────────────────────
CHAIN = [
    {'key': 'route', 'name': '① 入口分流（判成干活）', 'items': [
        {'key': 'o_route_rule', 'name': '入口判据（cfg.route_rule）',
         'where': 'cfg.route_rule（JSON）', 'editable': False, 'takes_effect': '下一轮',
         'note': '产物词表 + 产出性动词 + 交付说法 + 指代文件的说法；判错整条线走错。',
         'editor': '', 'probe': _p_route_rule},
        {'key': 'o_route_decision', 'name': '判成干活的三种情形（代码）',
         'where': 'server/app/secretary/router.py', 'editable': False,
         'takes_effect': '改代码',
         'note': '要一份文件 / 本轮带了文件 / 这句话在指会话里的文件。',
         'editor': '', 'probe': _p_route_decision},
        {'key': 'o_product', 'name': '产物类型怎么定',
         'where': 'router.route → 说明书 product → 缺省 docx', 'editable': False,
         'takes_effect': '改代码', 'note': '决定 make_deliverable 的 kind。',
         'editor': '', 'probe': _p_product},
    ]},
    {'key': 'fix', 'name': '② 名称与错字纠错', 'items': [
        {'key': 'o_name_fix', 'name': '六张词典 + 纠错引擎',
         'where': 'cfg.t_nc_* + name_fix.py', 'editable': False, 'takes_effect': '60 秒',
         'note': '与问数线同一份词典；改完 60 秒内重载。', 'editor': '',
         'probe': _p_name_fix},
    ]},
    {'key': 'spec', 'name': '③ 说明书', 'items': [
        {'key': 'o_spec_menu', 'name': '说明书目录（30 行）',
         'where': 'kb.spec → spec.menu_text', 'editable': False, 'takes_effect': '下一轮',
         'note': '常驻的一段：让它知道有哪些做法、各自什么时候用。', 'editor': '',
         'probe': _p_spec_menu},
        {'key': 'o_spec_pick', 'name': '命中判据（挑哪一篇）',
         'where': 'kb.spec.pick', 'editable': False, 'takes_effect': '改代码',
         'note': '点名 > 适用词 > 本人 > 全局 > 最新版；判错整份都错。', 'editor': '',
         'probe': _p_spec_pick},
        {'key': 'o_spec_table', 'name': '全部说明书（一行一篇）',
         'where': 'kb.spec', 'editable': False, 'takes_effect': '下一轮',
         'note': '版本、归属、产物、检查项条数都在这里。', 'editor': '',
         'probe': _p_spec_table},
        {'key': 'o_spec_conflict', 'name': '冲突标注',
         'where': 'kb.spec.role_code / product / conflict_with', 'editable': False,
         'takes_effect': '下一轮',
         'note': '同一「角色 + 产物」的两篇：用哪篇、要不要在交付里说明。',
         'editor': '', 'probe': _p_spec_conflict},
        {'key': 'o_spec_refs', 'name': '正文里的《书名号》引用',
         'where': 'kb.spec.body → spec.resolve_refs', 'editable': False,
         'takes_effect': '下一轮', 'note': '顺着读得到的其它说明书。', 'editor': '',
         'probe': _p_spec_refs},
    ]},
    {'key': 'files', 'name': '④ 会话文件', 'items': [
        {'key': 'o_session_files', 'name': '会话文件清单（本轮 + 此前 6 个）',
         'where': 'qa.t_chat_message.meta_json', 'editable': False, 'takes_effect': '每轮',
         'note': '只给清单和 file_id，正文按需 read_session_file。', 'editor': '',
         'probe': _p_session_files},
    ]},
    {'key': 'system', 'name': '⑤ 装配 system（10 段）', 'items': [
        {'key': 'o_inj_identity', 'name': '身份',
         'where': 'cfg.inject_text.identity → 缺省 prompts_data.IDENTITY', 'editable': True,
         'takes_effect': '60 秒', 'note': '它是谁、站在谁的位置做活。',
         'editor': 'inject:identity', 'probe': _p_inj_text('identity', '身份')},
        {'key': 'o_inj_redlines', 'name': '红线（S0）',
         'where': 'cfg.inject_text.redlines', 'editable': True, 'takes_effect': '60 秒',
         'note': '不许做什么；空就不注入。', 'editor': 'inject:redlines',
         'probe': _p_inj_text('redlines', '红线')},
        {'key': 'o_inj_priority', 'name': '谁说了算（S1）',
         'where': 'cfg.inject_text.priority', 'editable': True, 'takes_effect': '60 秒',
         'note': '几份规则撞车时的取舍顺序。', 'editor': 'inject:priority',
         'probe': _p_inj_text('priority', '优先级')},
        {'key': 'o_inj_number', 'name': '数字纪律',
         'where': 'cfg.inject_text.number_discipline', 'editable': True,
         'takes_effect': '60 秒', 'note': '数字来源、单位、小数位的写法。',
         'editor': 'inject:number_discipline',
         'probe': _p_inj_text('number_discipline', '数字纪律')},
        {'key': 'o_inj_menu', 'name': '说明书目录（system 里那一段）',
         'where': 'spec.menu_text → inject.build', 'editable': False,
         'takes_effect': '下一轮', 'note': '超预算时先压到 10 篇、再压到 3 篇。',
         'editor': '', 'probe': _p_inj_menu},
        {'key': 'o_inj_summary', 'name': '会话摘要',
         'where': 'qa.t_chat_session.summary', 'editable': False, 'takes_effect': '每 N 轮',
         'note': '更早轮次的浓缩；问数线不注入，干活线注入。', 'editor': '',
         'probe': _p_inj_summary},
        {'key': 'o_inj_history', 'name': '前几轮对话',
         'where': 'qa.t_chat_message → inject.history_text', 'editable': False,
         'takes_effect': '每轮', 'note': '超预算先丢最早的那几轮。', 'editor': '',
         'probe': _p_inj_history},
        {'key': 'o_inj_files', 'name': '会话文件清单（system 里的模板）',
         'where': 'inject.files_text', 'editable': False, 'takes_effect': '每轮',
         'note': '没有文件时整段不出现。', 'editor': '', 'probe': _p_inj_files},
        {'key': 'o_inj_spec', 'name': '本轮说明书正文',
         'where': 'kb.spec.body → inject.spec_block', 'editable': False,
         'takes_effect': '每轮', 'note': '命中后才出现：正文 + 检查项 + 引用 + 冲突标注。',
         'editor': '', 'probe': _p_inj_spec},
        {'key': 'o_inj_tools', 'name': '工具说明',
         'where': 'cfg.inject_text.tools_guide → prompt_text.tools → 默认段',
         'editable': True, 'takes_effect': '60 秒',
         'note': '先检索还是先读文件、什么时候出文件。', 'editor': 'inject:tools_guide',
         'probe': _p_inj_tools},
    ]},
    {'key': 'budget', 'name': '⑥ 常驻预算与压缩', 'items': [
        {'key': 'o_inj_budget', 'name': '常驻上限与压缩顺序',
         'where': 'cfg.inject_budget.resident_chars', 'editable': False,
         'takes_effect': '下一轮', 'note': '超了先丢历史、再压目录、最后截摘要。',
         'editor': '', 'probe': _p_inj_budget},
    ]},
    {'key': 'extra', 'name': '⑦ 代码追加的骨架', 'items': [
        {'key': 'o_sys_extra', 'name': '代码追加到 system 的三段',
         'where': 'server/app/secretary/loop.py _system', 'editable': False,
         'takes_effect': '改代码', 'note': '交付契约 / 这轮要交什么 / 没文件时的提示。',
         'editor': '', 'probe': _p_sys_extra},
        {'key': 'o_user_msg', 'name': '送进模型的 user 消息',
         'where': 'loop.py:275-285', 'editable': False, 'takes_effect': '改代码',
         'note': '本轮要办的事 / 今天 / 自检要求。', 'editor': '', 'probe': _p_user_msg},
    ]},
    {'key': 'loop', 'name': '⑧ 工具与循环', 'items': [
        {'key': 'o_tools', 'name': '7 只工具定义',
         'where': 'server/app/secretary/loop.py TOOLS', 'editable': False,
         'takes_effect': '改代码', 'note': '模型看到的那份；description 决定它怎么动手。',
         'editor': '', 'probe': _p_tools},
        {'key': 'o_tool_gate', 'name': '哪几只给、哪几只不给',
         'where': 'loop.py:286-288', 'editable': False, 'takes_effect': '改代码',
         'note': '没文件就不给 read_session_file；闲聊一只都不给。', 'editor': '',
         'probe': _p_tool_gate},
        {'key': 'o_loop_params', 'name': '运行参数',
         'where': 'config.py + loop.py', 'editable': False,
         'takes_effect': '改 .env 并重启', 'note': '模型、步数 14、think=False、没有时间预算。',
         'editor': '', 'probe': _p_loop_params},
        {'key': 'o_tool_result', 'name': '工具结果怎么回给模型',
         'where': 'tools_result.deliver + 6000 字截断', 'editable': False,
         'takes_effect': '改配置', 'note': '决定它看得到几行、要不要 read_result 接着取。',
         'editor': '', 'probe': _p_tool_result},
    ]},
    {'key': 'tools', 'name': '⑨ 工具内部口径', 'items': [
        {'key': 'o_q_data', 'name': 'query_data（查数）',
         'where': '指标契约 + 口径底表 + safe_select + 表白名单', 'editable': False,
         'takes_effect': '下一轮', 'note': '干活线也能查数：代号优先，否则写 SQL。',
         'editor': '', 'probe': _p_q_data},
        {'key': 'o_q_search', 'name': 'search_material（检索资料）',
         'where': '知识库检索 + config.KB_IDS', 'editable': False, 'takes_effect': '下一轮',
         'note': '制度、办法、流程、判据的原文切片；不返回说明书。', 'editor': '',
         'probe': _p_q_search},
        {'key': 'o_read_doc', 'name': 'read_doc（读说明书 / 全文）',
         'where': 'spec.load + tools_file.kb_read', 'editable': False, 'takes_effect': '每轮',
         'note': '读到说明书就记「本轮说明书」，出文件按它套版式。', 'editor': '',
         'probe': _p_read_doc},
        {'key': 'o_read_file', 'name': 'read_session_file（读会话文件）',
         'where': 'tools_file.read_file', 'editable': False, 'takes_effect': '每轮',
         'note': '分页读，next_offset 按实际给出的块数算。', 'editor': '',
         'probe': _p_read_file},
        {'key': 'o_make_fmt', 'name': 'make_deliverable：版式从哪来',
         'where': '说明书 fmt → 个人偏好 → 国标默认', 'editable': False,
         'takes_effect': '下一轮', 'note': '字体、字号、行距、页边距、落款、页码。',
         'editor': '', 'probe': _p_make_fmt},
        {'key': 'o_make_gen', 'name': 'make_deliverable：生成器与技能脚本',
         'where': 'howto_build / pipeline_deck / skills/office/bisheng-*', 'editable': False,
         'takes_effect': '改代码', 'note': '模型只给内容，版式由脚本套。', 'editor': '',
         'probe': _p_make_gen},
        {'key': 'o_make_selfcheck', 'name': '生成后的文件级自检',
         'where': 'inspect_docx / inspect_workbook / inspect_deck', 'editable': False,
         'takes_effect': '改代码', 'note': '「结论: 通过」才放行，否则拦住交付。',
         'editor': '', 'probe': _p_make_selfcheck},
    ]},
    {'key': 'gate', 'name': '⑩ 自检门（交付开关）', 'items': [
        {'key': 'o_gate', 'name': '命中说明书的检查项 + Gate',
         'where': 'kb.spec.check_items + loop.Gate', 'editable': False,
         'takes_effect': '下一轮', 'note': '没报全通过就不给文件，整改后重报。',
         'editor': '', 'probe': _p_gate},
    ]},
    {'key': 'after', 'name': '⑪ 收尾', 'items': [
        {'key': 'o_artifacts', 'name': '产物记账与落盘',
         'where': 'result.artifacts + data/agent_out', 'editable': False,
         'takes_effect': '每轮', 'note': '名字、字节、file_id、自检结论；停止即不交付。',
         'editor': '', 'probe': _p_artifacts},
        {'key': 'o_answer', 'name': '回答兜底文案',
         'where': 'loop.py:353-366', 'editable': False, 'takes_effect': '改代码',
         'note': '模型没正文时给什么话。', 'editor': '', 'probe': _p_answer},
    ]},
]


def _index():
    return {it['key']: (seg, it) for seg in CHAIN for it in seg['items']}


def _run(seg, it, cur):
    try:
        return it['probe'](cur)
    except Exception as exc:                          # noqa: BLE001
        return {'kind': 'text', 'size': '读不到',
                'text': '（现在没有内容）\n\n读取失败：%s: %s'
                        % (type(exc).__name__, str(exc)[:300])}


def _meta(seg, it, p):
    return {'key': it['key'], 'seg': seg['name'], 'name': it['name'],
            'where': it['where'], 'size': p.get('size') or '',
            'editable': bool(it['editable']), 'takes_effect': it['takes_effect'],
            'note': it['note'], 'editor': it['editor'], 'kind': p.get('kind') or 'text'}


def segments():
    out = []
    try:
        conn = _conn()
    except Exception as exc:                          # noqa: BLE001
        return [{'key': 'error', 'name': '读不到配置库',
                 'items': [{'key': 'error', 'seg': '读不到配置库', 'name': '数据库连不上',
                            'where': 'PostgreSQL', 'size': '-', 'editable': False,
                            'takes_effect': '-', 'note': str(exc)[:200], 'editor': '',
                            'kind': 'text'}]}]
    try:
        cur = conn.cursor()
        try:
            for seg in CHAIN:
                out.append({'key': seg['key'], 'name': seg['name'],
                            'items': [_meta(seg, it, _run(seg, it, cur))
                                      for it in seg['items']]})
        finally:
            cur.close()
    finally:
        conn.close()
    return out


def body(key):
    idx = _index().get(key)
    if not idx:
        return {'error': '未知的项：%s' % key}
    seg, it = idx
    conn = _conn()
    try:
        cur = conn.cursor()
        try:
            p = _run(seg, it, cur)
        finally:
            cur.close()
    finally:
        conn.close()
    out = dict(_meta(seg, it, p))
    out.update({'kind': p.get('kind') or 'text', 'text': p.get('text') or '',
                'columns': p.get('columns') or [], 'rows': p.get('rows') or [],
                'hint': p.get('note') or ''})
    return out
