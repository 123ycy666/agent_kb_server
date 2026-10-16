# -*- coding: utf-8 -*-
"""问数智能体 HTTP 接口（2026-10-15 单进程合并，原 agent/server.py 改写为 FastAPI 路由）。

路径保持原样（`/api/ask`、`/api/sessions`、`/api/history`、`/api/stats`、`/api/gaps`、
`/api/feedback`、`/api/page`、`/api/models`、`/api/skill/*`、`/api/report*`、`/api/asr*`），
与知识库的 `/api/v1/*` 天然不冲突 ⇒ **前端三个静态页零改动**（相对路径照旧命中）。

与原 http.server 实现的差异（合并带来的改进）：
1. `ask` / `sessions` / `history` 等需要 **JWT 登录**（原来零认证）——
   `user_id` 取当前登录用户，对话记录与账号真正挂上；
2. 检索身份 = 当前登录用户（`kb_adapter.set_actor`），
   问答只能检索到「公开 + 自己上传的」，与 Web 界面口径一致；
3. 预热改由 lifespan 调 `preheat()`（semantic 字典 + 会话摘要补跑）。
"""
from __future__ import annotations

import os
import threading
import time
import uuid

from fastapi import APIRouter, Depends, Query, Request
import json as _json
from fastapi.responses import HTMLResponse, Response
from starlette.concurrency import run_in_threadpool

from app.core.deps import CurrentActor, get_current_actor
from app.secretary import agent as agent_mod
from app.secretary import config as sec_config
from app.secretary import kb_adapter, qa_log, report as report_mod, tools_asr
from app.secretary.gaps import list_gaps
from app.schemas.agent import AskResponse


def _jr(data, status_code: int = 200) -> Response:
    """统一 JSON 响应。

    ⚠️ 必须先 json.dumps(default=str) 再包 Response：Starlette 的
    JSONResponse **没有 default 参数**，而 PG numeric / MySQL decimal 列
    会返回 Decimal 对象，直接丢给它会 TypeError（原 http.server 实现就是
    手动 dumps(default=str)，这里保持一致）。
    """
    return Response(
        content=_json.dumps(data, ensure_ascii=False, default=str),
        status_code=status_code,
        media_type="application/json",
    )

router = APIRouter(tags=["问数智能体"])


# ══════════════════════════════════════════════════════════════
#  检索范围（知识库选择）——校验 + 默认范围
# ══════════════════════════════════════════════════════════════

def _parse_kb_ids(raw):
    """把前端传来的 kb_ids 解析成 int 列表（非数字项直接忽略，不报错）。"""
    if not isinstance(raw, (list, tuple)):
        return []
    out = []
    for x in raw:
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            continue
    return out


def _resolve_kb_scope(requested):
    """把请求的 kb_ids 收敛为「可用」集合。返回 (kb_ids, explicit)。

    explicit=True  → 用户显式勾选了，返回校验通过的子集（空集 = 全都不可用）
    explicit=False → 用户没选，返回 `qa_enabled` 的库（可能为空 ⇒ 调用方用配置默认）

    ⚠️ 必须**服务端**校验：不能只信前端传来的 kb_ids，否则构造请求即可检索
       任意知识库。当前知识库没有「库级权限」（所有登录用户可读），
       所以校验 = 「库存在且 status=active」；将来若引入库级权限，只改这里。

    ⚠️ 为什么默认范围用「库上打标记」而不是配置里写 id 列表：
       自增 id 会因删库/建库漂移（删除不复用、新建拿更大值），写死的配置会悬空。
       标记则随库走：新建库勾选即生效、删库自动移出。
    """
    from sqlalchemy import select as _select

    from app.core.database import SessionLocal
    from app.models import KnowledgeBase

    db = SessionLocal()
    try:
        rows = db.execute(
            _select(KnowledgeBase.id, KnowledgeBase.qa_enabled)
            .where(KnowledgeBase.status == "active")
        ).all()
    finally:
        db.close()

    readable = {int(r[0]) for r in rows}
    if requested:
        return [k for k in requested if k in readable], True
    return [int(r[0]) for r in rows if r[1]], False

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(os.path.dirname(HERE), "secretary", "static")
ASR_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "output", "asr"
)


