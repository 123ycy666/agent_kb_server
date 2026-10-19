"""FastAPI 应用入口。

启动时自动初始化数据库（幂等），可重复启动不会出错。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.api.v1 import api_router

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("kb")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 68)
    logger.info("  %s 启动中", settings.app_name)
    logger.info("  模型通道 MODEL_CHANNEL = %s", settings.model_channel)
    logger.info("=" * 68)

    try:
        from app.db_init import init_database

        result = init_database(verbose=False)
        logger.info(
            "数据库就绪 | 扩展=%s | 新增权限点=%s | 新增角色=%s | 管理员同步=%s",
            result.get("extensions"),
            result.get("permissions_added"),
            result.get("roles_added"),
            result.get("admin_sync"),
        )
    except Exception as exc:  # noqa: BLE001
        # ⚠️ 这里**不中断启动**（老库表已存在时服务仍可用），但必须把「哪一步失败」说清楚。
        #    曾经这条日志只写"请确认 PostgreSQL 已启动"，把 `NameError` 这类代码问题
        #    也伪装成数据库连接问题，白排查了很久 —— 所以现在带上异常类型。
        logger.error(
            "数据库初始化失败（%s: %s）\n"
            "服务会继续启动，但建表 / 扩展 / 增量迁移 / 管理员同步都**未执行**。\n"
            "若是首次部署，请确认 PostgreSQL 已启动（docker compose up -d）"
            "且 .env 的 DATABASE_URL 正确。",
            type(exc).__name__,
            exc,
        )

    bundle = None
    try:
        from app.providers import get_providers

        bundle = get_providers()
        logger.info(
            "模型层 | embedding=%s | rerank=%s | llm=%s",
            bundle.embedding.model_id,
            bundle.rerank.model_id,
            bundle.llm.model_id,
        )
        if bundle.mode == "mock":
            # ⚠️ 最高频的误配：用户填了内网模型地址，却忘了把
            #    MODEL_CHANNEL 从 mock 改成 local。
            #    此时地址会被正常读入、但完全不会被使用 ——
            #    用户看到的现象是「地址明明填了却不生效」，极易误判为代码 bug。
            #    这里主动检测并给出醒目提示。
            configured: list[str] = []
            if settings.local_embedding_url and "127.0.0.1" not in settings.local_embedding_url:
                configured.append("本地模型服务")

            logger.warning(
                "当前为 mock 档位：检索结果为确定性伪向量，仅用于验证链路连通性。"
                "如需真实效果请设置 MODEL_CHANNEL=local 并配置 LOCAL_* 地址，"
                "或设 online 使用线上 API。"
            )
            if configured:
                logger.warning(
                    "⚠⚠⚠ 已检测到 %s 地址，但 MODEL_CHANNEL 仍是 mock，"
                    "这些配置不会被使用！请把 .env 中的 MODEL_CHANNEL 改为 local 后重启服务。",
                    "、".join(configured),
                )
    except Exception as exc:  # noqa: BLE001
        logger.error("模型层装配失败：%s", exc)

    # 问数智能体预热（语义字典 + 会话摘要补跑，后台线程，异常不影响服务）
    try:
        from app.api.v1.agent import preheat

        preheat()
    except Exception as exc:  # noqa: BLE001
        logger.warning("问数智能体预热启动失败（不影响服务）：%s", exc)

    logger.info("API 文档： http://127.0.0.1:8200/docs")
    yield
    logger.info("服务已停止")


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description=(
        "统一知识库与智能问数平台 API（单进程：知识库 + 问数智能体）。\n\n"
        "**认证方式**：`Authorization: Bearer <JWT>`（/auth/login 获取，"
        "账号密码 = 人力资源系统 t_employee）。\n\n"
        "**两大接口域**：\n"
        "- `/api/v1/*`：知识库（文档 / 检索 / 冲突 / 权限 / 审计）\n"
        "- `/api/*`：问数智能体（ask / sessions / history / skill / report / asr）\n\n"
        "**权限说明**：权限过滤由服务端强制注入，调用方无法通过参数绕过。"
    ),
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# ── CORS ─────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-Id"],
)


# ── 统一错误格式 ─────────────────────────────────────────────
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """把 HTTPException 的 detail 规范化。

    ⚠️ 对外 API 必须返回明确的错误码，不得只回 500。
    """
    detail = exc.detail
    if isinstance(detail, dict):
        payload = {
            "code": detail.get("code", "ERROR"),
            "message": detail.get("message", ""),
            "detail": detail.get("detail"),
        }
        if "required" in detail:
            payload["required"] = detail["required"]
    else:
        payload = {"code": f"HTTP_{exc.status_code}", "message": str(detail), "detail": None}

    return JSONResponse(status_code=exc.status_code, content={"detail": payload})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """参数校验失败也返回统一格式，便于调用方处理。"""
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": {
                "code": "VALIDATION_ERROR",
                "message": "请求参数校验失败",
                "detail": exc.errors()[:10],
            }
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """兜底异常处理。

    ⚠️ 未预期的异常也必须返回统一错误格式，不能让 500 裸奔。
       debug 模式下附上异常类型与消息，便于定位问题。
    """
    logger.exception("未处理异常：%s %s", request.method, request.url.path)
    message = f"{type(exc).__name__}: {exc}" if settings.debug else "服务器内部错误"
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": {
                "code": "INTERNAL_ERROR",
                "message": message,
                "detail": None,
            }
        },
    )


# ── 路由 ─────────────────────────────────────────────────────
# 顺序即优先级：API 路由先注册，静态挂载最后（通配捕获兜底）
app.include_router(api_router, prefix=settings.api_v1_prefix)

# 问数智能体（原 agent/ 服务，2026-10-15 单进程合并）
# 统一挂 /api 前缀：/api/ask、/api/sessions、/api/skill/* 等
# （与知识库的 /api/v1/* 天然不冲突，前端静态页相对路径零改）
from app.api.v1 import agent as agent_api  # noqa: E402

app.include_router(agent_api.router, prefix="/api")


@app.get("/api", tags=["系统"], summary="服务信息")
def root():
    return {
        "app": settings.app_name,
        "version": "1.0.0",
        "docs": "/docs",
        "kb_api_prefix": settings.api_v1_prefix,
        "agent_api_prefix": "/api",
        "model_channel": settings.model_channel,
    }


@app.get("/health", tags=["系统"], summary="统一健康检查（知识库 + 问数智能体）")
def unified_health():
    """一个系统一个 health：知识库库表连通 + 问数智能体各组件就绪情况。"""
    from app.core.database import check_connection, check_mysql_connection

    pg_ok, pg_detail = check_connection()
    my_ok, my_detail = check_mysql_connection()
    agent_h = agent_api.health()
    return {
        "status": "ok",
        "app": settings.app_name,
        "database": {"ok": pg_ok, "detail": pg_detail},
        "employee_source": {"ok": my_ok, "detail": my_detail},
        "agent": agent_h,
    }


# ── 智能问答页（/agent/）─────────────────────────────────────
# 原 agent 的三个静态页（问答 / 语音 / 技能台），页面内相对路径 /api/* 照旧命中
from pathlib import Path as _Path  # noqa: E402

from fastapi.responses import FileResponse as _FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles as _StaticFiles  # noqa: E402

_AGENT_STATIC = _Path(__file__).resolve().parent / "secretary" / "static"


@app.get("/agent/asr", tags=["问数智能体"], include_in_schema=False)
def agent_asr_page():
    """语音页（原 /asr）。"""
    return _FileResponse(_AGENT_STATIC / "asr.html")


@app.get("/agent/skills", tags=["问数智能体"], include_in_schema=False)
def agent_skills_page():
    """技能触发台（原 /skills）。"""
    return _FileResponse(_AGENT_STATIC / "skills.html")


if _AGENT_STATIC.is_dir():
    app.mount("/agent", _StaticFiles(directory=str(_AGENT_STATIC), html=True), name="agent-static")
else:
    logger.warning("智能问答静态页目录不存在：%s", _AGENT_STATIC)

# ── 前端 SPA（生产模式：后端直接托管构建产物，访问 8200 即是完整系统）──────
# 不用 mount("/")（会拦截一切），改用 catch-all 返回 index.html，
# 让 React Router 接管 /login、/kb、/overview 等前端路由。
# 路径：server/app/main.py → parents[2] = 项目根 → web/dist
_SPA_DIST = _Path(__file__).resolve().parents[2] / "web" / "dist"
_SPA_INDEX = _SPA_DIST / "index.html"
if not _SPA_INDEX.is_file():
    logger.warning("前端 dist 未构建（%s），SPA 兜底不可用；开发模式请用 web 的 vite dev", _SPA_DIST)


@app.get("/{full_path:path}", include_in_schema=False)
def spa_fallback(full_path: str):
    """SPA 兜底：未匹配的 GET 路径回 index.html（交给前端路由解析）。

    ⚠️ 必须先放行 dist 里**真实存在**的静态文件（JS/CSS/图片/字体）。
    否则浏览器请求 /assets/index-xxx.js 会拿到 index.html，
    content-type 是 text/html，浏览器按 JS 解析直接抛语法错误 → **白屏**。
    这个坑的表现很有迷惑性：页面 200、JS 也 200、控制台无输出、#root 空的。
    """
    if full_path:
        # 阻断路径穿越：只接受规范化后仍位于 dist 内的文件
        try:
            target = (_SPA_DIST / full_path).resolve()
            if target.is_file() and target.is_relative_to(_SPA_DIST.resolve()):
                # assets/ 下的文件名带内容 hash（index-XXXX.js），内容变了名字才变，
                # 因此可以放心长缓存；其余静态文件走默认。
                if full_path.startswith("assets/"):
                    return _FileResponse(
                        target, headers={"Cache-Control": "public, max-age=31536000, immutable"}
                    )
                return _FileResponse(target)
        except (OSError, ValueError):
            pass  # 非法路径（含 .. / 编码异常）→ 当作前端路由处理

    if _SPA_INDEX.is_file():
        # index.html **绝不能缓存**：它决定加载哪个 hash 的 JS。
        # 若被浏览器缓存，前端重新构建后用户仍会拿到旧 JS → 表现为「改了看不到」
        # 甚至旧 JS + 新接口不匹配而白屏。
        return _FileResponse(_SPA_INDEX, headers={"Cache-Control": "no-cache, must-revalidate"})
    return JSONResponse(
        {"detail": {"code": "NOT_FOUND", "message": "页面不存在（前端 dist 未构建）"}},
        status_code=404,
    )
