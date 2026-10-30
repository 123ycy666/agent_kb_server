# -*- coding: utf-8 -*-
"""提示词重构验收（2026-10-30）：表清单化 / 规则按需 / 个人规则注入。

钉死六条
--------
1. system 里**只有表清单**（不再有字段说明与样例值），且总长度 < 15000 字。
2. S1「谁说了算」与 S4「作答格式」确实进了 system。
3. 规则目录来自 `document.doc_type='rule'`，`get_rule(主题)` 能精确取到。
4. `get_rule(不存在的主题)` 明确报"没有这条规则"，不返回近似内容。
5. 个人规则：本人 `doc_type='rule'` 的文档被注入（≤1500 字）。
6. 隔离：别人的个人规则不会出现在我的结果里。

运行：py -X utf8 server/tests/agent/test_rule_stack.py
"""
from __future__ import annotations

import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SERVER))

_checks: list[tuple[str, bool, str]] = []
TAG = 'zz-test-rule-20261030'
KB_NAME = 'zz测试规则库-20261030'


def chk(label: str, cond: bool, extra: str = '') -> None:
    _checks.append((label, bool(cond), extra))


def _exec(sql, params=None):
    from app.secretary import pgdb
    conn = pgdb.connect(autocommit=True)
    try:
        cur = conn.cursor()
        cur.execute(sql, params or ())
        if str(sql).strip().lower().startswith('select'):
            return cur.fetchall() or []
        return []
    finally:
        conn.close()


def _add_doc(kb_id: int, filename: str, doc_type: str, owner: int, body: str) -> int:
    _exec(
        "INSERT INTO document (kb_id, version_no, is_active, filename, file_ext, file_size, "
        "storage_path, content_hash, parse_status, has_conflict, chunk_count, visibility, "
        "doc_type, owner_id) VALUES (%s,1,true,%s,'.md',10,'',%s,'ready',false,1,'private',%s,%s)",
        (kb_id, filename, TAG + filename, doc_type, owner))
    rows = _exec("SELECT id FROM document WHERE content_hash=%s ORDER BY id DESC LIMIT 1",
                 (TAG + filename,))
    did = int(rows[0]['id'])
    _exec(
        "INSERT INTO document_chunk (doc_id, kb_id, chunk_index, content, heading_path, "
        "embedding_model, tags, is_retrievable) "
        "VALUES (%s,%s,0,%s,'测试规则 > 审批停留','test/embed','{}',true)",
        (did, kb_id, '【测试规则 > 审批停留】\n' + body))
    return did


def _new_kb(owner: int) -> int:
    _exec(
        "INSERT INTO knowledge_base (name, description, embedding_model, embedding_dim, "
        "chunk_size, chunk_overlap, status, created_by, qa_enabled) "
        "VALUES (%s,'测试用','test/embed',1024,800,100,'active',%s,false)", (KB_NAME, owner))
    rows = _exec("SELECT id FROM knowledge_base WHERE name=%s AND created_by=%s "
                 "ORDER BY id DESC LIMIT 1", (KB_NAME, owner))
    return int(rows[0]['id'])


def _cleanup() -> None:
    try:
        _exec("DELETE FROM document_chunk WHERE kb_id IN "
              "(SELECT id FROM knowledge_base WHERE name=%s OR "
              "(name='我的资料库' AND created_by IN (901,902)))", (KB_NAME,))
        _exec("DELETE FROM document WHERE content_hash LIKE %s", (TAG + '%',))
        _exec("DELETE FROM knowledge_base WHERE name=%s OR "
              "(name='我的资料库' AND created_by IN (901,902))", (KB_NAME,))
    except Exception as exc:                       # noqa: BLE001
        print('  [清理跳过]', str(exc)[:120])


def main() -> int:
    print('提示词重构验收（真 PG）')
    from app.secretary import agent, agent_v2, rule_doc, personal_kb

    # ── 1/2 装配结构 ───────────────────────────────────────
    sys_text = agent_v2.system('data', agent._system, '2026-10-30',
                               priority=agent.RULES_PRIORITY,
                               answer_format=(agent.COMMON + agent.CHECK))
    chk('system 里含 S1 谁说了算', 'S1 谁说了算' in sys_text)
    chk('system 里含 S4 作答格式', 'S4 作答格式' in sys_text)
    chk('system 里只有表清单（无字段样例值）', '（例:' not in sys_text,
        '仍有字段样例值' if '（例:' in sys_text else '')
    chk('system 长度 < 15000', len(sys_text) < 15000, '%d 字' % len(sys_text))
    chk('system 提示了 describe_table', 'describe_table' in sys_text)

    # ── 3/4 规则按需（真 PG 夹具）────────────────────────────
    try:
        _cleanup()
        # 个人规则必须落在**本人的「我的资料库」**里（personal_rules 只认这个库）
        kb1 = personal_kb.ensure(901)
        _add_doc(kb1, '测试规则.md', 'rule', 901, '审批停留超过 90 天算积压（测试暗号 K9）')
        _add_doc(kb1, '普通资料.md', 'doc', 901, '这是一份资料，不该进规则目录（测试暗号 D9）')
        kb2 = personal_kb.ensure(902)
        _add_doc(kb2, '别人的规则.md', 'rule', 902, '别人的规则（测试暗号 O9）')
        rule_doc._CACHE['rows'] = None
    except Exception as exc:                       # noqa: BLE001
        print('  [跳过] 数据库不可用：%s' % str(exc)[:160])
        _report()
        return 1

    try:
        topics = rule_doc.topics(force=True)
        names = [t['topic'] for t in topics]
        chk('规则目录含测试主题', any('审批停留' in n for n in names), str(names[:5]))
        chk('资料文档不进规则目录',
            not any('普通资料' in (t.get('source') or '') for t in topics),
            str([t.get('source') for t in topics[:5]]))

        got = rule_doc.get('审批停留', force=True)
        chk('get_rule 精确取到正文', 'K9' in (got.get('text') or ''), str(got)[:120])
        miss = rule_doc.get('不存在的主题XYZ', force=True)
        chk('取不到时明确报错',
            bool(miss.get('error')) and '没有这条规则' in miss['error'], str(miss)[:120])
        chk('取不到时不返回近似正文', not miss.get('text'))

        # ── 5/6 个人规则 ───────────────────────────────────
        mine = personal_kb.personal_rules(901)
        chk('个人规则注入本人规则类文档', 'K9' in (mine.get('text') or ''), str(mine)[:120])
        chk('个人规则不含资料类文档', 'D9' not in (mine.get('text') or ''))
        chk('个人规则标注 S7', 'S7 我的规则' in (mine.get('text') or ''))
        other = personal_kb.personal_rules(902)
        chk('隔离：另一账号看不到我的规则', 'K9' not in (other.get('text') or ''),
            str(other)[:80])
    finally:
        _cleanup()

    return _report()


def _report() -> int:
    bad = [c for c in _checks if not c[1]]
    for label, ok, extra in _checks:
        print(('  [OK]   ' if ok else '  [FAIL] ') + label
              + (('   ← ' + extra) if (not ok and extra) else ''))
    print('\n%d 项检查，%d 项失败' % (len(_checks), len(bad)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
