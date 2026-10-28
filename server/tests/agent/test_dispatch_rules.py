# -*- coding: utf-8 -*-
"""写死审计的守卫测试：**配置改了就跟着变**（交接红线 1）。

对应 `docs/04-计划/2026-10-28-写死审计清单.md`：
  A 组 角色词 → `DR.LEVEL_ROLE`
  B 组 达标线 → `DR.RATE_TARGET`
  C 组 时限   → `DR.REOPEN_DAYS` / `DR.DRAFT_DAYS` / `LEVEL_REPLY_HOURS` / `LEVEL_TARGET_DAYS`

跑法： py -X utf8 server/tests/agent/test_dispatch_rules.py
产出： output/test_results/<日期>-规则外置/raw-rules.json
"""
import json
import os
import sys
import time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve()
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT / 'server'))
os.environ.setdefault('DISPATCH_AI_ON', '0')

from sqlalchemy import select, text  # noqa: E402

from app import dispatch_rules as DR  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.dispatch import AlertItem, DispatchTask  # noqa: E402
from app.secretary import alert_scope, alerts4  # noqa: E402
from app.services import alert_service, dispatch_ai, dispatch_service as S  # noqa: E402
from app.services.dispatch_service import DispatchError  # noqa: E402

OUT = ROOT / 'output' / 'test_results' / ('%s-规则外置' % time.strftime('%Y-%m-%d'))
OUT.mkdir(parents=True, exist_ok=True)
RESULTS = []
TB = ['alert_scan_run', 'alert_item', 'dispatch_task', 'dispatch_task_assignee',
      'dispatch_task_event', 'dispatch_task_plan', 'dispatch_check', 'dispatch_notice',
      'dispatch_attachment', 'alert_chat_link']


def check(no, name, ok, detail=''):
    RESULTS.append({'no': no, 'name': name, 'ok': bool(ok), 'detail': str(detail)[:900]})
    print('%s %-5s %s%s' % ('PASS' if ok else 'FAIL', no, name,
                            ('  | ' + str(detail)[:190]) if detail else ''))


