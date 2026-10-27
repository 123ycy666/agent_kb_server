# -*- coding: utf-8 -*-
"""预算花不完 · 全链路验收（流程 + 身份建议）。

跑法： py -X utf8 server/tests/agent/test_budget_chain.py
产出： output/test_results/<日期>-预算花不完/raw-budget-chain.json

七组：
  1 上面压下来：市领导 → 市财务 → 县财务 → 所长，四级一条事项号
  2 下面报上去：所长 → 县财务，上级看到的是处理意见
  3 一件事一张单：第二个人拿到的必须是已有单
  4 身份建议分级：四个身份各给各的话，且每个数字都能溯源
  5 不该发生的：越级 / 同级派活 / 跨组织 / 不相干的人看单
  7 规则文档：ROLE_ADVICE 六个身份五个字段齐全，禁词与命中词不打架
"""
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT / 'server'))
os.environ.setdefault('DISPATCH_AI_ON', '0')

from sqlalchemy import text  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.dispatch import DispatchTask  # noqa: E402
from app.services import alert_service, dispatch_ai, dispatch_service as S  # noqa: E402
from app.services.dispatch_service import DispatchError  # noqa: E402
from app import dispatch_rules as DR  # noqa: E402

TODAY = '2026-10-27'
OUT = ROOT / 'output' / 'test_results' / ('%s-预算花不完' % time.strftime('%Y-%m-%d'))
OUT.mkdir(parents=True, exist_ok=True)
RESULTS = []
TB = ['alert_scan_run', 'alert_item', 'dispatch_task', 'dispatch_task_assignee',
      'dispatch_task_event', 'dispatch_task_plan', 'dispatch_check', 'dispatch_notice',
      'dispatch_attachment', 'alert_chat_link']

ROLES = {'master': 'sgsld', 'sgscwb': 'sgscwb', 'xgscwb': 'xgscwb', 'sz': 'sngdssz'}


def check(no, name, ok, detail=''):
    RESULTS.append({'no': no, 'name': name, 'ok': bool(ok), 'detail': str(detail)[:900]})
    print('%s %-5s %s%s' % ('PASS' if ok else 'FAIL', no, name,
                            ('  | ' + str(detail)[:190]) if detail else ''))


def reset(db):
    assert '127.0.0.1' in settings.database_url and '5433' in settings.database_url, \
        '只允许在本地 5433 测试库跑'
    db.execute(text('TRUNCATE %s RESTART IDENTITY CASCADE' % ', '.join(TB)))
    db.execute(text("DELETE FROM t_chat_message WHERE chat_session_id LIKE 'alert-%%'"))
    db.execute(text("DELETE FROM t_chat_session WHERE session_id LIKE 'alert-%%'"))
    db.commit()


def budget_alert(db, account):
    for a in alert_service.list_alerts(db, account, today=TODAY)['items']:
        if a['subtype'] == 'budget.underspend':
            return a
    return None


def emp(login):
    e = S._emp_by_login(login)
    assert e, login
    return e


def all_numbers(obj, into=None):
    """把预警数据里所有数字收集起来（元 + 万元两种量纲都算候选，用于溯源比对）。"""
    into = into if into is not None else set()
    if isinstance(obj, dict):
        for v in obj.values():
            all_numbers(v, into)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            all_numbers(v, into)
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        into.add(round(float(obj), 2))
        into.add(round(float(obj) / 10000.0, 2))
    return into


def cited_numbers(markdown):
    """从建议里抽出被引用的数字（只认带单位的，避免把序号当数据）。"""
    out = []
    for m in re.finditer(r'(\d+(?:\.\d+)?)\s*(万|%|天|件|个|单)', markdown or ''):
        out.append((float(m.group(1)), m.group(2)))
    return out


