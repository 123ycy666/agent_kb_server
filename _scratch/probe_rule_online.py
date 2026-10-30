# -*- coding: utf-8 -*-
"""线上只读探针：规则目录 / system 构成 / 个人规则状态。"""
import json
import sys

sys.path.insert(0, '/app')


def main() -> int:
    from app.secretary import agent, agent_v2, rule_doc, semantic, personal_kb

    topics = rule_doc.topics(force=True)
    menu = rule_doc.menu(force=True)
    print('规则主题数 =', len(topics))
    print('目录长度 =', len(menu))
    print(menu[:700])

    sys_text = agent_v2.system('data', agent._system, '2026-10-30',
                               priority=agent.RULES_PRIORITY,
                               answer_format=(agent.COMMON + agent.CHECK))
    print('\ndata system 长度 =', len(sys_text))
    print('含 S1 =', 'S1 谁说了算' in sys_text)
    print('含 S4 =', 'S4 作答格式' in sys_text)
    print('含 S6 规则目录 =', 'S6 规则目录' in sys_text)
    print('含 L3 静态口径（应为 False）=', 'L3 固定口径' in sys_text)
    print('含字段样例值（应为 False）=', '（例:' in sys_text)
    print('表清单长度 =', len(semantic.table_list_menu()))

    got = rule_doc.get('固定口径', force=True)
    print('\nget_rule(固定口径) 命中 =', bool(got.get('text')),
          '｜ 长度 =', len(got.get('text') or ''))
    miss = rule_doc.get('不存在的主题XYZ', force=True)
    print('get_rule(不存在) =', json.dumps(miss, ensure_ascii=False)[:160])
    pr = personal_kb.personal_rules(1)
    print('personal_rules(1) =', json.dumps(
        {'files': pr.get('files'), 'chars': pr.get('chars'), 'overflow': pr.get('overflow')},
        ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