# ══════════════════════════════════════════════════════════════
#  预热（lifespan 调用）：语义字典 + 会话摘要补跑
# ══════════════════════════════════════════════════════════════

def preheat() -> None:
    """后台预热。与原 server.py 的 _preheat 等价，异常不影响服务。"""

    def warm_semantic():
        try:
            from app.secretary import semantic

            t0 = time.time()
            semantic.warm()
            print(f"[启动] 语义字典就绪（耗时 {time.time() - t0:.1f}s）", flush=True)
        except Exception as e:                     # noqa: BLE001
            print(f"[启动] 语义字典预热异常（不影响服务，首个请求会补建）：{e}", flush=True)

    def resume_tasks():
        try:
            from app.secretary import session_summary

            st = session_summary.status()
            n = session_summary.recover() if st.get("available") else 0
            print(
                f"[启动] 会话摘要{'就绪' if st.get('available') else '未启用'}"
                f"（{st.get('source', '?')}，每 {st.get('every', 0)} 轮，补跑 {n} 场）",
                flush=True,
            )
        except Exception as e:                     # noqa: BLE001
            print(f"[启动] 会话摘要不可用（不影响问答）：{e}", flush=True)

    threading.Thread(target=warm_semantic, daemon=True, name="semantic-preheat").start()
    threading.Thread(target=resume_tasks, daemon=True, name="resume-tasks").start()


# ══════════════════════════════════════════════════════════════
#  健康检查（公开，探活用）
# ══════════════════════════════════════════════════════════════

@router.get("/health", summary="问数智能体健康检查")
def health():
    kb = kb_adapter.health(timeout=8)
    try:
        from app.secretary import name_fix, session_summary

        nf = name_fix.status()
        ss = session_summary.status()
    except Exception:                              # noqa: BLE001
        nf, ss = {"available": False}, {"available": False}
    return {
        "ok": True,
        "model": sec_config.MODEL,
        "channel": sec_config.MODEL_CHANNEL,
        "name_fix": nf.get("available", False),
        "name_fix_source": nf.get("source", ""),
        "qa_log": True,
        "kb": kb.get("available", False),
        "kb_reason": kb.get("reason", ""),
        "session_summary": ss.get("available", False),
    }


# ══════════════════════════════════════════════════════════════
#  问答核心：POST /api/ask（需登录）
# ══════════════════════════════════════════════════════════════

