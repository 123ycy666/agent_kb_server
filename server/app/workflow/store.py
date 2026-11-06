"""存取：运行批次、结果流水、事项、发送记录、参数。存取规则全部在这里。"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, select, text
from sqlalchemy.orm import Session

from app.core.database import engine
from app.workflow.models import (
    DEFAULT_PARAMS, TABLES, CheckResult, Item, Notify, Param, RuleDoc, Run, RunLog,
)

_TZ = timezone(timedelta(hours=8))


def now() -> datetime:
    return datetime.now(_TZ)


def today() -> str:
    return now().strftime('%Y-%m-%d')


def ensure_tables() -> None:
    from app.core.database import Base
    Base.metadata.create_all(bind=engine, tables=[t.__table__ for t in TABLES])
    with Session(bind=engine) as db:
        # 2026-11-06：调度方案支持多个运行时间点（8 点跑一次、9 点再跑一次）
        db.execute(text('ALTER TABLE wf_schedule ADD COLUMN IF NOT EXISTS crons jsonb '
                        "NOT NULL DEFAULT '[]'::jsonb"))
        # 老数据：把 wf_doc.schedule_id 的单值关系搬进多对多表
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


# ── 结果流水 + 事项 ─────────────────────────────────────────────────────
def write_result(db: Session, run: Run, doc: RuleDoc, *, object_key: str, object_name: str = '',
                 scope_type: str | None = None, scope_key: str | None = None,
                 value: float | None = None, threshold: float | None = None,
                 op: str | None = None, tier: str | None = None, hit: bool = False,
                 detail: dict | None = None, evidence: dict | None = None,
                 reliable: bool = True, title: str = '', body: str | None = None) -> dict:
    cap = param_int(db, 'max_items_per_run', 200)
    done = db.execute(select(func.count()).select_from(CheckResult)
                      .where(CheckResult.run_id == run.id)).scalar_one()
    if int(done) >= cap:
        return {'ok': False, 'error': '本轮已写满 %d 条（max_items_per_run）' % cap}
    if run.dry_run:
        return {'ok': True, 'dry': True}

    row = CheckResult(run_id=run.id, doc_id=doc.id, object_key=str(object_key)[:160],
                      object_name=str(object_name)[:200], scope_type=scope_type,
                      scope_key=(str(scope_key)[:64] if scope_key is not None else None),
                      value=value, threshold=threshold, op=op, tier=tier, hit=bool(hit),
                      detail=detail or {}, evidence=evidence or {},
                      reliable=1 if reliable else 0)
    db.add(row)
    db.commit()
    if hit:
        return upsert_item(db, run, doc, object_key=object_key, object_name=object_name,
                           scope_type=scope_type, scope_key=scope_key, value=value,
                           threshold=threshold, op=op, tier=tier, detail=detail,
                           evidence=evidence, reliable=reliable, title=title, body=body)
    return {'ok': True, 'hit': False}


def upsert_item(db: Session, run: Run, doc: RuleDoc, *, object_key: str, object_name: str = '',
                scope_type: str | None = None, scope_key: str | None = None,
                value: float | None = None, threshold: float | None = None,
                op: str | None = None, tier: str | None = None, detail: dict | None = None,
                evidence: dict | None = None, reliable: bool = True,
                title: str = '', body: str | None = None) -> dict:
    key = '%s|%s' % (doc.id, object_key)
    it = db.execute(select(Item).where(Item.dedupe_key == key)).scalar_one_or_none()
    ts = now()
    if it is None:
        it = Item(dedupe_key=key, doc_id=doc.id, alert_type=doc.alert_type or '',
                  object_key=str(object_key)[:160], object_name=str(object_name)[:200],
                  scope_type=scope_type, scope_key=(str(scope_key) if scope_key else None),
                  value=value, threshold=threshold, op=op, tier=tier,
                  detail=detail or {}, evidence=evidence or {}, reliable=1 if reliable else 0,
                  title=(title or object_name or str(object_key))[:300], body=body,
                  status='open', first_seen=ts, last_seen=ts, seen_times=1,
                  run_id_last=run.id)
        db.add(it)
        db.commit()
        return {'ok': True, 'state': 'new', 'item_id': it.id}

    it.object_name = str(object_name)[:200] or it.object_name
    it.value, it.threshold, it.op, it.tier = value, threshold, op, tier
    it.detail = detail or it.detail
    it.evidence = evidence or it.evidence
    it.reliable = 1 if reliable else 0
    if title:
        it.title = title[:300]
    if body:
        it.body = body
    it.status = 'open'
    it.close_reason = None
    it.last_seen = ts
    it.seen_times = int(it.seen_times or 0) + 1
    it.run_id_last = run.id
    db.commit()
    return {'ok': True, 'state': 'updated', 'item_id': it.id}


def close_missing(db: Session, run: Run, doc: RuleDoc, seen_keys: set) -> int:
    """本轮没出现的 open 事项 → 关闭（数据恢复）。"""
    if run.dry_run:
        return 0
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
    db.execute(text('DELETE FROM wf_result WHERE created_at < :c'), {'c': cut})
    db.commit()


# ── 发送 ────────────────────────────────────────────────────────────────
def recent_sent(db: Session, item_id: int, login: str) -> bool:
    days = param_int(db, 'no_repeat_days', 1)
    if days <= 0:
        return False
    cut = now() - timedelta(days=days)
    row = db.execute(select(Notify).where(and_(Notify.item_id == item_id,
                                               Notify.recipient_login == login,
                                               Notify.ok == 1,
                                               Notify.sent_at >= cut))).scalars().first()
    return row is not None


def add_notify(db: Session, run: Run, item_id: int, login: str, name: str = '',
               reason: str = '', channel: str = '', ok: bool = True, error: str = '') -> dict:
    ch = channel or param(db, 'send_channel_default', 'station')
    n = Notify(item_id=item_id, recipient_login=str(login)[:64],
               recipient_name=(str(name)[:64] if name else None), channel=ch[:16],
               reason=reason, ok=1 if ok else 0, error=error or None,
               run_id=run.id, doc_version=run.doc_version)
    db.add(n)
    db.commit()
    if ok and item_id:
        it = db.get(Item, item_id)
        if it is not None:
            it.sent_at = now()
            db.commit()
    return {'ok': bool(ok), 'notify_id': n.id}


def list_items(db: Session, *, status: str | None = None, doc_id: int | None = None,
               alert_type: str | None = None, tier: str | None = None,
               scope_key: str | None = None, q: str | None = None,
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
    if scope_key:
        where.append(Item.scope_key == str(scope_key))
    if q:
        like = '%%%s%%' % q
        where.append(Item.title.like(like) | Item.object_name.like(like))
    total = db.execute(select(func.count()).select_from(Item).where(*where)).scalar_one()
    rows = db.execute(select(Item).where(*where).order_by(Item.last_seen.desc())
                      .limit(max(1, min(500, limit))).offset(max(0, offset))).scalars().all()
    return {'total': int(total), 'items': [item_dict(r) for r in rows]}


def item_dict(it: Item) -> dict:
    return {
        'id': it.id, 'doc_id': it.doc_id, 'alert_type': it.alert_type,
        'object_key': it.object_key, 'object_name': it.object_name,
        'scope_type': it.scope_type, 'scope_key': it.scope_key,
        'value': it.value, 'threshold': it.threshold, 'op': it.op, 'tier': it.tier,
        'detail': it.detail or {}, 'evidence': it.evidence or {},
        'reliable': bool(it.reliable), 'title': it.title, 'body': it.body,
        'status': it.status, 'close_reason': it.close_reason,
        'first_seen': it.first_seen.isoformat() if it.first_seen else None,
        'last_seen': it.last_seen.isoformat() if it.last_seen else None,
        'seen_times': it.seen_times,
        'sent_at': it.sent_at.isoformat() if it.sent_at else None,
        'notify_count': 0,
    }


def item_history(db: Session, item: Item, limit: int = 30) -> list[dict]:
    rows = db.execute(select(CheckResult).where(and_(CheckResult.doc_id == item.doc_id,
                                                     CheckResult.object_key == item.object_key))
                      .order_by(CheckResult.id.desc()).limit(limit)).scalars().all()
    return [{'at': r.created_at.isoformat() if r.created_at else None, 'value': r.value,
             'threshold': r.threshold, 'tier': r.tier, 'hit': bool(r.hit),
             'run_id': r.run_id} for r in rows]


def item_notifies(db: Session, item_id: int) -> list[dict]:
    rows = db.execute(select(Notify).where(Notify.item_id == item_id)
                      .order_by(Notify.id.desc()).limit(50)).scalars().all()
    return [{'id': n.id, 'login': n.recipient_login, 'name': n.recipient_name,
             'channel': n.channel, 'reason': n.reason, 'ok': bool(n.ok), 'error': n.error,
             'sent_at': n.sent_at.isoformat() if n.sent_at else None} for n in rows]


def list_notifies(db: Session, limit: int = 100, offset: int = 0) -> dict:
    total = db.execute(select(func.count()).select_from(Notify)).scalar_one()
    rows = db.execute(select(Notify).order_by(Notify.id.desc())
                      .limit(max(1, min(500, limit))).offset(max(0, offset))).scalars().all()
    return {'total': int(total), 'items': [
        {'id': n.id, 'item_id': n.item_id, 'login': n.recipient_login, 'name': n.recipient_name,
         'channel': n.channel, 'reason': n.reason, 'ok': bool(n.ok), 'error': n.error,
         'sent_at': n.sent_at.isoformat() if n.sent_at else None} for n in rows]}


# ── 读事项（发��工种用） ────────────────────────────────────────────────
def read_items(db: Session, *, status: str = 'open', doc_id: int | None = None,
               alert_type: str | None = None, tier: str | None = None,
               limit: int = 200) -> list[dict]:
    where = []
    if status:
        where.append(Item.status == status)
    if doc_id:
        where.append(Item.doc_id == int(doc_id))
    if alert_type:
        where.append(Item.alert_type == alert_type)
    if tier:
        where.append(Item.tier == tier)
    rows = db.execute(select(Item).where(*where).order_by(Item.last_seen.desc())
                      .limit(max(1, min(500, limit)))).scalars().all()
    return [item_dict(r) for r in rows]


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
