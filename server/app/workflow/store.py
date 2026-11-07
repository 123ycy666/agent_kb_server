"""存取：运行批次、结果（wf_item）、每人版本（wf_notify）、参数。存取规则全部在这里。

2026-11-07 定死：只写两份数据。过程证据在 `wf_run` / `wf_run_log`，不落流水表。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, select, text
from sqlalchemy.orm import Session

from app.core.database import SessionLocalWF, wf_engine as engine
from app.workflow.models import (
    DEFAULT_PARAMS, TABLES, Item, Notify, Param, RuleDoc, Run, RunLog,
)

_TZ = timezone(timedelta(hours=8))


def now() -> datetime:
    return datetime.now(_TZ)


def today() -> str:
    return now().strftime('%Y-%m-%d')


def _col(db: Session, table: str, col: str) -> bool:
    row = db.execute(text('SELECT 1 FROM information_schema.columns '
                          'WHERE table_name=:t AND column_name=:c'),
                     {'t': table, 'c': col}).first()
    return row is not None


def ensure_tables() -> None:
    from app.core.database import WFBase as Base
    with SessionLocalWF() as db:
        # 2026-11-07 换结构：流水表与通知关系表不再建；两张数据表按新列重建（旧数据是演示数据）
        db.execute(text('DROP TABLE IF EXISTS wf_result'))
        db.execute(text('DROP TABLE IF EXISTS wf_notify_item'))
        for t in ('wf_item', 'wf_notify'):
            if _col(db, t, 'dedupe_key') or _col(db, t, 'item_id'):
                db.execute(text('DROP TABLE IF EXISTS %s' % t))
        db.commit()
        # 2026-11-07：口径类 CHECK 全部撤出代码（句子长度、禁词、枚举都由运行参数与字典管）
        for c in ('ck_wf_item_type', 'ck_wf_notify_level', 'ck_wf_notify_type',
                  'ck_wf_notify_msg_len', 'ck_wf_notify_msg_tier', 'ck_wf_notify_msg_list',
                  'ck_wf_notify_alerts'):
            db.execute(text('ALTER TABLE IF EXISTS wf_item DROP CONSTRAINT IF EXISTS %s' % c))
            db.execute(text('ALTER TABLE IF EXISTS wf_notify DROP CONSTRAINT IF EXISTS %s' % c))
        db.commit()
    Base.metadata.create_all(bind=engine, tables=[t.__table__ for t in TABLES])
    with SessionLocalWF() as db:
        # 发送文档指回取数文档：一份取数只挂一份发送（9 对 9 由库卡住）
        db.execute(text('ALTER TABLE wf_doc ADD COLUMN IF NOT EXISTS pair_doc_id bigint'))
        db.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS uk_wf_doc_pair '
                        'ON wf_doc (pair_doc_id) WHERE pair_doc_id IS NOT NULL'))
        # 调度方案支持多个运行时间点
        db.execute(text('ALTER TABLE wf_schedule ADD COLUMN IF NOT EXISTS crons jsonb '
                        "NOT NULL DEFAULT '[]'::jsonb"))
        db.execute(text('INSERT INTO wf_doc_schedule (doc_id, schedule_id) '
                        'SELECT d.id, d.schedule_id FROM wf_doc d '
                        'WHERE d.schedule_id IS NOT NULL AND NOT EXISTS ('
                        '  SELECT 1 FROM wf_doc_schedule x WHERE x.doc_id = d.id '
                        '  AND x.schedule_id = d.schedule_id)'))
        db.execute(text('UPDATE wf_doc SET schedule_id = NULL WHERE schedule_id IS NOT NULL'))
        for k, (v, note) in DEFAULT_PARAMS.items():
            if db.get(Param, k) is None:
                db.add(Param(key=k, value=v, note=note))
        db.commit()


# ── 参数 ────────────────────────────────────────────────────────────────
def params(db: Session) -> dict:
    out = dict(DEFAULT_PARAMS)
    for p in db.execute(select(Param)).scalars().all():
        out[p.key] = (p.value, p.note)
    return out


def param(db: Session, key: str, default: str = '') -> str:
    p = db.get(Param, key)
    return (p.value if p and p.value is not None else default) or default


def param_int(db: Session, key: str, default: int = 0) -> int:
    try:
        return int(param(db, key, str(default)) or default)
    except (TypeError, ValueError):
        return default


def set_param(db: Session, key: str, value: str, note: str | None = None) -> None:
    p = db.get(Param, key)
    if p is None:
        db.add(Param(key=key, value=value, note=note))
    else:
        p.value = value
        if note is not None:
            p.note = note
    db.commit()


# ── 批次 ────────────────────────────────────────────────────────────────
def start_run(db: Session, doc: RuleDoc, trigger: str = 'manual', dry: bool = False) -> Run:
    import uuid
    key = '%s-%s-%s-%s' % (trigger, doc.id, today(), uuid.uuid4().hex[:6])
    run = Run(run_key=key, doc_id=doc.id, doc_name=doc.name, doc_version=doc.version,
              work_type=doc.work_type, trigger=trigger, status='running',
              dry_run=1 if dry else 0)
    db.add(run)
    db.commit()
    return run


def finish_run(db: Session, run: Run, **kw) -> None:
    for k, v in kw.items():
        if hasattr(run, k) and v is not None:
            setattr(run, k, v)
    run.finished_at = now()
    db.commit()


def log(db: Session, run: Run, kind: str, payload: dict) -> None:
    seq = (db.execute(select(func.max(RunLog.seq)).where(RunLog.run_id == run.id))
           .scalar_one_or_none() or 0) + 1
    db.add(RunLog(run_id=run.id, seq=int(seq), kind=kind[:16], payload=payload))
    db.commit()


# ── 结果（wf_item） ────────────────────────────────────────────────────
def write_item(db: Session, run: Run, doc: RuleDoc, *, object_key: str, object_name: str = '',
               hit: bool = False, unit_name: str = '', county_name: str = '',
               value: float | None = None, value_unit: str = '', threshold: float | None = None,
               tier: str | None = None, detail: dict | None = None,
               evidence: dict | None = None, reliable: bool = True) -> dict:
    if not hit:
        return {'ok': True, 'hit': False}
    cap = param_int(db, 'max_items_per_run', 200)
    if doc.work_type == 'detect':
        n = db.execute(select(func.count()).select_from(Item)
                       .where(Item.last_seen >= now() - timedelta(hours=6))).scalar_one()
        if int(n) >= cap:
            return {'ok': False, 'error': '近期已写满 %d 条（max_items_per_run）' % cap}
    return upsert_item(db, run, doc, object_key=object_key, object_name=object_name,
                       unit_name=unit_name, county_name=county_name, value=value,
                       value_unit=value_unit, threshold=threshold, tier=tier,
                       detail=detail, evidence=evidence, reliable=reliable)


def upsert_item(db: Session, run: Run, doc: RuleDoc, *, object_key: str, object_name: str = '',
                unit_name: str = '', county_name: str = '', value: float | None = None,
                value_unit: str = '', threshold: float | None = None, tier: str | None = None,
                detail: dict | None = None, evidence: dict | None = None,
                reliable: bool = True) -> dict:
    it = db.execute(select(Item).where(and_(Item.doc_id == doc.id,
                                            Item.object_key == str(object_key)[:160]))
                    ).scalar_one_or_none()
    ts = now()
    if it is None:
        it = Item(doc_id=doc.id, doc_name=doc.name, alert_type=doc.alert_type or '',
                  object_key=str(object_key)[:160], object_name=str(object_name)[:200],
                  unit_name=str(unit_name or '')[:120], county_name=str(county_name or '')[:120],
                  value=value, value_unit=str(value_unit or '')[:16], threshold=threshold,
                  tier=tier, detail=detail or {}, evidence=evidence or {},
                  reliable=1 if reliable else 0, status='open',
                  first_seen=ts, last_seen=ts)
        db.add(it)
        db.commit()
        return {'ok': True, 'state': 'new', 'item_id': it.id}

    it.doc_name = doc.name
    it.alert_type = doc.alert_type or it.alert_type
    it.object_name = str(object_name)[:200] or it.object_name
    it.unit_name = str(unit_name or '')[:120] or it.unit_name
    it.county_name = str(county_name or '')[:120] or it.county_name
    it.value, it.value_unit = value, str(value_unit or '')[:16]
    it.threshold, it.tier = threshold, tier
    it.detail = detail or it.detail
    it.evidence = evidence or it.evidence
    it.reliable = 1 if reliable else 0
    it.status = 'open'
    it.close_reason = None
    it.last_seen = ts
    db.commit()
    return {'ok': True, 'state': 'updated', 'item_id': it.id}


def close_missing(db: Session, run: Run, doc: RuleDoc, seen_keys: set) -> int:
    """本轮没出现的 open 结果 → 关闭（数据恢复）。"""
    rows = db.execute(select(Item).where(and_(Item.doc_id == doc.id,
                                              Item.status == 'open'))).scalars().all()
    n = 0
    for it in rows:
        if it.object_key not in seen_keys:
            it.status = 'closed'
            it.close_reason = '数据恢复'
            n += 1
    if n:
        db.commit()
    return n


def cleanup(db: Session) -> None:
    months = param_int(db, 'retention_months', 12)
    if months <= 0:
        return
    cut = now() - timedelta(days=30 * months)
    db.execute(text('DELETE FROM wf_run_log WHERE at < :c'), {'c': cut})
    db.commit()


# ── 每人版本（wf_notify） ──────────────────────────────────────────────
def recent_sent(db: Session, login: str, level: str, alert_type: str) -> bool:
    days = param_int(db, 'no_repeat_days', 1)
    if days <= 0:
        return False
    cut = now() - timedelta(days=days)
    row = db.execute(select(Notify).where(and_(
        Notify.recipient_login == login, Notify.level == level,
        Notify.alert_type == alert_type, Notify.sent_at >= cut))).scalars().first()
    return row is not None


def add_notify(db: Session, run: Run, doc: RuleDoc, *, level: str, scope_name: str,
               login: str, name: str = '', role: str = '', message: str,
               alert_ids: list[int], reason: str = '', channel: str = '') -> dict:
    ch = channel or param(db, 'send_channel_default', 'station')
    ids = [int(x) for x in (alert_ids or [])]
    n = Notify(level=level[:16], alert_type=doc.alert_type or '', scope_name=str(scope_name)[:120],
               recipient_login=str(login)[:64], recipient_name=(str(name)[:64] if name else None),
               role_code=(str(role)[:32] if role else None), message=str(message)[:200],
               alert_ids=ids, doc_id=doc.id, reason=reason, channel=ch[:16])
    db.add(n)
    db.commit()
    ts = now()
    for i in ids:
        it = db.get(Item, i)
        if it is not None:
            it.sent_at = ts
    db.commit()
    return {'ok': True, 'notify_id': n.id}


def list_items(db: Session, *, status: str | None = None, doc_id: int | None = None,
               alert_type: str | None = None, tier: str | None = None,
               level: str | None = None, county_name: str | None = None,
               unit_name: str | None = None, q: str | None = None,
               limit: int = 100, offset: int = 0) -> dict:
    where = []
    if status:
        where.append(Item.status == status)
    if doc_id:
        where.append(Item.doc_id == int(doc_id))
    if alert_type:
        where.append(Item.alert_type == alert_type)
    if tier:
        where.append(Item.tier == tier)
    if level == 'station' and unit_name:
        where.append(Item.unit_name.like('%%%s%%' % unit_name))
    if county_name:
        where.append(Item.county_name.like('%%%s%%' % county_name))
    if unit_name:
        where.append(Item.unit_name.like('%%%s%%' % unit_name))
    if q:
        like = '%%%s%%' % q
        where.append(Item.object_name.like(like) | Item.doc_name.like(like))
    total = db.execute(select(func.count()).select_from(Item).where(*where)).scalar_one()
    rows = db.execute(select(Item).where(*where).order_by(Item.last_seen.desc())
                      .limit(max(1, min(500, limit))).offset(max(0, offset))).scalars().all()
    return {'total': int(total), 'items': [item_dict(r) for r in rows]}


def item_dict(it: Item) -> dict:
    return {
        'id': it.id, 'doc_id': it.doc_id, 'doc_name': it.doc_name,
        'alert_type': it.alert_type, 'object_key': it.object_key,
        'object_name': it.object_name, 'unit_name': it.unit_name,
        'county_name': it.county_name, 'value': float(it.value) if it.value is not None else None,
        'value_unit': it.value_unit,
        'threshold': float(it.threshold) if it.threshold is not None else None,
        'tier': it.tier, 'reliable': bool(it.reliable), 'detail': it.detail or {},
        'evidence': it.evidence or {}, 'status': it.status, 'close_reason': it.close_reason,
        'first_seen': it.first_seen.isoformat() if it.first_seen else None,
        'last_seen': it.last_seen.isoformat() if it.last_seen else None,
        'sent_at': it.sent_at.isoformat() if it.sent_at else None,
        'task_id': it.task_id,
    }


def item_notifies(db: Session, item_id: int) -> list[dict]:
    rows = db.execute(text('SELECT id, level, scope_name, message, recipient_login, '
                           'recipient_name, role_code, sent_at, read_at FROM wf_notify '
                           'WHERE :i = ANY(alert_ids) ORDER BY id DESC LIMIT 50'),
                      {'i': int(item_id)}).mappings().all()
    return [dict(r) | {'sent_at': r['sent_at'].isoformat() if r['sent_at'] else None,
                       'read_at': r['read_at'].isoformat() if r['read_at'] else None}
            for r in rows]


def notify_dict(n: Notify) -> dict:
    return {
        'id': n.id, 'level': n.level, 'alert_type': n.alert_type, 'scope_name': n.scope_name,
        'recipient_login': n.recipient_login, 'recipient_name': n.recipient_name,
        'role_code': n.role_code, 'message': n.message, 'alert_ids': list(n.alert_ids or []),
        'alert_count': len(n.alert_ids or []), 'doc_id': n.doc_id,
        'sent_at': n.sent_at.isoformat() if n.sent_at else None,
        'read_at': n.read_at.isoformat() if n.read_at else None,
        'reason': n.reason, 'channel': n.channel,
    }


def list_notifies(db: Session, *, login: str | None = None, unread: bool = False,
                  alert_type: str | None = None, level: str | None = None,
                  limit: int = 100, offset: int = 0) -> dict:
    where = []
    if login:
        where.append(Notify.recipient_login == login)
    if unread:
        where.append(Notify.read_at.is_(None))
    if alert_type:
        where.append(Notify.alert_type == alert_type)
    if level:
        where.append(Notify.level == level)
    total = db.execute(select(func.count()).select_from(Notify).where(*where)).scalar_one()
    rows = db.execute(select(Notify).where(*where).order_by(Notify.sent_at.desc())
                      .limit(max(1, min(500, limit))).offset(max(0, offset))).scalars().all()
    unread_n = db.execute(select(func.count()).select_from(Notify)
                          .where(Notify.recipient_login == (login or ''))
                          .where(Notify.read_at.is_(None))).scalar_one() if login else 0
    return {'total': int(total), 'unread': int(unread_n),
            'items': [notify_dict(n) for n in rows]}


def mark_read(db: Session, notify_id: int, login: str) -> dict | None:
    n = db.get(Notify, int(notify_id))
    if n is None:
        return None
    if n.recipient_login != login:
        return {'forbidden': True}
    if n.read_at is None:
        n.read_at = now()
        db.commit()
    return {'ok': True, 'id': n.id,
            'read_at': n.read_at.isoformat() if n.read_at else None}


def overview(db: Session, login: str = '') -> dict:
    rows = db.execute(text("SELECT alert_type, count(*) FROM wf_item WHERE status='open' "
                           'GROUP BY 1')).all()
    tiers = dict((r[0] or '-', int(r[1])) for r in db.execute(
        text("SELECT tier, count(*) FROM wf_item WHERE status='open' GROUP BY 1")).all())
    unread = db.execute(select(func.count()).select_from(Notify)
                        .where(Notify.recipient_login == login)
                        .where(Notify.read_at.is_(None))).scalar_one() if login else 0
    last = db.execute(text("SELECT doc_name, finished_at, status, hit_n FROM wf_run "
                           "WHERE work_type='detect' ORDER BY id DESC LIMIT 1")).first()
    return {'alert_types': [{'alert_type': r[0], 'total': int(r[1])} for r in rows],
            'tiers': tiers, 'unread': int(unread),
            'last_run': ({'doc_name': last[0],
                          'finished_at': last[1].isoformat() if last[1] else None,
                          'status': last[2], 'hit_n': int(last[3] or 0)} if last else None)}


# ── 读结果（发送工种用） ──────────────────────────────────────────────
def read_items(db: Session, *, doc_name: str | None = None, doc_id: int | None = None,
               status: str = 'open', alert_type: str | None = None,
               tier: str | None = None, limit: int = 200, offset: int = 0,
               with_detail: bool = False) -> list[dict]:
    where = []
    if status:
        where.append(Item.status == status)
    if doc_id:
        where.append(Item.doc_id == int(doc_id))
    if doc_name:
        d = db.execute(select(RuleDoc).where(RuleDoc.name == doc_name)).scalars().first()
        if d is None:
            return []
        where.append(Item.doc_id == d.id)
    if alert_type:
        where.append(Item.alert_type == alert_type)
    if tier:
        where.append(Item.tier == tier)
    rows = db.execute(select(Item).where(*where)
                      .order_by(Item.tier, Item.value, Item.id)
                      .limit(max(1, min(500, limit))).offset(max(0, offset))).scalars().all()
    out = []
    for r in rows:
        d = item_dict(r)
        slim = {'id': d['id'], 'object_name': d['object_name'], 'unit_name': d['unit_name'],
                'county_name': d['county_name'], 'tier': d['tier'], 'value': d['value'],
                'value_unit': d['value_unit'], 'reliable': d['reliable'],
                'last_seen': (d['last_seen'] or '')[:10]}
        if with_detail:
            slim['detail'] = d['detail']
        out.append(slim)
    return out


# ── 会话（事项进对话） ─────────────────────────────────────────────────
def append_chat(db: Session, session_id: str, account: str, content: str,
                meta: dict | None = None, title: str = '') -> dict | None:
    """写一条会话消息：复用 t_chat_session / t_chat_message。表缺失时返回 None。"""
    import json as _json
    try:
        row = db.execute(text('SELECT id FROM t_chat_session WHERE session_id=:s'),
                         {'s': session_id}).mappings().first()
        if row is None:
            row = db.execute(text(
                'INSERT INTO t_chat_session (session_id, user_code, title, channel, '
                ' msg_count, status, last_msg_at) VALUES (:sid,:uc,:ti,\'web\',0,1,now()) '
                'RETURNING id'), {'sid': session_id, 'uc': account,
                                  'ti': (title or '')[:255]}).mappings().first()
        skey = int(row['id'])
        seq = db.execute(text('SELECT COALESCE(MAX(seq_no),0) FROM t_chat_message '
                              'WHERE session_id=:s'), {'s': skey}).scalar_one()
        db.execute(text(
            'INSERT INTO t_chat_message (session_id, chat_session_id, seq_no, direction, '
            ' sender_type, msg_type, content, meta_json, gen_status, send_status) '
            "VALUES (:sk,:sid,:seq,2,3,2,:c,CAST(:m AS jsonb),1,3)"),
            {'sk': skey, 'sid': session_id, 'seq': int(seq) + 1, 'c': content,
             'm': _json.dumps(meta or {}, ensure_ascii=False)})
        db.execute(text('UPDATE t_chat_session SET msg_count=msg_count+1, last_msg_at=now() '
                        'WHERE id=:s'), {'s': skey})
        db.commit()
        return {'session_id': session_id, 'seq_no': int(seq) + 1}
    except Exception:                       # noqa: BLE001
        db.rollback()
        return None

