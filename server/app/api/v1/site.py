# -*- coding: utf-8 -*-
"""现场配置接口（UI-001 / FR-CFG-001..003）。

    GET  /api/v1/site/status   当前现场状态（公开：前端据此决定「只出配置页」）
    GET  /api/v1/site/config   读取引导配置（口令脱敏）+ 业务库 + 模型通道
    PUT  /api/v1/site/config   保存并立即重连；管理员不存在才建，已有口令不覆盖
    POST /api/v1/site/test     三条连通性测试（产品库 / 业务库 / 模型通道），只测不存

⚠️ 鉴权：降级时（未配置或连不上）这四个接口**不要求登录** —— 此时本来就登不了；
   一旦现场就绪，config/test 只允许管理员，否则谁都能把这个实例接到别的库上。
"""
from __future__ import annotations

import time
import urllib.error
import urllib.request

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core import site
from app.core.deps import CurrentActor, get_current_actor_optional

router = APIRouter(prefix="/site", tags=["现场配置"])

_RUNTIME_KEYS = ("model_channel", "llm_base_url", "llm_api_key", "llm_model",
                 "db_host", "db_port", "db_name", "db_user", "db_password")


def _admin_guard(actor: CurrentActor | None) -> None:
    """现场就绪后只放管理员。未就绪时不需要登录。"""
    if not site.ok():
        return
    if actor is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail={"code": "NOT_AUTHENTICATED", "message": "缺少访问凭证"})
    if not actor.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail={"code": "NO_PERMISSION", "message": "只有管理员能改现场配置"})


def _mask(v: str) -> str:
    s = str(v or "")
    if not s:
        return ""
    return ("*" * 6 + s[-4:]) if len(s) > 10 else "*" * len(s)


def _alert_db() -> str:
    """预警工作流实际在用的业务库（留空时等于问数业务库）。"""
    try:
        from app.wf import meta
        return meta.biz_db()
    except Exception:                                 # noqa: BLE001 - 读不到就空着
        return ""


@router.get("/status", summary="当前现场状态（公开）")
def get_status():
    return site.status(refresh=True)


@router.get("/config", summary="读现场配置（口令脱敏）")
def get_config(actor: CurrentActor | None = Depends(get_current_actor_optional)):
    _admin_guard(actor)          # 现场就绪后只有管理员能看连接信息（含脱敏口令）
    st = site.status()
    cfg = site.load() or {}
    from app.secretary import config as sec_config, runtime_cfg

    vals = runtime_cfg.all() if st.get("ok") else {}
    if st.get("ok"):
        # 先把库里的值算进 config.DB，再读它 —— 否则页面显示的是上次请求算出的旧连接
        runtime_cfg.refresh()
    pg = dict((cfg.get("pg") or {}))
    pg["password"] = _mask(pg.get("password", ""))
    admin = dict((cfg.get("admin") or {}))
    admin["password"] = _mask(admin.get("password", ""))
    # 业务库显示**实际生效**的那组（config.DB），不是库里存的半截值：
    # 页面空着但环境变量在生效时，用户要能看见自己实际在查哪个库。
    return {
        "status": st,
        "pg": pg,
        "admin": admin,
        "mysql": {"db_host": sec_config.DB.get("host") or "",
                  "db_port": sec_config.DB.get("port") or "",
                  "db_name": sec_config.DB.get("db") or "",
                  "db_user": sec_config.DB.get("user") or ""},
        "mysql_password_set": bool(sec_config.DB.get("password")),
        # 预警工作流单独用的业务库（同一台 MySQL，只换库名）；空 = 跟问数同一个库
        "alert_db_name": _alert_db(),
        "mysql_source": ("page" if all(vals.get(k) for k in
                                       ("db_host", "db_port", "db_name", "db_user", "db_password"))
                         else "env"),
        "model": {"channel": vals.get("model_channel") or "",
                  "base_url": vals.get("llm_base_url") or "",
                  "api_key_set": bool(vals.get("llm_api_key")),
                  "model": vals.get("llm_model") or ""},
    }


