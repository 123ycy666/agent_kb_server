# -*- coding: utf-8 -*-
"""任务流转接口：`/api/tasks/*`（人办的事）+ `/api/dispatch/*`（系统与看板）。

鉴权：用户 JWT；`/dispatch/tick` 用内部令牌（供外部定时任务调用，也可由服务内部循环调）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import dispatch_rules as DR
from app.core.database import get_db
from app.core.deps import CurrentActor, get_current_actor
from app.models.dispatch import AlertItem, DispatchCheck, DispatchNotice, DispatchTask
from app.schemas.dispatch import (
    CloseIn,
    DeclineIn,
    NoteIn,
    PlanIn,
    PolishIn,
    ReplyIn,
    SubtaskIn,
    TransferIn,
)
from app.secretary import config as sec_config
from app.services import alert_service, dispatch_ai, dispatch_service
from app.services.dispatch_service import DispatchError

router_tasks = APIRouter(prefix='/tasks', tags=['任务流转'])
router_dispatch = APIRouter(prefix='/dispatch', tags=['任务流转·系统'])


def _guard():
    if not DR.dispatch_on():
        raise HTTPException(status_code=503,
                            detail={'code': 'DISPATCH_OFF', 'message': '任务流转已关闭（DISPATCH_ON=0）'})


def _internal(request: Request):
    want = sec_config.KB_INTERNAL_TOKEN
    got = request.headers.get('X-Internal-Token') or ''
    if not want or got != want:
        raise HTTPException(status_code=401,
                            detail={'code': 'UNAUTHORIZED', 'message': '内部令牌不正确'})


def _fail(exc: DispatchError):
    raise HTTPException(status_code=exc.status,
                        detail={'code': exc.code, 'message': exc.message, 'detail': exc.detail})


def _alert_dict(db: Session, alert_id):
    if not alert_id:
        return None
    a = db.get(AlertItem, int(alert_id))
    return alert_service.serialize(a) if a is not None else None


# ══════════════════════════════════════════════════════════════
#  任务
# ══════════════════════════════════════════════════════════════

@router_tasks.get('', summary='我的任务（mine=参与/发起，created=我发起的，cc=抄送我的）')
def list_tasks(role: str = Query('mine', pattern='^(mine|created|cc|all)$'),
               status: str | None = Query(None, pattern='^(draft|open|doing|verify|closed|cancelled)$'),
               category: str | None = Query(None, pattern='^(budget|project|review|metric)$'),
               limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
               actor: CurrentActor = Depends(get_current_actor),
               db: Session = Depends(get_db)):
    _guard()
    try:
        return dispatch_service.list_tasks(db, actor.username, role=role, status=status,
                                           category=category, limit=limit, offset=offset)
    except DispatchError as exc:
        _fail(exc)


@router_tasks.get('/{task_id}', summary='任务详情（时间线/方案/核销记录/子任务）')
def get_task(task_id: int, actor: CurrentActor = Depends(get_current_actor),
             db: Session = Depends(get_db)):
    _guard()
    try:
        return dispatch_service.task_detail(db, actor.username, task_id)
    except DispatchError as exc:
        _fail(exc)


@router_tasks.post('/{task_id}/accept', summary='接单（open → doing）')
def accept(task_id: int, actor: CurrentActor = Depends(get_current_actor),
           db: Session = Depends(get_db)):
    _guard()
    try:
        return dispatch_service.accept(db, actor.username, task_id)
    except DispatchError as exc:
        _fail(exc)


@router_tasks.post('/{task_id}/decline', summary='拒接（留痕 + 升级）')
def decline(task_id: int, body: DeclineIn | None = None,
            actor: CurrentActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    _guard()
    try:
        return dispatch_service.decline(db, actor.username, task_id, (body.reason if body else ''))
    except DispatchError as exc:
        _fail(exc)


@router_tasks.post('/{task_id}/plan', summary='提交实施方案')
def submit_plan(task_id: int, body: PlanIn,
                actor: CurrentActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    _guard()
    try:
        return dispatch_service.submit_plan(db, actor.username, task_id, body.content, body.items)
    except DispatchError as exc:
        _fail(exc)


@router_tasks.post('/{task_id}/plan/{plan_id}/accept', summary='方案通过（发起人）')
def accept_plan(task_id: int, plan_id: int,
                actor: CurrentActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    _guard()
    try:
        return dispatch_service.accept_plan(db, actor.username, task_id, plan_id, True)
    except DispatchError as exc:
        _fail(exc)


@router_tasks.post('/{task_id}/plan/{plan_id}/reject', summary='方案驳回（发起人）')
def reject_plan(task_id: int, plan_id: int,
                actor: CurrentActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    _guard()
    try:
        return dispatch_service.accept_plan(db, actor.username, task_id, plan_id, False)
    except DispatchError as exc:
        _fail(exc)


@router_tasks.post('/{task_id}/reply', summary='提交回执（→ verify）')
def reply(task_id: int, body: ReplyIn,
          actor: CurrentActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    _guard()
    try:
        return dispatch_service.reply(db, actor.username, task_id, body.content, body.attachments)
    except DispatchError as exc:
        _fail(exc)


@router_tasks.post('/{task_id}/close', summary='人工核销（verify → closed）')
def close(task_id: int, body: CloseIn | None = None,
          actor: CurrentActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    _guard()
    try:
        b = body or CloseIn()
        return dispatch_service.close(db, actor.username, task_id, b.conclusion, b.reason)
    except DispatchError as exc:
        _fail(exc)


@router_tasks.post('/{task_id}/reopen', summary='重开（闭环 7 天内）')
def reopen(task_id: int, actor: CurrentActor = Depends(get_current_actor),
           db: Session = Depends(get_db)):
    _guard()
    try:
        return dispatch_service.reopen(db, actor.username, task_id)
    except DispatchError as exc:
        _fail(exc)


@router_tasks.post('/{task_id}/transfer', summary='转派')
def transfer(task_id: int, body: TransferIn,
             actor: CurrentActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    _guard()
    try:
        return dispatch_service.transfer(db, actor.username, task_id, body.to_employee_id)
    except DispatchError as exc:
        _fail(exc)


@router_tasks.post('/{task_id}/subtask', summary='继续下发（生成子任务）')
def subtask(task_id: int, body: SubtaskIn,
            actor: CurrentActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    _guard()
    try:
        return dispatch_service.subtask(db, actor.username, task_id,
                                        body.model_dump(exclude_none=True))
    except DispatchError as exc:
        _fail(exc)


@router_tasks.post('/{task_id}/urge', summary='手动催办')
def urge(task_id: int, body: NoteIn | None = None,
         actor: CurrentActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    _guard()
    try:
        return dispatch_service.urge(db, actor.username, task_id, (body.note if body else ''))
    except DispatchError as exc:
        _fail(exc)


@router_tasks.post('/{task_id}/supervise', summary='督办（通知，不改状态）')
def supervise(task_id: int, body: NoteIn | None = None,
              actor: CurrentActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    _guard()
    try:
        return dispatch_service.supervise(db, actor.username, task_id, (body.note if body else ''))
    except DispatchError as exc:
        _fail(exc)


@router_tasks.post('/{task_id}/escalate', summary='手动上报（升级一级）')
def escalate(task_id: int, body: NoteIn | None = None,
             actor: CurrentActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    _guard()
    try:
        return dispatch_service.escalate(db, actor.username, task_id, (body.note if body else ''))
    except DispatchError as exc:
        _fail(exc)


@router_tasks.post('/{task_id}/cancel', summary='撤回（留痕）')
def cancel(task_id: int, body: NoteIn | None = None,
           actor: CurrentActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    _guard()
    try:
        return dispatch_service.cancel(db, actor.username, task_id, (body.note if body else ''))
    except DispatchError as exc:
        _fail(exc)


@router_tasks.post('/{task_id}/archive', summary='归档（closed 后）')
def archive(task_id: int, actor: CurrentActor = Depends(get_current_actor),
            db: Session = Depends(get_db)):
    _guard()
    try:
        return dispatch_service.archive(db, actor.username, task_id)
    except DispatchError as exc:
        _fail(exc)


# ── AI 挂点 ────────────────────────────────────────────────────

@router_tasks.post('/{task_id}/ai/advice', summary='执行侧"怎么干"建议')
def ai_advice(task_id: int, actor: CurrentActor = Depends(get_current_actor),
              db: Session = Depends(get_db)):
    _guard()
    try:
        d = dispatch_service.task_detail(db, actor.username, task_id)
    except DispatchError as exc:
        _fail(exc)
    return dispatch_ai.plan_brief(d, _alert_dict(db, d.get('alert_id')))


@router_tasks.post('/{task_id}/ai/plan', summary='实施方案草稿（执行人改后提交）')
def ai_plan(task_id: int, actor: CurrentActor = Depends(get_current_actor),
            db: Session = Depends(get_db)):
    _guard()
    try:
        d = dispatch_service.task_detail(db, actor.username, task_id)
    except DispatchError as exc:
        _fail(exc)
    b = dispatch_ai.plan_brief(d, _alert_dict(db, d.get('alert_id')))
    return {'draft': '\n'.join(b['steps']), 'markdown': b['markdown'], 'ai_used': b['ai_used']}


@router_tasks.post('/{task_id}/ai/report', summary='上报报告（Markdown；docx 后续接）')
def ai_report(task_id: int, actor: CurrentActor = Depends(get_current_actor),
              db: Session = Depends(get_db)):
    _guard()
    try:
        d = dispatch_service.task_detail(db, actor.username, task_id)
    except DispatchError as exc:
        _fail(exc)
    return dispatch_ai.report_markdown(d, _alert_dict(db, d.get('alert_id')))


@router_tasks.post('/{task_id}/ai/polish', summary='润色（只改措辞，不新增数字）')
def ai_polish(task_id: int, body: PolishIn,
              actor: CurrentActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    _guard()
    try:
        dispatch_service.task_detail(db, actor.username, task_id)   # 权限校验
    except DispatchError as exc:
        _fail(exc)
    return dispatch_ai.polish(body.content)


# ══════════════════════════════════════════════════════════════
#  系统 / 看板
# ══════════════════════════════════════════════════════════════

@router_dispatch.post('/tick', summary='催办/升级/自动核销（内部令牌，幂等）')
def tick(request: Request, db: Session = Depends(get_db)):
    _guard()
    _internal(request)
    try:
        return dispatch_service.tick(db)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500,
                            detail={'code': 'TICK_FAILED', 'message': '%s: %s' % (type(exc).__name__, exc)})


@router_dispatch.get('/stats', summary='统计：状态/类别/闭环率/平均处置时长/升级率')
def stats(actor: CurrentActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    _guard()
    return dispatch_service.stats(db)


@router_dispatch.get('/notices', summary='我的通知（?unread=1）')
def notices(unread: bool = False, limit: int = Query(50, ge=1, le=200),
            actor: CurrentActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    _guard()
    try:
        return dispatch_service.list_notices(db, actor.username, unread=unread, limit=limit)
    except DispatchError as exc:
        _fail(exc)


@router_dispatch.post('/notices/{notice_id}/read', summary='标记通知已读')
def read_notice(notice_id: int, actor: CurrentActor = Depends(get_current_actor),
                db: Session = Depends(get_db)):
    _guard()
    try:
        return dispatch_service.read_notice(db, actor.username, notice_id)
    except DispatchError as exc:
        _fail(exc)


@router_dispatch.get('/health', summary='任务流转健康检查（表存在/待办数/逾期数）')
def health(db: Session = Depends(get_db)):
    try:
        n_alerts = db.execute(select(func.count()).select_from(AlertItem)).scalar_one()
        n_tasks = db.execute(select(func.count()).select_from(DispatchTask)).scalar_one()
        n_open = db.execute(select(func.count()).select_from(DispatchTask).where(
            DispatchTask.status.in_(('open', 'doing', 'verify')))).scalar_one()
        n_overdue = db.execute(select(func.count()).select_from(DispatchTask).where(
            DispatchTask.overdue_flag == 1)).scalar_one()
        n_notice = db.execute(select(func.count()).select_from(DispatchNotice)).scalar_one()
        n_check = db.execute(select(func.count()).select_from(DispatchCheck)).scalar_one()
        return {'ok': True, 'tables': 10, 'alerts': n_alerts, 'tasks': n_tasks,
                'open_tasks': n_open, 'overdue': n_overdue,
                'notices': n_notice, 'checks': n_check, 'dispatch_on': DR.dispatch_on()}
    except Exception as exc:  # noqa: BLE001
        return {'ok': False, 'error': '%s: %s' % (type(exc).__name__, str(exc)[:300])}


def health_summary() -> dict:
    """给 /health 用的无依赖版本（自己开会话）。"""
    from app.core.database import SessionLocal
    db = SessionLocal()
    try:
        return health(db)
    finally:
        db.close()