@router.post(
    "/ask",
    summary="提问（需登录；检索范围 = kb_ids，或「参与智能问答」标记的库）",
    response_model=AskResponse,
    responses={403: {"description": "所选知识库全部不可用（不存在 / 已停用 / 无权限）"}},
)
async def ask(request: Request, actor: CurrentActor = Depends(get_current_actor)):
    data = await _read_json(request)
    q = (data.get("question") or "").strip()
    if not q:
        return _jr({"answer": "问题为空", "trace": []})

    model = data.get("model") or None
    max_steps = data.get("max_steps") or None
    profile = (data.get("profile") or "").strip() or None
    # 会话与身份：不传 session_id 则服务端生成；user_id 取当前登录用户（统一用户表）
    session_id = (data.get("session_id") or "").strip() or ("s_" + uuid.uuid4().hex)
    user_code = (data.get("uid") or data.get("user_code") or "").strip() or None
    user_name = (data.get("user_name") or "").strip() or actor.display_name or actor.username
    channel = (data.get("channel") or "").strip() or "web"
    history_turns = data.get("history_turns")
    want_page = bool(data.get("want_page"))
    page_async = (data.get("page_mode") or "").strip().lower() == "async"
    scope = (data.get("scope") or data.get("business_scope") or "").strip() or None
    today = (data.get("today") or data.get("business_date") or "").strip() or None
    client_ip = request.client.host if request.client else None

    # 检索范围：前端可传 kb_ids（问答页勾选的库）；不传则用库里 qa_enabled 的库。
    # 服务端校验后才生效（见 _resolve_kb_scope）—— 防止构造请求越权检索。
    kb_scope, explicit_kb = _resolve_kb_scope(_parse_kb_ids(data.get("kb_ids")))
    if explicit_kb and not kb_scope:
        return _jr(
            {"answer": "所选知识库不可用（不存在、已停用或没有权限）",
             "trace": [], "session_id": session_id},
            status_code=403,
        )

    # 检索身份 = 当前登录用户（线程/协程内生效，问答结束自动失效）
    kb_adapter.set_actor(actor)

    try:
        # ⚠️ 必须丢线程池执行（2026-10-16 修的真实故障）：
        #    agent_mod.ask 内部是**同步**调模型（urllib，单次几十秒），
        #    直接写在 async def 里会占住事件循环 ⇒ 这期间**所有人的请求**都要排队
        #    （连同步 def 的知识库端点也发不出响应），表现为「一个人提问、全站转圈」。
        #    anyio 的 to_thread 会**复制上下文** ⇒ kb_adapter 的身份/检索范围不会丢。
        r = await run_in_threadpool(
            agent_mod.ask,
            q,
            model=model,
            max_steps=max_steps,
            profile=profile,
            want_page=want_page,
            today=today,
            scope=scope,
            page_async=page_async,
            session_id=session_id,
            user_id=actor.user_id,
            user_code=user_code or actor.username,
            client_ip=client_ip,
            channel=channel,
            history_turns=history_turns,
            kb_ids=kb_scope or None,   # None ⇒ kb_adapter 退回配置默认
        )
    except Exception as e:                         # noqa: BLE001
        r = {
            "input_question": q, "question": q, "session_id": session_id,
            "answer": "服务异常：" + type(e).__name__ + " " + str(e), "trace": [],
            "no_db_query": True, "db_query_count": 0, "elapsed_ms": 0, "steps": 0,
        }
    r["user_name"] = user_name
    return _jr(r)


async def _read_json(request: Request) -> dict:
    """宽容解析 JSON：空 body 返 {}，非法 JSON 返 {}（由各接口自行判定）。"""
    raw = await request.body()
    if not raw:
        return {}
    import json

    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:                              # noqa: BLE001
        return {}


# ══════════════════════════════════════════════════════════════
#  历史会话 / 统计 / 缺口 / 异步页面 / 模型清单（需登录）
# ══════════════════════════════════════════════════════════════

@router.get("/sessions", summary="我的会话列表（按当前登录用户过滤）")
def sessions(
    limit: int = Query(default=0),
    offset: int = Query(default=0),
    actor: CurrentActor = Depends(get_current_actor),
):
    """统一用户后不再需要 uid 参数：只返回当前登录用户自己的会话。

    limit=0（不传）= 全部返回，受 config.API_MAX_ROWS 防呆上限保护。
    """
    try:
        return _jr(
            qa_log.list_sessions(user_id=actor.user_id, limit=limit or None, offset=offset or 0)
        )
    except Exception as e:                         # noqa: BLE001
        return _jr(
            {"total": 0, "returned": 0, "limit": 0, "offset": 0,
             "truncated": False, "sessions": [],
             "error": "%s: %s" % (type(e).__name__, str(e)[:200])}
        )


@router.get("/history", summary="某场会话的历史记录（含每轮口径/质量信息）")
def history(
    session_id: str = Query(default=""),
    limit: int = Query(default=0),
    with_steps: bool = Query(default=False),
    actor: CurrentActor = Depends(get_current_actor),
):
    sid = (session_id or "").strip()
    if not sid:
        return _jr({"error": "缺少 session_id"}, status_code=400)
    try:
        r = qa_log.get_history(sid, limit=limit or None, with_steps=with_steps)
        if r is None:
            return _jr({"error": "session 不存在", "session_id": sid}, status_code=404)
        return _jr(r)
    except Exception as e:                         # noqa: BLE001
        return _jr(
            {"error": "%s: %s" % (type(e).__name__, str(e)[:200]), "session_id": sid},
            status_code=500,
        )


@router.get("/stats", summary="记录库概览（问答量/缺口数/未核实数/纠错命中）")
def stats(actor: CurrentActor = Depends(get_current_actor)):
    try:
        return _jr(qa_log.stats())
    except Exception as e:                         # noqa: BLE001
        return _jr({"error": "%s: %s" % (type(e).__name__, str(e)[:200])}, status_code=500)


