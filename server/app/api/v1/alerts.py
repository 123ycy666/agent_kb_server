# -*- coding: utf-8 -*-
"""预警接口：扫描 / 列表 / 详情 / 卡片 / 预警↔对话 / 拟办 / 下发。

挂载：`/api/alerts/*`（与 agent 的 `/api/alerts/daily` 并存，互不影响）

鉴权：
  · 用户接口：`Authorization: Bearer <JWT>`（与知识库同一套登录）
  · 系统接口（scan）：`X-Internal-Token`（沿用 .env 的 INTERNAL_SERVICE_TOKEN）
  · **不接受调用方传角色/范围**：一律按账号现查（alert_scope）
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app import dispatch_rules as DR
from app.core.database import get_db
from app.core.deps import CurrentActor, get_current_actor
from app.schemas.dispatch import ChatIn, DispatchIn, ProposeIn, ScanIn
from app.secretary import config as sec_config
from app.services import alert_service, dispatch_ai, dispatch_service
from app.services.dispatch_service import DispatchError

router = APIRouter(prefix='/alerts', tags=['预警'])


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


@router.post('/scan', summary='跑一轮预警扫描并落库（内部令牌）')
def scan(body: ScanIn, request: Request, db: Session = Depends(get_db)):
    _guard()
    _internal(request)
    try:
        return alert_service.scan(db, accounts=body.accounts, cadence=body.cadence,
                                  today=body.today, trigger=body.trigger or 'api')
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500,
                            detail={'code': 'SCAN_FAILED', 'message': '%s: %s' % (type(exc).__name__, exc)})


@router.get('', summary='我的预警列表（按登录人范围 + 角色过滤）')
def list_alerts(
    category: str | None = Query(None, pattern='^(budget|project|review|metric)$'),
    subtype: str | None = None,
    status: str | None = Query(None, pattern='^(open|dispatched|closed|ignored|resolved)$'),
    level: str | None = Query(None, pattern='^(high|mid|low)$'),
    today: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    _guard()
    return alert_service.list_alerts(db, actor.username, category=category, subtype=subtype,
                                     status=status, level=level, limit=limit, offset=offset,
                                     today=today)


@router.get('/{alert_id}', summary='预警详情（含已下发任务）')
def get_alert(alert_id: int, today: str | None = None,
              actor: CurrentActor = Depends(get_current_actor),
              db: Session = Depends(get_db)):
    _guard()
    out = alert_service.get_alert(db, actor.username, alert_id, today)
    if out is None:
        raise HTTPException(status_code=404,
                            detail={'code': 'ALERT_NOT_FOUND', 'message': '预警不存在或无权查看'})
    return out


@router.get('/{alert_id}/card', summary='预警卡片（前端渲染用；不调模型）')
def get_card(alert_id: int, today: str | None = None,
             actor: CurrentActor = Depends(get_current_actor),
             db: Session = Depends(get_db)):
    _guard()
    out = alert_service.get_alert(db, actor.username, alert_id, today)
    if out is None:
        raise HTTPException(status_code=404,
                            detail={'code': 'ALERT_NOT_FOUND', 'message': '预警不存在或无权查看'})
    return {'card': out}


@router.post('/{alert_id}/chat', summary='点预警进对话：建/复用会话，写卡片 + 首段话')
def chat(alert_id: int, body: ChatIn | None = None,
         actor: CurrentActor = Depends(get_current_actor),
         db: Session = Depends(get_db)):
    _guard()
    out = alert_service.open_chat(db, actor.username, alert_id, (body.today if body else None))
    if out is None:
        raise HTTPException(status_code=404,
                            detail={'code': 'ALERT_NOT_FOUND', 'message': '预警不存在或无权查看'})
    return out


@router.post('/{alert_id}/analysis', summary='AI 根因分析 + 3 条建议（不落库）')
def analysis(alert_id: int, actor: CurrentActor = Depends(get_current_actor),
             db: Session = Depends(get_db)):
    _guard()
    out = alert_service.get_alert(db, actor.username, alert_id)
    if out is None:
        raise HTTPException(status_code=404,
                            detail={'code': 'ALERT_NOT_FOUND', 'message': '预警不存在或无权查看'})
    return dispatch_ai.brief(out)


@router.post('/{alert_id}/propose', summary='AI 拟办：按某条建议生成 draft（不落库）')
def propose(alert_id: int, body: ProposeIn | None = None,
            actor: CurrentActor = Depends(get_current_actor),
            db: Session = Depends(get_db)):
    _guard()
    out = alert_service.get_alert(db, actor.username, alert_id)
    if out is None:
        raise HTTPException(status_code=404,
                            detail={'code': 'ALERT_NOT_FOUND', 'message': '预警不存在或无权查看'})
    return dispatch_ai.propose(out, option=(body.option if body else 1))


@router.post('/{alert_id}/dispatch/preview', summary='下发确认页数据：推荐承办人 + 默认时限 + 拟办草稿')
def preview(alert_id: int, actor: CurrentActor = Depends(get_current_actor),
            db: Session = Depends(get_db)):
    _guard()
    try:
        return dispatch_service.preview(db, actor.username, alert_id)
    except DispatchError as exc:
        _fail(exc)


@router.post('/{alert_id}/dispatch', summary='确认下发（task + 承办人 + 通知 同事务）')
def dispatch(alert_id: int, body: DispatchIn,
             actor: CurrentActor = Depends(get_current_actor),
             db: Session = Depends(get_db)):
    _guard()
    try:
        return dispatch_service.create(db, actor.username, alert_id, body.model_dump())
    except DispatchError as exc:
        _fail(exc)