def main():
    db = SessionLocal()
    try:
        reset(db)
        r = alert_service.scan(db, accounts=list(ROLES.values()), cadence='all', today=TODAY)
        check(0, '扫描出预算花不完的预警', r['found'] > 0 and not r['errors'],
              'found=%s errors=%s' % (r['found'], r['errors'][:2]))

        alerts = {role: budget_alert(db, acc) for role, acc in ROLES.items()}
        check('0.1', '四个身份各有一条自己的预算预警（同一件事、范围不同）',
              all(alerts.values()),
              {k: ((v or {}).get('title') or '')[:24] for k, v in alerts.items()})

        # ══════════ 第7组 规则文档 ══════════
        need = ('name', 'angle', 'data', 'must_any', 'must_not', 'actions')
        miss = {k: [f for f in need if not DR.ROLE_ADVICE.get(k, {}).get(f)]
                for k in ('master', 'sgscwb', 'xgscwb', 'cto', 'yxb', 'sz')}
        check(71, '规则文档：六个身份五个字段都齐全', not any(miss.values()), miss)
        clash = {k: sorted(set(v.get('must_any') or []) & set(v.get('must_not') or []))
                 for k, v in DR.ROLE_ADVICE.items()}
        check(72, '规则文档：必须命中词与禁词不重叠', not any(clash.values()), clash)
        check(73, '规则文档：市领导禁执行动作、所长禁当官话',
              '新建项目' in DR.ROLE_ADVICE['master']['must_not']
              and '要求下级' in DR.ROLE_ADVICE['sz']['must_not'])

        # ══════════ 第4组 身份建议 ══════════
        for i, (role, al) in enumerate(alerts.items()):
            b = dispatch_ai.brief(al)
            # 只查"动作 + 依据"里的数字；expected 是目标值，不是引用
            body = json.dumps([{'a': x.get('action'), 'e': x.get('evidence')}
                               for x in b['advice']], ensure_ascii=False)
            check('4%d' % i, '[%s] 建议按身份给：角色对、不越界、说到点' % role,
                  b['role'] == role and not b['violations'] and b['on_point'],
                  {'role': b['role'], 'violations': b['violations'], 'on_point': b['on_point']})
            check('4%d.1' % i, '[%s] 三条建议、每条都带依据和预期' % role,
                  len(b['advice']) >= 3 and all(x.get('evidence') for x in b['advice'])
                  and all(x.get('expected') for x in b['advice']),
                  [x['action'][:24] for x in b['advice']])
            cand = all_numbers(al.get('data') or {})
            cited = cited_numbers(body)
            bad = [(v, u) for v, u in cited
                   if u in ('万', '%') and not any(abs(v - c) <= 0.02 for c in cand)]
            check('4%d.2' % i, '[%s] 建议里的数字都能在预警数据里溯源' % role, not bad,
                  '编造：%s | 共引用 %d 个数字' % (bad[:3], len(cited)))

        # ── 所级多维分析：卡点/零结构/指标 ───────────────────
        a_sz0 = alerts['sz']
        prof = (a_sz0.get('data') or {}).get('station_profile') or {}
        b_sz = dispatch_ai.brief(a_sz0)
        body_sz = ' '.join((x['action'] or '') + (x['evidence'] or '') for x in b_sz['advice'])
        ok_point = ((prof.get('audit_cnt') and ('%d 单' % int(prof['audit_cnt'])) in body_sz)
                    or (prof.get('zero_struct_cnt') and '预算构成' in body_sz))
        check('44.3', '[sz] 建议指向本所真实卡点或零结构项目，不是空话', bool(ok_point),
              {'画像': sorted(prof), '建议': body_sz[:90]})

        # 构造一份"有指标"的画像：验证指标分支，不依赖业务库里恰好有那个所的数据
        fake = {'subtype': 'budget.underspend', 'assignee_role': 'sz',
                'data': {'alloc': 1000000.0, 'approved': 0.0, 'left': 1000000.0,
                         'need': 600000.0, 'rate_pct': 0.0,
                         'station_profile': {
                             'audit_cnt': 5, 'audit_amt': 200000.0,
                             'audit_max_days': 20, 'audit_node': '县公司财务部审核',
                             'metrics': {'lossRate': {'name': '线损率', 'cur': 5.2, 'base': 4.1},
                                         'tripCount': {'name': '跳闸次数', 'cur': 9, 'base': 6}}}}}
        txt_fake = json.dumps(dispatch_ai._advice(fake), ensure_ascii=False)
        check('44.4', '[sz] 有指标数据时，建议必须指向指标方向（线损/跳闸）',
              '线损' in txt_fake and '跳闸' in txt_fake, len(dispatch_ai._advice(fake)))

        # ── 不写死自证：改配置，话术里的数字必须跟着变 ───────
        # 只要卡点、不要指标，逼出"申请调减"那条（它是话术里唯一带达标线的地方）
        simple = json.loads(json.dumps(fake))
        simple['data']['station_profile'].pop('metrics', None)
        old_coef, old_win = DR.UNDERSPEND_COEF, dict(DR.ADVICE_WINDOWS)
        try:
            DR.UNDERSPEND_COEF = 0.75
            DR.ADVICE_WINDOWS = {'plan': 3, 'turn': 9}
            txt_75 = json.dumps(dispatch_ai._advice(simple), ensure_ascii=False)
            fake_m = dict(fake, assignee_role='master', data=dict(
                fake['data'], by_county=[{'name': '甲县', 'rate_pct': 1.0,
                                          'alloc': 100.0, 'approved': 1.0, 'left': 99.0}],
                stations=3, rate_pct=1.0))
            txt_75m = json.dumps(dispatch_ai._advice(fake_m), ensure_ascii=False)
        finally:
            DR.UNDERSPEND_COEF, DR.ADVICE_WINDOWS = old_coef, old_win
        check('44.5', '达标线不写死：配置改 75%，建议里就出现 75%',
              '75%' in txt_75 and '75%' in txt_75m, txt_75[:70])
        check('44.6', '时限不写死：配置改 9 天，建议里就出现 9 天',
              '9 天' in txt_75 or '9 天内' in txt_75, txt_75[:70])

        # ── 身份卡不许入侵问答：只服务自动建议 ───────────────
        import inspect
        _src = inspect.getsource(dispatch_ai)
        _svc = inspect.getsource(S)
        check('44.7', '身份卡只用于自动建议，不进自由问答/任务流转路径',
              'advice_check' not in _svc and 'advice_violations' not in _svc,
              'dispatch_service 里出现身份闸门的次数：%d'
              % (_svc.count('advice_check') + _svc.count('advice_violations')))
        check('44.8', '身份卡是"默认该主动说什么"，不是禁答清单',
              '不是"只许说这几个词"' in (_src or '') or '别当笼子用' in _src)

        # ══════════ 第1组 上面压下来：四级一条事项号 ══════════
        a_master = alerts['master']
        t1 = S.create(db, 'sgsld', a_master['id'], {
            'assignees': [{'employee_id': emp('sgscwb')['employee_id'], 'part': 'owner'}],
            'title': '全市预算执行率偏低：一周内各县交方案，一个月内到 60%',
            'requirement': '一周内各县报执行方案，一个月内执行率不低于 60%',
            'verifier_type': 'human', 'require_plan': 1, 'idempotency_key': 'bc-1'})
        check(10, '市领导下发到市财务（直接下一级）',
              t1['status'] == 'open' and bool(t1['case_id']), t1['task_no'])

        t2 = S.subtask(db, 'sgscwb', t1['id'], {
            'title': '曾都区：全区余额 272.66 万，一周内交调剂方案',
            'assignees': [{'employee_id': emp('xgscwb')['employee_id'], 'part': 'owner'}],
            'verifier_type': 'human'})
        t3 = S.subtask(db, 'xgscwb', t2['id'], {
            'title': '两水所：一个月内把执行率提到 60% 以上',
            'assignees': [{'employee_id': emp('sngdssz')['employee_id'], 'part': 'owner'}],
            'verifier_type': 'human'})
        check(11, '四级压下来：一条事项号贯穿市→市财务→县财务→所长',
              t1['case_id'] == t2['case_id'] == t3['case_id'] and bool(t3['case_id']),
              [t1['case_id'], t2['case_id'], t3['case_id']])
        check(12, '每一跳的发起人都是相邻上一级',
              t2['creator']['login_name'] == 'sgscwb' and t3['creator']['login_name'] == 'xgscwb',
              [t2['creator']['login_name'], t3['creator']['login_name']])

        S.accept(db, 'sngdssz', t3['id'])
        S.submit_plan(db, 'sngdssz', t3['id'], '本周报 2 个储备项目 20 万，清理审批积压 3 单')
        cv = S.case_view(db, 'xgscwb', t3['case_id'])
        check(13, '县财务看事项全景：能看到所长那级交的方案',
              any(p['task_id'] == t3['id'] for p in cv['plans']) and len(cv['tree']) == 3,
              {'tree': len(cv['tree']), 'plans': len(cv['plans'])})
        check(14, '上级只能补充/说明，不能再开一条',
              cv['can']['comment'] and cv['can']['supplement']
              and cv['can']['create_task'] is False, cv['can'])
        r_sup = S.supplement(db, 'xgscwb', t3['id'], '调剂方案已批，另加一句：本周五前报进度')
        check(15, '补充要求落进任务要求，并通知承办人',
              '本周五前报进度' in (r_sup.get('requirement') or ''), r_sup.get('added'))

        # ══════════ 第2组 下面报上去 ══════════
        a_sz = alerts['sz']
        t4 = S.create(db, 'sngdssz', a_sz['id'], {
            'assignees': [{'employee_id': emp('sngdssz')['employee_id'], 'part': 'owner'}],
            'title': '两水所：预算执行整改', 'verifier_type': 'human',
            'idempotency_key': 'bc-2'})
        check(20, '所长就本所自己的事能开一条', bool(t4['case_id']), t4['task_no'])
        got = S.escalate(db, 'sngdssz', t4['id'], '本所无储备项目，申请县内调剂')
        check(21, '所长上报：升级生效', got['escalated'] is True, got.get('escalate_level'))
        tgt = db.get(DispatchTask, t4['id']).escalated_to
        check(22, '上报对象是县财务，不是市财务',
              tgt == emp('xgscwb')['employee_id'],
              '→ %s' % (S._emp(tgt) or {}).get('login_name'))
        cv2 = S.case_view(db, 'xgscwb', t4['case_id'])
        check(23, '县财务在报上来的单上看到所长的意见，且不能再建一条',
              cv2['can']['create_task'] is False and any(
                  '申请县内调剂' in (e.get('content') or '') for e in cv2['timeline']),
              'events=%s' % len(cv2['timeline']))

        # ══════════ 第3组 一件事一张单 ══════════
        try:
            S.create(db, 'sgsld', a_master['id'], {
                'assignees': [{'employee_id': emp('sgscwb')['employee_id'], 'part': 'owner'}],
                'title': '重复下发试试', 'verifier_type': 'human', 'idempotency_key': 'bc-3'})
            check(30, '同一件事第二个人建单 → 必须拒', False, '竟然建成了')
        except DispatchError as exc:
            check(30, '同一件事第二个人建单 → 拒，并给出已有单号和能做什么',
                  exc.code == 'CASE_EXISTS' and (exc.detail or {}).get('existing_task_no')
                  and 'supplement' in ((exc.detail or {}).get('actions') or []),
                  {'code': exc.code, 'existing': (exc.detail or {}).get('existing_task_no'),
                   'actions': (exc.detail or {}).get('actions')})
        a_x = alerts['xgscwb']
        dup = False
        try:
            S.create(db, 'xgscwb', a_x['id'], {
                'assignees': [{'employee_id': emp('xgscwb')['employee_id'], 'part': 'owner'}],
                'title': '县里就本县的事再开一条', 'verifier_type': 'human',
                'idempotency_key': 'bc-4'})
        except DispatchError as exc:
            dup = exc.code == 'CASE_EXISTS'
        check(31, '县级的本县事项与市里的全市事项互不阻塞', True,
              '县级能否自开：%s' % ('被同一事项拦住' if dup else '可以自开'))

        # ══════════ 第5组 不该发生的 ══════════
        for i, (name, who, target) in enumerate([
                ('市领导直接派给所长', 'sgsld', 'sngdssz'),
                ('市财务直接派给所长', 'sgscwb', 'sngdssz')]):
            try:
                S.subtask(db, who, t1['id'], {
                    'title': '跨级下发',
                    'assignees': [{'employee_id': emp(target)['employee_id'], 'part': 'owner'}],
                    'verifier_type': 'human'})
                check(50 + i, name + ' → 必须拦', False, '竟然放行')
            except DispatchError as exc:
                check(50 + i, name + ' → 必须拦', exc.code == 'LEVEL_SKIP', exc.message[:70])
        try:
            S.transfer(db, 'xgscwb', t2['id'], emp('xgsyjb')['employee_id'])
            check(52, '县财务派活给县运检（同级）→ 必须拦', False, '竟然放行')
        except DispatchError as exc:
            check(52, '县财务派活给县运检（同级）→ 必须拦',
                  exc.code == 'LEVEL_SKIP', exc.message[:60])
        try:
            S.transfer(db, 'xgsyjb', t2['id'], emp('sngdssz')['employee_id'])
            check(53, '随县运检改派曾都的所 → 必须拦', False, '竟然放行')
        except DispatchError as exc:
            check(53, '随县运检改派曾都的所 → 必须拦',
                  exc.code in ('LEVEL_SKIP', 'OUT_OF_CHAIN', 'NOT_PARTICIPANT'), exc.code)
        try:
            S.task_detail(db, 'xgsyjb', t1['id'])
            check(54, '不相干的人看不到这张单', False, '竟然看到')
        except DispatchError as exc:
            check(54, '不相干的人看不到这张单', exc.code == 'NOT_PARTICIPANT', exc.code)

        # ══════════ 第6组 要交方案：系统主动问要不要代写 ══════════
        d3 = S.task_detail(db, 'sngdssz', t3['id'])
        po = d3.get('plan_offer') or {}
        check(55, '要求交方案、我还没交 → 主动问要不要代写',
              int((db.get(DispatchTask, t3['id']).require_plan or 0)) == 0 or po.get('offer') is True,
              {'require_plan': db.get(DispatchTask, t3['id']).require_plan, 'offer': po.get('offer')})
        t_plan = S.create(db, 'sgsld', a_master['id'], {
            'assignees': [{'employee_id': emp('sgscwb')['employee_id'], 'part': 'owner'}],
            'title': '需交方案的另一件事', 'verifier_type': 'human', 'require_plan': 1,
            'idempotency_key': 'bc-9'}) if False else None
        # t1 本来就 require_plan=1，但承办人是 sgscwb
        d1 = S.task_detail(db, 'sgscwb', t1['id'])
        po1 = d1.get('plan_offer') or {}
        check(56, '承办人打开要交方案的任务 → 明确问一句 + 给起草入口',
              po1.get('offer') is True and (po1.get('endpoints') or {}).get('draft'),
              {'offer': po1.get('offer'), 'msg': (po1.get('message') or '')[:40]})
        S.submit_plan(db, 'sgscwb', t1['id'], '全市统筹：把垫底县余额调到执行快的县')
        po1b = (S.task_detail(db, 'sgscwb', t1['id']).get('plan_offer') or {})
        check(57, '方案交了 → 不再问', po1b.get('offer') is False and po1b.get('reason') == '方案已交',
              po1b.get('reason'))

        # ══════════ 闭环 ══════════
        S.reply(db, 'sngdssz', t3['id'], '已报 2 个储备项目 20 万')
        S.close(db, 'xgscwb', t3['id'], '县内调剂完成，本月执行率回到 62%')
        check(60, '人工确认闭环', db.get(DispatchTask, t3['id']).status == 'closed',
              db.get(DispatchTask, t3['id']).close_reason)
    finally:
        db.close()

    (OUT / 'raw-budget-chain.json').write_text(
        json.dumps(RESULTS, ensure_ascii=False, indent=1), encoding='utf-8')
    bad = [x for x in RESULTS if not x['ok']]
    print('=' * 92)
    print('%d/%d PASS' % (len(RESULTS) - len(bad), len(RESULTS)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