@router.get("/gaps", summary="知识缺口清单（模型回答不了的问题）")
def gaps(limit: int = Query(default=200), actor: CurrentActor = Depends(get_current_actor)):
    try:
        rows = list_gaps(limit)
    except Exception as e:                         # noqa: BLE001
        return _jr({"total": 0, "rows": [], "error": str(e)[:200]})
    return _jr({"total": len(rows), "rows": rows})


@router.get("/page", summary="取异步生成的分析页面（token）")
def page(token: str = Query(default=""), actor: CurrentActor = Depends(get_current_actor)):
    tok = (token or "").strip()
    got = agent_mod.page_get(tok) if tok else None
    if not got:
        return Response("page token not found", status_code=404, media_type="text/plain")
    if not got.get("done"):
        return Response("page generating", status_code=202, media_type="text/plain")
    if got.get("html"):
        return HTMLResponse(got["html"])
    return Response("page failed: %s" % got.get("error", ""), status_code=500, media_type="text/plain")


@router.get("/models", summary="可选模型清单（当前通道下实测存在）")
def models(actor: CurrentActor = Depends(get_current_actor)):
    return _jr({"default": sec_config.MODEL, "models": sec_config.MODELS})


@router.post("/feedback", summary="给某次回答打评价（1 赞 / 0 踩）")
async def feedback(request: Request, actor: CurrentActor = Depends(get_current_actor)):
    data = await _read_json(request)
    qa_id = data.get("qa_id")
    if qa_id in (None, ""):
        return _jr({"error": "缺少 qa_id（取 /api/ask 返回里的 qa_id）"}, status_code=400)
    try:
        qa_id = int(qa_id)
    except (TypeError, ValueError):
        return _jr({"error": "qa_id 必须是整数"}, status_code=400)
    try:
        fb = int(data.get("feedback"))
    except (TypeError, ValueError):
        return _jr({"error": "feedback 必须是 1（赞）或 0（踩）"}, status_code=400)
    try:
        n = await run_in_threadpool(qa_log.set_feedback, qa_id, fb, data.get("note"))
        return _jr({"ok": n > 0, "updated": n, "qa_id": qa_id, "feedback": fb})
    except Exception as e:                         # noqa: BLE001
        return _jr({"error": "%s: %s" % (type(e).__name__, str(e)[:200])}, status_code=500)


# ══════════════════════════════════════════════════════════════
#  报告技能组（需登录）
# ══════════════════════════════════════════════════════════════

@router.post("/skill/run", summary="触发一个技能（技能文档 + 上游数据 → 合并回答）")
async def skill_run(request: Request, actor: CurrentActor = Depends(get_current_actor)):
    data = await _read_json(request)
    try:
        r = await run_in_threadpool(
            report_mod.run_skill,
            (data.get("skill_id") or "").strip(),
            model=data.get("model") or None,
            work_name=data.get("work_name") or "",
            frm=data.get("from") or "", to=data.get("to") or "",
            questions=data.get("questions") or None,
            role=data.get("role") or "", scope=data.get("scope") or "",
            use_doc=bool(data.get("use_doc", True)),
            inputs=data.get("inputs") or None,
        )
    except Exception as e:                         # noqa: BLE001
        r = {"answer": "服务异常：" + type(e).__name__ + " " + str(e)[:200], "trace": [], "tool_calls": 0}
    return _jr(r)


