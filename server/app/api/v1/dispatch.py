# -*- coding: utf-8 -*-
"""任务流转接口：`/api/tasks/*`（人办的事）+ `/api/dispatch/*`（系统与看板）。

鉴权：用户 JWT；`/dispatch/tick` 用内部令牌（供外部定时任务调用，也可由服务内部循环调）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import dispatch_rules as DR
from app.core.database import get_db
from app.core.deps import CurrentActor, get_current_actor
from app.models.dispatch import AlertItem, DispatchCheck, DispatchNotice, DispatchTask
from app.schemas.dispatch import (
    AdjustIn,
    AskIn,
    CloseIn,
    CommentIn,
    ConversationDraftIn,
    DeclineIn,
    ExtendIn,
    FanoutIn,
    DraftConfirmIn,
    DraftIn,
    NoteIn,
    PlanIn,
    PolishIn,
    ReplyIn,
    SubtaskIn,
    SupplementIn,
    TransferIn,
)
from app.secretary import config as sec_config
from app.services import alert_service, dispatch_ai, dispatch_service
from app.services.dispatch_service import DispatchError

S = dispatch_service        # 本文件下面用 `S.` 的短名（原来漏了这行：case_view/comment/
#                            supplement/fanout 四个路由会当场 NameError → 500）

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


@router_tasks.get('/case/{case_id}', summary='事项全景（整棵树+各层方案+补充说明；同一件事只有一张单）')
def case_view(case_id: str, actor: CurrentActor = Depends(get_current_actor),
              db: Session = Depends(get_db)):
    try:
        return S.case_view(db, actor.username, case_id)
    except S.DispatchError as exc:
        raise _fail(exc)


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
        return dispatch_service.reply(db, actor.username, task_id, body.content,
                                      body.attachments, body.attachment_ids)
    except DispatchError as exc:
        _fail(exc)


@router_tasks.post('/{task_id}/adjust', summary='发起人改时限/要求（子任务夹在父任务窗口内）')
def adjust(task_id: int, body: AdjustIn,
           actor: CurrentActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    _guard()
    try:
        return dispatch_service.adjust(db, actor.username, task_id,
                                       body.model_dump(exclude_none=True))
    except DispatchError as exc:
        _fail(exc)


@router_tasks.post('/{task_id}/extend', summary='承办人申请延期（留记录 + 通知发起人，不改时限）')
def request_extend(task_id: int, body: ExtendIn,
                   actor: CurrentActor = Depends(get_current_actor),
                   db: Session = Depends(get_db)):
    _guard()
    try:
        return dispatch_service.request_extend(db, actor.username, task_id,
                                               body.model_dump(exclude_none=True))
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


@router_tasks.post('/{task_id}/fanout', summary='一次下发 → N 条任务（每个目标一条，各看各的核销范围）')
def fanout(task_id: int, body: FanoutIn,
           actor: CurrentActor = Depends(get_current_actor),
           db: Session = Depends(get_db)):
    _guard()
    try:
        return S.fanout(db, actor.username, task_id, body.model_dump())
    except S.DispatchError as exc:
        raise _fail(exc)


@router_tasks.post('/{task_id}/comment', summary='说明（同一事项的人都能写，不要求谁办）')
def comment(task_id: int, body: CommentIn,
            actor: CurrentActor = Depends(get_current_actor),
            db: Session = Depends(get_db)):
    try:
        return S.comment(db, actor.username, task_id, body.content)
    except S.DispatchError as exc:
        raise _fail(exc)


@router_tasks.post('/{task_id}/supplement', summary='补充要求（追加到任务要求，通知承办人）')
def supplement(task_id: int, body: SupplementIn,
               actor: CurrentActor = Depends(get_current_actor),
               db: Session = Depends(get_db)):
    try:
        return S.supplement(db, actor.username, task_id, body.content)
    except S.DispatchError as exc:
        raise _fail(exc)


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


# ── 对话建任务（智能体拟稿 → 人工确认）────────────────────────

@router_tasks.post('/draft', summary='对话建任务：识别意图并拟草稿（不直接建任务）')
def task_draft(body: DraftIn, actor: CurrentActor = Depends(get_current_actor),
               db: Session = Depends(get_db)):
    _guard()
    try:
        return dispatch_service.propose_from_chat(
            db, actor.username, body.session_id, body.question, body.alert_id, body.today)
    except DispatchError as exc:
        _fail(exc)


@router_tasks.post('/draft/from-conversation', summary='把这段对话变成任务草稿（不判意图，需人工确认）')
def task_draft_from_conversation(body: ConversationDraftIn,
                                 actor: CurrentActor = Depends(get_current_actor),
                                 db: Session = Depends(get_db)):
    _guard()
    try:
        return dispatch_service.draft_from_conversation(
            db, actor.username, body.session_id, body.alert_id, body.today, body.instruction)
    except DispatchError as exc:
        _fail(exc)


@router_tasks.post('/draft/confirm', summary='确认草稿 → 正式任务（message 可传「确认」/「取消」）')
def task_draft_confirm(body: DraftConfirmIn, actor: CurrentActor = Depends(get_current_actor),
                       db: Session = Depends(get_db)):
    _guard()
    try:
        return dispatch_service.confirm_draft(
            db, actor.username, body.task_id, body.session_id, body.message, body.overrides)
    except DispatchError as exc:
        _fail(exc)


@router_tasks.get('/draft/pending', summary='查当前会话的待确认草稿')
def task_draft_pending(session_id: str = Query(..., min_length=1, max_length=64),
                       actor: CurrentActor = Depends(get_current_actor),
                       db: Session = Depends(get_db)):
    _guard()
    try:
        return dispatch_service.pending_draft(db, actor.username, session_id)
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


@router_tasks.post('/{task_id}/ai/report', summary='上报报告（markdown 或 docx）')
def ai_report(task_id: int, format: str = Query('markdown', pattern='^(markdown|docx)$'),
              actor: CurrentActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    _guard()
    try:
        d = dispatch_service.task_detail(db, actor.username, task_id)
        if format == 'docx':
            return dispatch_service.report_docx(db, actor.username, task_id)
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


# ── 附件 / 追问 ───────────────────────────────────────────────

@router_tasks.post('/{task_id}/attachments', summary='上传附件（回执材料/报告）')
def upload_attachment(task_id: int, file: UploadFile = File(...),
                      actor: CurrentActor = Depends(get_current_actor),
                      db: Session = Depends(get_db)):
    _guard()
    try:
        data = file.file.read()
        return dispatch_service.add_attachment(db, actor.username, task_id,
                                               file.filename or 'file', data)
    except DispatchError as exc:
        _fail(exc)


@router_tasks.get('/{task_id}/attachments', summary='附件列表')
def list_attachments(task_id: int, actor: CurrentActor = Depends(get_current_actor),
                      db: Session = Depends(get_db)):
    _guard()
    try:
        return dispatch_service.list_attachments(db, actor.username, task_id)
    except DispatchError as exc:
        _fail(exc)


@router_tasks.get('/{task_id}/attachments/{att_id}/download', summary='下载附件')
def download_attachment(task_id: int, att_id: int,
                        actor: CurrentActor = Depends(get_current_actor),
                        db: Session = Depends(get_db)):
    _guard()
    try:
        path, name = dispatch_service.attachment_file(db, actor.username, task_id, att_id)
    except DispatchError as exc:
        _fail(exc)
    return FileResponse(str(path), filename=name,
                        media_type='application/octet-stream')


@router_tasks.post('/{task_id}/ask', summary='任务内追问（复用 agent，带任务上下文）')
def task_ask(task_id: int, body: AskIn,
             actor: CurrentActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    _guard()
    try:
        return dispatch_service.ask(db, actor.username, task_id, body.question)
    except DispatchError as exc:
        _fail(exc)


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


@router_dispatch.get('/jobs', summary='站点包定时任务：在不在跑、下次什么时候跑（内部令牌）')
def jobs(request: Request):
    """给运维看：调度器起没起、每条活的下次运行时间、最近 10 次执行结果。"""
    _internal(request)
    from app.secretary import scheduler

    try:
        return scheduler.status()
    except Exception as exc:  # noqa: BLE001
        return {'running': False, 'jobs': [], 'recent': [],
                'error': '%s: %s' % (type(exc).__name__, str(exc)[:200])}


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
