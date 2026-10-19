# -*- coding: utf-8 -*-
"""预警存储与查询服务：扫描落库、列表/详情、卡片、预警↔对话。

分工：
  · `secretary/alerts4.py` —— 纯判据计算（不写库）
  · 本文件                  —— 落库（PG `alert_item`）、权限过滤、卡片、会话绑定
  · `services/dispatch_service.py` —— 任务流转

三条纪律：
  1. 扫描幂等：`dedupe_key` 唯一，重复命中只更新 `last_seen`，不新增行。
  2. 一轮扫完，本轮没命中的 open 预警 → `resolved`（close_reason=resolved_by_data）。
  3. 权限只由账号现查（alert_scope），不接受调用方传范围；查不到一律关闸。
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import and_, case, func, or_, select, text
from sqlalchemy.orm import Session

from app import dispatch_rules as DR
from app.models.dispatch import AlertChatLink, AlertItem, AlertScanRun, DispatchTask
from app.secretary import alert_scope, alerts4
from app.secretary import alerts as A

logger = logging.getLogger(__name__)

LEVEL_ORDER = {'high': 0, 'mid': 1, 'low': 2}


# ─────────────────────────────────────────────────────────── 基础

def _q(sql, args=None, limit=800):
    return A._all(sql, args, limit=limit)


def _now():
    return datetime.now(timezone.utc)


def _today(today=None) -> str:
    return (today or time.strftime('%Y-%m-%d'))[:10]


def all_role_accounts() -> list[str]:
    """全部有效业务账号（有角色、未删除、未停用）。"""
    rows = _q('SELECT DISTINCT e.login_name AS a FROM t_employee e '
              'JOIN t_role_employee re ON re.employee_id=e.employee_id '
              'WHERE e.deleted_flag=0 AND e.disabled_flag=0 AND e.login_name IS NOT NULL',
              limit=500)
    return [str(r['a']) for r in rows if r.get('a')]


def _scope_key(sc) -> str:
    if sc['scope_type'] == 'station':
        return str(sc['station_ids'][0])
    if sc['scope_type'] == 'county':
        return str(sc.get('dept_id') or '')
    return 'city'


def visible_condition(sc):
    """把账号范围翻译成 SQL 条件（预警列表/详情共用）。"""
    conds = []
    if sc.get('scope_type') == 'station':
        conds.append(and_(AlertItem.scope_type == 'station',
                          AlertItem.scope_key.in_([str(x) for x in sc['station_ids']])))
    elif sc.get('scope_type') == 'county':
        conds.append(and_(AlertItem.scope_type == 'county',
                          AlertItem.scope_key == str(sc.get('dept_id'))))
    elif sc.get('scope_type') == 'city':
        conds.append(AlertItem.scope_type == 'city')
    if not conds:
        return None
    return and_(AlertItem.assignee_role == sc.get('role_code'), or_(*conds))


def _can_see(sc, alert) -> bool:
    if not sc.get('ok'):
        return False
    if alert.assignee_role and alert.assignee_role != sc.get('role_code'):
        return False
    if alert.scope_type == 'station':
        return alert.scope_key in [str(x) for x in sc.get('station_ids') or []]
    if alert.scope_type == 'county':
        return alert.scope_key == str(sc.get('dept_id'))
    return alert.scope_type == 'city'


# ─────────────────────────────────────────────────────────── 扫描

def scan(db: Session, accounts=None, cadence: str = 'daily',
         today: str | None = None, trigger: str = 'manual') -> dict:
    """跑一轮扫描：判据 → upsert `alert_item` → 关闭本轮未命中的 open 项。"""
    today = _today(today)
    seen_at = _now()
    run_key = '%s-%s-%s-%s' % (trigger, cadence, today, uuid.uuid4().hex[:8])
    run = AlertScanRun(run_key=run_key, trigger_type=trigger, cadence=cadence,
                       started_at=seen_at, status='running')
    db.add(run)
    db.flush()

    errors: list[dict] = []
    found = new = resolved = 0
    groups: dict[tuple, dict] = {}
    facts_cache: dict[tuple, dict] = {}
    scanned_subtypes: set[str] = set()

    try:
        accts = list(accounts or DR.scan_accounts() or all_role_accounts())
        # 批量解析：122 个账号 4 条 SQL（单账号 resolve 是 4 条/人，全量要 58 秒）
        scopes = alert_scope.resolve_many(accts)
        for acct in accts:
            sc = scopes.get(acct) or {'ok': False, 'error': 'resolve_failed', 'account': acct}
            if not sc.get('ok'):
                errors.append({'account': acct, 'error': sc.get('error') or 'resolve_failed'})
                continue
            role = sc['role_code']
            types = DR.enabled_types(role, cadence)
            if not types:
                continue
            scanned_subtypes.update(types)
            key = (role, sc['scope_type'], _scope_key(sc))
            if key not in groups:
                groups[key] = {'sc': sc, 'accounts': [], 'types': types}
            groups[key]['accounts'].append(acct)

        for (role, _st, _sk), g in groups.items():
            sc = g['sc']
            types = set(g.get('types') or [])
            need_metrics = any(t.startswith('metric.') for t in types)
            need_quality = 'project.amount_structure' in types
            need_mid = (A.R.forecast_mode() == 'pipeline'
                        and any(t.startswith('budget.') for t in types))
            need_core = any(t.startswith('budget.') or t == 'project.approval_stay'
                            for t in types)
            uses_facts = need_core or need_quality or need_metrics
            if uses_facts:
                ck = (tuple(sc['station_ids']), need_metrics, need_quality, need_mid, need_core)
                if ck not in facts_cache:
                    facts_cache[ck] = A._facts(sc['station_ids'], today[:4], today,
                                               need_quality=need_quality,
                                               need_metrics=need_metrics,
                                               need_mid=need_mid, need_core=need_core)
                facts = facts_cache[ck]
            else:
                facts = {}
            items, errs = alerts4.detect(role, sc, today, facts, cadence=cadence)
            errors.extend([dict(e, role=role, scope=sc.get('scope')) for e in errs])
            items = _post_filter(db, items)
            found += len(items)
            for a in items:
                got = _upsert(db, a, seen_at)
                new += 1 if got == 'new' else 0

        if scanned_subtypes and groups:
            # ⚠️ 必须先 flush：会话是 autoflush=False，不 flush 的话下面这条"本轮没命中"的查询
            #    看不到刚更新的 last_seen，会把**本轮明明命中的**预警也关掉（实测踩过：resolved=2）
            db.flush()
            # ⚠️ 只关"本轮真正扫过的那几个口径"的预警：
            #    单账号扫描（accounts=[x]）绝不能把别人的预警关掉（实测踩过：resolved=73）
            scope_conds = [and_(AlertItem.assignee_role == r,
                                AlertItem.scope_type == st,
                                AlertItem.scope_key == sk) for (r, st, sk) in groups]
            q = (select(AlertItem).where(AlertItem.status == 'open',
                                         AlertItem.last_seen < seen_at,
                                         AlertItem.subtype.in_(sorted(scanned_subtypes)),
                                         or_(*scope_conds)))
            stale = db.execute(q).scalars().all()
            for a in stale:
                a.status = 'resolved'
                a.closed_at = seen_at
                a.close_reason = 'resolved_by_data'
                resolved += 1

        run.finished_at = _now()
        run.status = 'done'
        run.found_n, run.new_n, run.resolved_n = found, new, resolved
        run.detail = {'accounts': len(accts), 'groups': len(groups),
                      'subtypes': sorted(scanned_subtypes),
                      'errors': errors[:50]}
        db.commit()
    except Exception as exc:  # noqa: BLE001 - 失败也要留痕
        db.rollback()
        logger.exception('预警扫描失败')
        try:
            run = db.get(AlertScanRun, run.id)
            if run:
                run.status = 'failed'
                run.finished_at = _now()
                run.error = '%s: %s' % (type(exc).__name__, str(exc)[:800])
                db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
        raise

    return {'run_id': run.id, 'run_key': run_key, 'cadence': cadence, 'today': today,
            'found': found, 'new': new, 'resolved': resolved,
            'accounts': len(accts), 'groups': len(groups), 'errors': errors[:50]}


def _post_filter(db: Session, items: list[dict]) -> list[dict]:
    """复核类：`review.not_reviewed` 要排除已经有复盘任务的项目。"""
    out = []
    for a in items:
        if a.get('subtype') != 'review.not_reviewed':
            out.append(a)
            continue
        ids = (a.get('data') or {}).get('candidate_ids') or []
        if not ids:
            continue
        reviewed = set()
        rows = db.execute(select(DispatchTask.biz_keys).where(
            DispatchTask.task_type == 'review', DispatchTask.deleted_flag == 0)).scalars().all()
        for keys in rows or []:
            for k in (keys or []):
                try:
                    reviewed.add(int(k))
                except (TypeError, ValueError):
                    continue
        left = [i for i in ids if int(i) not in reviewed]
        if not left:
            continue
        a['data'] = {**(a.get('data') or {}), 'candidate_ids': left, 'cnt': len(left)}
        a['title'] = a['title'].replace(str(len(ids)), str(len(left)))
        out.append(a)
    return out


def _upsert(db: Session, item: dict, seen_at: datetime) -> str:
    existing = db.execute(select(AlertItem).where(
        AlertItem.dedupe_key == item['dedupe_key'])).scalar_one_or_none()
    if existing is None:
        db.add(AlertItem(
            alert_type=item['alert_type'], subtype=item['subtype'], level=item['level'],
            dedupe_key=item['dedupe_key'], scope_type=item['scope_type'],
            scope_key=item['scope_key'], scope_name=item['scope_name'],
            object_type=item['object_type'], object_key=item['object_key'],
            title=item['title'], detail=item.get('detail') or '',
            data=item.get('data') or {}, verify_hint=item.get('verify_hint') or {},
            assignee_role=item.get('assignee_role'),
            first_seen=seen_at, last_seen=seen_at, seen_times=1, status='open',
        ))
        return 'new'

    keep_ai = (existing.data or {}).get('ai') if isinstance(existing.data, dict) else None
    data = dict(item.get('data') or {})
    if keep_ai:
        data['ai'] = keep_ai
    existing.level = item['level']
    existing.title = item['title']
    existing.detail = item.get('detail') or existing.detail
    existing.data = data
    existing.verify_hint = item.get('verify_hint') or existing.verify_hint
    existing.assignee_role = item.get('assignee_role') or existing.assignee_role
    existing.last_seen = seen_at
    existing.seen_times = int(existing.seen_times or 0) + 1
    if existing.status in ('resolved', 'closed'):
        existing.status = 'open'
        existing.closed_at = None
        existing.close_reason = None
    return 'updated'


# ─────────────────────────────────────────────────────────── 查询

def serialize(a: AlertItem, today: str | None = None) -> dict:
    today = _today(today)
    fs = a.first_seen.date() if a.first_seen else None
    ls = a.last_seen.date() if a.last_seen else None
    days_open = (ls - fs).days if (fs and ls) else 0
    return {
        'id': a.id,
        'type': a.alert_type,
        'category': a.alert_type,
        'category_name': DR.CATEGORY_NAMES.get(a.alert_type, a.alert_type),
        'subtype': a.subtype,
        'subtype_name': DR.name_of(a.subtype),
        'level': a.level,
        'title': a.title,
        'detail': a.detail or '',
        'scope_type': a.scope_type,
        'scope_key': a.scope_key,
        'scope_name': a.scope_name,
        'object_type': a.object_type,
        'object_key': a.object_key,
        'assignee_role': a.assignee_role,
        'status': a.status,
        'data': a.data or {},
        'verify_hint': a.verify_hint or {},
        'first_seen': a.first_seen.isoformat() if a.first_seen else None,
        'last_seen': a.last_seen.isoformat() if a.last_seen else None,
        'days_open': days_open,
        'is_new_today': bool(fs and fs.isoformat() == today),
        'seen_times': a.seen_times,
        'task_id': a.task_id,
        'closed_at': a.closed_at.isoformat() if a.closed_at else None,
        'close_reason': a.close_reason,
    }


def list_alerts(db: Session, account: str, category: str | None = None,
                status: str | None = None, level: str | None = None,
                subtype: str | None = None, limit: int = 50, offset: int = 0,
                today: str | None = None) -> dict:
    sc = alert_scope.resolve(account)
    base = {'account': account, 'scope': sc.get('scope'), 'scope_type': sc.get('scope_type'),
            'role_code': sc.get('role_code'), 'today': _today(today)}
    if not sc.get('ok'):
        return dict(base, status='unknown', total=0, items=[], error=sc.get('error'))
    cond = visible_condition(sc)
    if cond is None:
        return dict(base, status='ok', total=0, items=[])
    where = [cond]
    if category:
        where.append(AlertItem.alert_type == category)
    if subtype:
        where.append(AlertItem.subtype == subtype)
    if status:
        where.append(AlertItem.status == status)
    if level:
        where.append(AlertItem.level == level)
    total = db.execute(select(func.count()).select_from(AlertItem).where(*where)).scalar_one()
    rows = db.execute(
        select(AlertItem).where(*where)
        .order_by(case(LEVEL_ORDER, value=AlertItem.level, else_=9),
                  AlertItem.last_seen.desc())
        .limit(max(1, min(200, int(limit)))).offset(max(0, int(offset)))
    ).scalars().all()
    return dict(base, status='ok', total=total,
                items=[serialize(a, today) for a in rows])


def get_alert(db: Session, account: str, alert_id: int, today: str | None = None) -> dict | None:
    sc = alert_scope.resolve(account)
    if not sc.get('ok'):
        return None
    a = db.get(AlertItem, int(alert_id))
    if a is None or not _can_see(sc, a):
        return None
    out = serialize(a, today)
    tasks = db.execute(select(DispatchTask).where(
        DispatchTask.alert_id == a.id, DispatchTask.deleted_flag == 0)
        .order_by(DispatchTask.id.desc()).limit(10)).scalars().all()
    out['tasks'] = [{'id': t.id, 'task_no': t.task_no, 'title': t.title,
                     'status': t.status, 'level': t.level} for t in tasks]
    return out


# ─────────────────────────────────────────────────────────── 预警 ↔ 对话

def _msg_rows(db: Session, chat_sid: str):
    try:
        rows = db.execute(text(
            'SELECT id, seq_no, direction, sender_type, msg_type, content, meta_json, create_time '
            'FROM t_chat_message WHERE chat_session_id=:sid AND deleted_flag=0 ORDER BY seq_no'
        ), {'sid': chat_sid}).mappings().all()
        return [dict(r) for r in rows]
    except Exception:  # noqa: BLE001 - 会话表缺失时不阻断卡片返回
        db.rollback()
        logger.warning('t_chat_message 不可读（可能是新库未建会话表）')
        return None


def _ensure_session(db: Session, sc, alert: AlertItem) -> tuple[str | None, int | None]:
    """建会话（幂等：先查 link）。返回 (session_id, 内部主键)。"""
    emp_id = int(sc.get('employee_id') or 0)
    link = db.execute(select(AlertChatLink).where(
        AlertChatLink.alert_id == alert.id,
        AlertChatLink.employee_id == emp_id)).scalar_one_or_none()
    if link:
        row = db.execute(text('SELECT id FROM t_chat_session WHERE session_id=:s'),
                         {'s': link.session_id}).mappings().first()
        return link.session_id, (int(row['id']) if row else None)

    sid = 'alert-%d-%s' % (alert.id, uuid.uuid4().hex[:8])
    title = '【预警】%s %s' % (alert.scope_name or '', DR.name_of(alert.subtype))
    try:
        row = db.execute(text(
            'INSERT INTO t_chat_session (session_id, user_id, user_code, title, channel, '
            ' msg_count, status, last_msg_at) '
            "VALUES (:sid, :uid, :uc, :title, 'web', 0, 1, now()) RETURNING id"
        ), {'sid': sid, 'uid': emp_id or None, 'uc': sc.get('account'),
            'title': title[:255]}).mappings().first()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.warning('t_chat_session 不可写（新库未建会话表？）')
        return None, None
    skey = int(row['id'])
    db.add(AlertChatLink(alert_id=alert.id, employee_id=emp_id, session_id=sid))
    db.commit()
    return sid, skey


def _append(db: Session, skey: int, sid: str, sender_type: int, content: str,
            msg_type: int = 1, meta: dict | None = None) -> dict:
    seq = db.execute(text('SELECT COALESCE(MAX(seq_no),0) FROM t_chat_message WHERE session_id=:s'),
                     {'s': skey}).scalar_one()
    row = db.execute(text(
        'INSERT INTO t_chat_message (session_id, chat_session_id, seq_no, direction, sender_type, '
        ' msg_type, content, meta_json, gen_status, send_status) '
        'VALUES (:sk, :sid, :seq, :dir, :st, :mt, :content, CAST(:meta AS jsonb), 1, 3) '
        'RETURNING id, seq_no'
    ), {'sk': skey, 'sid': sid, 'seq': int(seq) + 1, 'dir': 2 if sender_type != 1 else 1,
        'st': sender_type, 'mt': msg_type, 'content': content,
        'meta': None if meta is None else json.dumps(meta, ensure_ascii=False)}
    ).mappings().first()
    db.execute(text('UPDATE t_chat_session SET msg_count = msg_count + 1, '
                    'last_msg_at = now() WHERE id=:s'), {'s': skey})
    db.commit()
    return {'id': int(row['id']), 'seq_no': int(row['seq_no']), 'sender_type': sender_type,
            'msg_type': msg_type, 'content': content, 'meta': meta}


def open_chat(db: Session, account: str, alert_id: int, today: str | None = None) -> dict | None:
    """点预警 → 进对话：复用会话，补卡片 + 首段话，返回会话与消息。"""
    sc = alert_scope.resolve(account)
    if not sc.get('ok'):
        return None
    alert = db.get(AlertItem, int(alert_id))
    if alert is None or not _can_see(sc, alert):
        return None

    from app.services import dispatch_ai

    card = serialize(alert, today)
    br = dispatch_ai.brief(card)
    sid, skey = _ensure_session(db, sc, alert)
    msgs = _msg_rows(db, sid) if sid else None
    if sid and skey and not msgs:
        _append(db, skey, sid, 3, '**%s**\n\n%s' % (card['title'], card['detail'] or ''),
                msg_type=2, meta={'kind': 'alert_card', 'alert_id': alert.id, 'card': card})
        _append(db, skey, sid, 2, br['markdown'], msg_type=1,
                meta={'kind': 'ai_brief', 'ai_used': br.get('ai_used', False)})
        msgs = _msg_rows(db, sid)
    return {
        'session_id': sid,
        'chat_store': 'ok' if sid else 'unavailable',
        'alert': card,
        'advice': br['advice'],
        'ai_used': br.get('ai_used', False),
        'messages': [
            {'seq_no': m.get('seq_no'), 'sender_type': m.get('sender_type'),
             'msg_type': m.get('msg_type'), 'content': m.get('content'),
             'meta': m.get('meta_json')}
            for m in (msgs or [])
        ] or [{'seq_no': 1, 'sender_type': 3, 'msg_type': 2, 'content': card['title'],
               'meta': {'kind': 'alert_card', 'alert_id': alert.id, 'card': card}},
              {'seq_no': 2, 'sender_type': 2, 'msg_type': 1, 'content': br['markdown'],
               'meta': {'kind': 'ai_brief', 'ai_used': br.get('ai_used', False)}}],
    }
