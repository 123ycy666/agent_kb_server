# -*- coding: utf-8 -*-
"""表结构验收：10 张表 / 关键列 / 唯一键 / CHECK 约束。

跑法： py -X utf8 server/tests/agent/test_dispatch_schema.py
产出： output/test_results/<日期>-预警任务流转/raw-schema.json
"""
import json
import os
import sys
import time
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = Path(HERE).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'server'))
os.environ.setdefault('DISPATCH_AI_ON', '0')

from sqlalchemy import text  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402

TODAY = time.strftime('%Y-%m-%d')
OUT = ROOT / 'output' / 'test_results' / ('%s-预警任务流转' % TODAY)
OUT.mkdir(parents=True, exist_ok=True)
RESULTS = []

TABLES = {
    'alert_scan_run': ['run_key', 'cadence', 'found_n', 'new_n', 'resolved_n', 'detail'],
    'alert_item': ['alert_type', 'subtype', 'level', 'dedupe_key', 'scope_type', 'scope_key',
                   'scope_name', 'object_type', 'object_key', 'title', 'data', 'verify_hint',
                   'assignee_role', 'first_seen', 'last_seen', 'seen_times', 'status',
                   'task_id', 'closed_at', 'close_reason'],
    'dispatch_task': ['task_no', 'task_type', 'title', 'requirement', 'status', 'level',
                      'alert_id', 'root_task_id', 'parent_task_id', 'scope_type', 'scope_key',
                      'scope_name', 'biz_type', 'biz_keys', 'creator_id', 'creator_name',
                      'creator_login', 'creator_dept_id', 'creator_session_id', 'reply_due_at',
                      'due_at', 'require_plan', 'verifier_type', 'verify_rule', 'verify_state',
                      'last_check_at', 'escalate_level', 'overdue_flag', 'escalated_to',
                      'archived_flag', 'closed_at', 'close_reason', 'deleted_flag'],
    'dispatch_task_assignee': ['task_id', 'part', 'user_id', 'employee_id', 'login_name',
                               'name', 'dept_id', 'dept_name', 'role_code', 'state',
                               'session_id', 'accepted_at', 'replied_at', 'declined_reason'],
    'dispatch_task_event': ['task_id', 'action', 'actor_id', 'actor_name', 'actor_role',
                            'from_status', 'to_status', 'content', 'payload'],
    'dispatch_task_plan': ['task_id', 'kind', 'version', 'title', 'content', 'items',
                           'source', 'author_id', 'accepted', 'accepted_by', 'accepted_at'],
    'dispatch_check': ['task_id', 'check_kind', 'rule', 'measured', 'passed', 'message',
                       'check_time'],
    'dispatch_notice': ['task_id', 'notice_type', 'receiver_id', 'receiver_login',
                        'receiver_name', 'title', 'content', 'channel', 'sent_at',
                        'read_flag', 'read_at', 'dedupe_key'],
    'dispatch_attachment': ['task_id', 'event_id', 'file_name', 'storage_path',
                            'size_bytes', 'md5', 'uploader_id', 'uploader_name'],
    'alert_chat_link': ['alert_id', 'employee_id', 'session_id'],
}

INDEXES = ['uk_scan_run_key', 'uk_alert_dedupe', 'idx_alert_status', 'uk_task_no',
           'idx_task_status', 'uk_assignee', 'uk_plan_ver', 'idx_check_task',
           'uk_notice_dedupe', 'idx_notice_rcv', 'idx_attach_task', 'uk_alert_chat']

CHECKS = ['ck_alert_type', 'ck_alert_level', 'ck_task_status', 'ck_task_verifier',
          'ck_assignee_part', 'ck_assignee_state', 'ck_plan_kind']


def check(no, name, ok, detail=''):
    RESULTS.append({'no': no, 'name': name, 'ok': bool(ok), 'detail': str(detail)[:800]})
    print('%s %2s %s%s' % ('PASS' if ok else 'FAIL', no, name,
                           ('  | ' + str(detail)[:160]) if detail else ''))


def main():
    db = SessionLocal()
    try:
        # 1 表存在
        rows = db.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename = ANY(:ts)"
        ), {'ts': list(TABLES)}).scalars().all()
        miss = sorted(set(TABLES) - set(rows))
        check(1, '10 张表都存在', not miss, '缺：%s' % miss if miss else '10/10')

        # 2 关键列齐全
        cols = {}
        for r in db.execute(text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name = ANY(:ts)"),
                {'ts': list(TABLES)}).mappings():
            cols.setdefault(r['table_name'], set()).add(r['column_name'])
        bad = []
        for t, expect in TABLES.items():
            lost = [c for c in expect if c not in (cols.get(t) or set())]
            if lost:
                bad.append('%s 缺 %s' % (t, lost))
        check(2, '关键列齐全', not bad, '; '.join(bad) if bad else '10 张表列数 %s'
              % {t: len(cols.get(t) or []) for t in TABLES})

        # 3 关键索引/唯一键
        idx = set(db.execute(text(
            "SELECT indexname FROM pg_indexes WHERE schemaname='public'"
        )).scalars().all())
        miss_idx = [i for i in INDEXES if i not in idx]
        check(3, '唯一键/索引齐全', not miss_idx, '缺：%s' % miss_idx if miss_idx else '%d/%d'
              % (len(INDEXES), len(INDEXES)))

        # 4 CHECK 约束
        con = set(db.execute(text(
            "SELECT conname FROM pg_constraint WHERE contype='c' AND connamespace='public'::regnamespace"
        )).scalars().all())
        miss_ck = [c for c in CHECKS if c not in con]
        check(4, 'CHECK 约束齐全', not miss_ck, '缺：%s' % miss_ck if miss_ck else '%d/%d'
              % (len(CHECKS), len(CHECKS)))

        # 5 非法 alert_type 被拒
        ok = False
        try:
            db.execute(text(
                "INSERT INTO alert_item (alert_type, subtype, level, dedupe_key, scope_type, "
                "scope_key, scope_name, object_type, object_key, title) "
                "VALUES ('foo','x','high','test-bad','city','city','x','station','*','t')"))
            db.commit()
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            ok = 'ck_alert_type' in str(exc) or 'violates check constraint' in str(exc)
        check(5, '非法 alert_type 被 CHECK 拒绝', ok)

        # 6 重复 dedupe_key 被拒
        ok2 = False
        try:
            db.execute(text(
                "INSERT INTO alert_item (alert_type, subtype, level, dedupe_key, scope_type, "
                "scope_key, scope_name, object_type, object_key, title) "
                "VALUES ('budget','budget.underspend','high','test-dup','city','city','x','station','*','t')"))
            db.commit()
            db.execute(text(
                "INSERT INTO alert_item (alert_type, subtype, level, dedupe_key, scope_type, "
                "scope_key, scope_name, object_type, object_key, title) "
                "VALUES ('budget','budget.underspend','high','test-dup','city','city','x','station','*','t')"))
            db.commit()
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            ok2 = 'uk_alert_dedupe' in str(exc) or 'duplicate key' in str(exc)
        check(6, '重复 dedupe_key 被唯一键拒绝', ok2)
    finally:
        db.close()

    (OUT / 'raw-schema.json').write_text(json.dumps(RESULTS, ensure_ascii=False, indent=1),
                                         encoding='utf-8')
    fail = [r for r in RESULTS if not r['ok']]
    print('=' * 80)
    print('%d/%d PASS' % (len(RESULTS) - len(fail), len(RESULTS)))
    return 1 if fail else 0


if __name__ == '__main__':
    sys.exit(main())