@router.put("/config", summary="保存现场配置并立即重连")
async def put_config(request: Request,
                     actor: CurrentActor | None = Depends(get_current_actor_optional)):
    _admin_guard(actor)
    try:
        body = await request.json()
    except Exception:                                 # noqa: BLE001
        raise HTTPException(status_code=400, detail={"code": "BAD_BODY",
                                                     "message": "请求体不是 JSON"})
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail={"code": "BAD_BODY",
                                                     "message": "请求体必须是 JSON 对象"})
    pg = body.get("pg")
    if not isinstance(pg, dict) or not pg.get("dbname"):
        raise HTTPException(status_code=400, detail={"code": "BAD_PG",
                                                     "message": "缺少产品库连接（pg）"})
    cfg = {"pg": pg, "admin": body.get("admin") or {},
           "wf_database": body.get("wf_database") or None}
    st = site.apply(cfg)
    if not st.get("ok"):
        return {"ok": False, "error": st.get("error") or "连接失败", "status": st}

    saved: dict = {}
    mysql = body.get("mysql") or {}
    model = body.get("model") or {}
    payload = {}
    for k in ("db_host", "db_port", "db_name", "db_user", "db_password", "alert_db_name"):
        if mysql.get(k):
            payload[k] = str(mysql[k])
    for k in ("model_channel", "llm_base_url", "llm_api_key", "llm_model"):
        if model.get(k):
            payload[k] = str(model[k])
    if payload:
        from app.secretary import runtime_cfg

        saved = runtime_cfg.save(payload, user=(actor.username if actor else "setup"))
    return {"ok": True, "status": site.status(refresh=True), "saved": saved.get("saved", 0),
            "admin": st.get("admin"), "pg_detail": st.get("pg_detail"), "runtime": payload}


@router.post("/test", summary="连通性测试（产品库 / 业务库 / 模型通道）")
async def test_conn(request: Request,
                    actor: CurrentActor | None = Depends(get_current_actor_optional)):
    _admin_guard(actor)
    try:
        body = await request.json()
    except Exception:                                 # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    target = (body.get("target") or "pg").strip().lower()
    t0 = time.time()
    if target == "pg":
        pg = body.get("pg") or (site.load() or {}).get("pg") or {}
        ok, detail = site.test_pg(pg)
    elif target == "mysql":
        from app.secretary import runtime_cfg

        r = runtime_cfg.test_conn(body.get("mysql") or body)
        ok, detail = bool(r.get("ok")), str(r.get("result") if r.get("ok") else r.get("error"))
    elif target == "model":
        ok, detail = _test_model(body.get("model") or body)
    else:
        raise HTTPException(status_code=400, detail={"code": "BAD_TARGET",
                                                     "message": "target 只能是 pg / mysql / model"})
    return {"ok": ok, "target": target, "detail": detail, "ms": int((time.time() - t0) * 1000)}


def _test_model(m: dict) -> tuple[bool, str]:
    """模型通道连通性：先 GET /models，网关不支持就发一条 1 token 的对话。"""
    base = str(m.get("llm_base_url") or m.get("base_url") or "").rstrip("/")
    key = str(m.get("llm_api_key") or m.get("api_key") or "")
    model = str(m.get("llm_model") or m.get("model") or "")
    if not base:
        return False, "网关地址为空"
    headers = {"Authorization": "Bearer " + key} if key else {}

    def _call(url: str, data: bytes | None) -> tuple[int, str]:
        req = urllib.request.Request(url, data=data, headers=dict(headers, **(
            {"Content-Type": "application/json"} if data else {})),
            method="POST" if data else "GET")
        with urllib.request.urlopen(req, timeout=12) as r:
            return r.status, r.read()[:200].decode("utf-8", "replace")

    try:
        code, body = _call(base + "/models", None)
        return True, "GET /models → HTTP %s" % code
    except urllib.error.HTTPError as exc:
        if exc.code not in (404, 405, 400) or not model:
            return False, "GET /models → HTTP %s" % exc.code
    except Exception as exc:                          # noqa: BLE001
        return False, "%s: %s" % (type(exc).__name__, str(exc)[:160])
    try:
        import json

        data = json.dumps({"model": model, "max_tokens": 1,
                           "messages": [{"role": "user", "content": "hi"}]}).encode()
        code, _ = _call(base + "/chat/completions", data)
        return code < 400, "POST /chat/completions（%s）→ HTTP %s" % (model, code)
    except urllib.error.HTTPError as exc:
        return False, "POST /chat/completions → HTTP %s" % exc.code
    except Exception as exc:                          # noqa: BLE001
        return False, "%s: %s" % (type(exc).__name__, str(exc)[:160])