@router.post("/skill/title", summary="定时触发专用：只回一个标题（绝不空返回）")
async def skill_title(request: Request, actor: CurrentActor = Depends(get_current_actor)):
    data = await _read_json(request)
    try:
        r = await run_in_threadpool(
            report_mod.run_skill_title,
            (data.get("skill_id") or "").strip(),
            model=data.get("model") or None,
            account=data.get("account") or data.get("uid") or actor.username,
            when=data.get("when") or data.get("triggered_at") or "",
            scope=data.get("scope") or "",
            inputs=data.get("inputs") or None,
            today=(data.get("today") or data.get("business_date") or "").strip() or None,
        )
    except Exception as e:                         # noqa: BLE001
        r = {"skill_id": data.get("skill_id"), "status": "unknown", "count": None,
             "title": "服务异常：" + type(e).__name__ + " " + str(e)[:200],
             "checked_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    return _jr(r)


@router.post("/report/notice", summary="按供电所生成预算分配通知（可编辑模拟值）")
async def report_notice(request: Request, actor: CurrentActor = Depends(get_current_actor)):
    data = await _read_json(request)
    station = (data.get("station") or "").strip()
    if not station:
        return _jr({"error": "缺少 station"})
    try:
        r = await run_in_threadpool(
            report_mod.build_notice, station,
            base_year=data.get("base_year"), plan_year=data.get("plan_year"))
    except Exception as e:                         # noqa: BLE001
        r = {"error": type(e).__name__ + " " + str(e)[:200]}
    return _jr(r)


@router.post("/report", summary="年度缺陷治理计划分析（模型按技能文档自查自答）")
async def report(request: Request, actor: CurrentActor = Depends(get_current_actor)):
    data = await _read_json(request)
    try:
        r = await run_in_threadpool(
            report_mod.run_report, notice=data.get("notice") or None,
            station=(data.get("station") or "环潭供电所"),
            model=data.get("model") or None)
    except Exception as e:                         # noqa: BLE001
        r = {"report": "", "trace": [], "notice": {},
             "warnings": ["服务异常：" + type(e).__name__ + " " + str(e)], "elapsed_ms": 0}
    return _jr(r)


@router.get("/statechange", summary="状态变更模拟数据（技能台演示）")
def statechange(actor: CurrentActor = Depends(get_current_actor)):
    return _jr(report_mod.state_change_sim())


@router.get("/triggers", summary="技能触发清单")
def triggers(actor: CurrentActor = Depends(get_current_actor)):
    return _jr(report_mod.list_triggers())


@router.get("/skills", summary="技能文档清单")
def skills(actor: CurrentActor = Depends(get_current_actor)):
    return _jr({"skills": report_mod.list_skill_docs()})


@router.get("/report/meta", summary="报告技能元数据（基准年/计划年/供电所列表）")
def report_meta(actor: CurrentActor = Depends(get_current_actor)):
    y = report_mod.latest_year()
    return _jr({"base_year": y, "plan_year": str(int(y) + 1),
                         "stations": report_mod.list_stations(),
                         "title": "年度缺陷治理计划分析"})


# ══════════════════════════════════════════════════════════════
#  语音识别（需登录）：原始二进制上传，5 阶段全链路返回
# ══════════════════════════════════════════════════════════════

@router.post("/asr", summary="语音识别（浏览器录音 → 转写 → 纠错）")
async def asr(request: Request, actor: CurrentActor = Depends(get_current_actor)):
    raw = await request.body()
    fname = (request.headers.get("X-Filename") or "rec.webm").replace("\\", "_").replace("/", "_")
    os.makedirs(ASR_DIR, exist_ok=True)
    save_to = os.path.join(ASR_DIR, time.strftime("%Y%m%d_%H%M%S_") + fname)
    with open(save_to, "wb") as f:
        f.write(raw)
    try:
        # ⚠️ 语音识别是最长的阻塞（内部 time.sleep 轮询，最长 180 秒），
        #    必须丢线程池 —— 否则一个人录音，全站转圈三分钟。
        r = await run_in_threadpool(tools_asr.transcribe, save_to)
    except Exception as e:                         # noqa: BLE001
        r = {"text": "", "error": type(e).__name__ + " " + str(e), "stages": []}
    r["saved_to"] = save_to
    r["bytes"] = len(raw)
    return _jr(r)


@router.get("/asr/sample", summary="示例音频（语音页演示用）")
def asr_sample():
    sample = os.path.join(os.path.dirname(ASR_DIR), "sample_asr.mp3")
    if os.path.exists(sample):
        with open(sample, "rb") as f:
            return Response(f.read(), media_type="audio/mpeg")
    return Response("sample not found", status_code=404, media_type="text/plain")