class patched:
    """临时改配置（退出时还原）。"""

    def __init__(self, mod, **kv):
        self.mod, self.kv, self.old = mod, kv, {}

    def __enter__(self):
        for k, v in self.kv.items():
            self.old[k] = getattr(self.mod, k)
            setattr(self.mod, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self.old.items():
            setattr(self.mod, k, v)
        return False


def reset(db):
    assert '127.0.0.1' in settings.database_url and '5433' in settings.database_url, \
        '只允许在本地 5433 测试库跑'
    db.execute(text('TRUNCATE %s RESTART IDENTITY CASCADE' % ', '.join(TB)))
    db.execute(text("DELETE FROM t_chat_message WHERE chat_session_id LIKE 'alert-%%'"))
    db.execute(text("DELETE FROM t_chat_session WHERE session_id LIKE 'alert-%%'"))
    db.commit()


def make_alert(db, *, scope, scope_key, scope_name, role, hint, title, dedupe,
               object_key='*'):
    alert_service._upsert(db, {
        'alert_type': 'budget', 'subtype': 'budget.underspend', 'level': 'high',
        'title': title, 'detail': '', 'data': {},
        'scope_type': scope, 'scope_key': scope_key, 'scope_name': scope_name,
        'object_type': 'station', 'object_key': object_key,
        'assignee_role': role, 'verify_hint': hint, 'dedupe_key': dedupe,
    }, S._now())
    db.flush()
    return db.execute(select(AlertItem).where(AlertItem.dedupe_key == dedupe)).scalar_one()


def main():
    # ══════════ A 组：角色词来自 DR.LEVEL_ROLE ══════════
    check('A1', 'DR.LEVEL_ROLE 三级齐全',
          DR.LEVEL_ROLE == {'station': 'sz', 'county': 'xgscwb', 'city': 'sgscwb'},
          DR.LEVEL_ROLE)

    st_alert = SimpleNamespace(scope_type='station', scope_key='42',
                               subtype='budget.underspend', data={}, level='high')
    with patched(DR, LEVEL_ROLE={'station': 'master', 'county': 'xgscwb', 'city': 'sgscwb'}):
        role, why = S._target_role(st_alert)
    check('A2', '改 LEVEL_ROLE → 所级应办角色跟着变（不再是写死的 sz）',
          role == 'master', {'role': role, 'why': why})

    act = {'scope': {'scope_type': 'county', 'dept_id': 10, 'station_ids': [42],
                     'scope': '曾都区供电公司'}}
    # 话里没有明确范围 → 落回发言人自己的范围，角色取 LEVEL_ROLE
    with patched(DR, LEVEL_ROLE={'station': 'sz', 'county': 'chief', 'city': 'sgscwb'}):
        sc_county = S._match_scope('这个月预算偏慢，怎么补', act)
    check('A3', '改 LEVEL_ROLE → 落回本级的角色跟着变',
          sc_county.get('role') == 'chief' and sc_county.get('scope_type') == 'county',
          {'role': sc_county.get('role'), 'scope': sc_county.get('scope_type')})

    # "全市" 这条分支同样取配置
    with patched(DR, LEVEL_ROLE={'station': 'sz', 'county': 'xgscwb', 'city': 'bigboss'}):
        sc_city = S._match_scope('全市这个月预算偏慢', act)
    check('A3.1', '改 LEVEL_ROLE → "全市"分支的角色也跟着变',
          sc_city.get('role') == 'bigboss',
          {'role': sc_city.get('role'), 'scope': sc_city.get('scope_type')})

    counties = S._county_depts()
    check('A4', '县公司清单来自站点包（不再写死随州四县）',
          len(counties) == 4 and {c[0] for c in counties} == {'曾都', '随县', '高新', '广水'}
          and {c[1] for c in counties} == {10, 11, 12, 13},
          counties)

    # ══════════ B 组：达标线来自 DR.RATE_TARGET ══════════
    sc_st = {'station_ids': [42]}
    with patched(DR, RATE_TARGET=0.75):
        rule = alerts4._rate_rule(sc_st, 2026)
    check('B1', '改 RATE_TARGET → 预警核销规则的 target 跟着变',
          rule.get('target') == 0.75, rule.get('target'))

    fake = {'subtype': 'budget.underspend', 'assignee_role': 'sz',
            'data': {'alloc': 1000000.0, 'approved': 100000.0, 'left': 900000.0,
                     'rate_pct': 10.0}}
    with patched(DR, RATE_TARGET=0.75):
        advice = json.dumps(dispatch_ai._advice(fake), ensure_ascii=False)
    check('B2', '改 RATE_TARGET → 建议话术里的达标线跟着变（出现 75%，不再是 60%）',
          '75%' in advice and '60%' not in advice, advice[:90])

    # ══════════ C 组：时限来自配置 + 库里的真实行为 ══════════
    db = SessionLocal()
    try:
        reset(db)
        sz_scope = alert_scope.resolve('sngdssz')
        cty_scope = alert_scope.resolve('xgscwb')
        cty_alert = make_alert(
            db, scope='county', scope_key='10', scope_name=cty_scope['scope'],
            role='xgscwb',
            hint={'kind': 'budget_rate', 'station_ids': cty_scope['station_ids'],
                  'op': '>=', 'target': 0.0},
            title='曾都区预算执行进度偏慢', dedupe='rules|county|10')
        st_alert2 = make_alert(
            db, scope='station', scope_key='42', scope_name=sz_scope['scope'], role='sz',
            hint={'kind': 'budget_rate', 'station_ids': sz_scope['station_ids'],
                  'op': '>=', 'target': 0.0},
            title='两水所预算执行进度偏慢', dedupe='rules|station|42')
        st_alert3 = make_alert(
            db, scope='station', scope_key='42', scope_name=sz_scope['scope'], role='sz',
            hint={'kind': 'budget_rate', 'station_ids': sz_scope['station_ids'],
                  'op': '>=', 'target': 0.0},
            title='两水所：草稿用例', dedupe='rules|station|42|d', object_key='draft')
        db.commit()

        me_cty = S._emp_by_login('xgscwb')
        parent = S.create(db, 'xgscwb', cty_alert.id, {
            'assignees': [{'employee_id': me_cty['employee_id'], 'part': 'owner'}],
            'title': '规则外置用例', 'require_reply': 1, 'verifier_type': 'human',
            'idempotency_key': 'rules-1'})
        parent_row = db.get(DispatchTask, parent['id'])

        # C1 子任务默认时限
        with patched(DR, LEVEL_REPLY_HOURS={'high': 5, 'mid': 72, 'low': 168},
                     LEVEL_TARGET_DAYS={'high': 2, 'mid': 30, 'low': 30}):
            child = S.subtask(db, 'xgscwb', parent_row.id, {
                'title': '子任务：改配置后默认时限',
                'assignees': [{'employee_id': S._emp_by_login('sngdssz')['employee_id'],
                               'part': 'owner'}]})
        child_row = db.get(DispatchTask, child['id'])
        now = S._now()
        reply_h = (child_row.reply_due_at - now).total_seconds() / 3600.0
        due_d = (child_row.due_at - now).total_seconds() / 86400.0
        check('C1', '子任务默认时限取 LEVEL_REPLY_HOURS / LEVEL_TARGET_DAYS（改 5 小时 / 2 天生效）',
              4.5 < reply_h < 5.5 and 1.5 < due_d < 2.5,
              {'reply_h': round(reply_h, 2), 'due_d': round(due_d, 2)})

        # C2 重开窗口（另开一张没有子任务的单）
        me_sz = S._emp_by_login('sngdssz')
        single = S.create(db, 'sngdssz', st_alert2.id, {
            'assignees': [{'employee_id': me_sz['employee_id'], 'part': 'owner'}],
            'title': '单独一张：重开窗口', 'verifier_type': 'human',
            'idempotency_key': 'rules-2'})
        single_row = db.get(DispatchTask, single['id'])
        S.accept(db, 'sngdssz', single_row.id)
        S.reply(db, 'sngdssz', single_row.id, '办完了')
        S.close(db, 'sngdssz', single_row.id, '闭环')
        with patched(DR, REOPEN_DAYS=-1):
            blocked = [True, '']
            try:
                S.reopen(db, 'sngdssz', single_row.id)
                blocked = [False, '竟然重开了']
            except DispatchError as exc:
                blocked = [exc.code == 'REOPEN_EXPIRED', exc.code]
        check('C2', '重开窗口取 REOPEN_DAYS（改成 -1 立刻过期 → REOPEN_EXPIRED）',
              blocked[0], blocked[1])

        # C3 草稿清理窗口
        draft = S.create(db, 'sngdssz', st_alert3.id, {
            'assignees': [{'employee_id': me_sz['employee_id'], 'part': 'owner'}],
            'title': '两天前的草稿', 'idempotency_key': 'rules-draft'})
        draft_row = db.get(DispatchTask, draft['id'])
        draft_row.status = 'draft'
        draft_row.create_time = S._now() - timedelta(days=2)
        db.commit()
        S.tick(db)                                  # 默认 7 天：两天前的草稿不该被清
        db.refresh(draft_row)
        keep = draft_row.status
        with patched(DR, DRAFT_DAYS=1):
            S.tick(db)
        db.refresh(draft_row)
        check('C3', '草稿清理窗口取 DRAFT_DAYS（默认 7 天不动它，改成 1 天清掉）',
              keep == 'draft' and draft_row.status == 'cancelled',
              {'默认7天': keep, '改1天后': draft_row.status})
    finally:
        db.rollback()
        db.close()

    (OUT / 'raw-rules.json').write_text(
        json.dumps(RESULTS, ensure_ascii=False, indent=1), encoding='utf-8')
    bad = [r for r in RESULTS if not r['ok']]
    print('=' * 90)
    print('%d/%d PASS' % (len(RESULTS) - len(bad), len(RESULTS)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
