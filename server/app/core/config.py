"""全局配置。

所有配置集中在此，通过环境变量或 .env 文件注入。
其中 ``MODEL_CHANNEL`` 是整个模型适配层的总开关（mock / local / online 三通道）。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（server/ 的上一级）
BASE_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BASE_DIR.parent

# 模型通道：mock（零依赖演示）/ local（内网自建）/ online（线上 API）
ProviderMode = Literal["mock", "local", "online"]


class Settings(BaseSettings):
    """应用配置。字段名不区分大小写，对应 .env 中的大写键名。"""

    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env", BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ─── 应用 ────────────────────────────────────────────────
    app_name: str = "企业级本地知识库系统"
    app_env: str = "development"
    debug: bool = True
    api_v1_prefix: str = "/api/v1"

    # ─── 数据库 ──────────────────────────────────────────────
    database_url: str = "postgresql+psycopg://kb:kb_dev_pwd@127.0.0.1:5433/kb_system"
    db_echo: bool = False

    # ─── MySQL（员工表 / 业务库 / 字典库 / 记录库）────────────
    # 统一用户表权威源在 MySQL：dlj_data.t_employee
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str = ""
    mysql_charset: str = "utf8mb4"
    mysql_db_business: str = "dlj_data"   # 业务数据（只读）
    mysql_db_dict: str = "ai_data"        # 业务字典与系统提示词（只读）
    mysql_db_agent: str = "agent_data"    # agent 对话记录与纠错词典（读写）

    # ─── 安全 ────────────────────────────────────────────────
    jwt_secret: str = "dev-only-jwt-secret-please-change-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 720

    # ─── ⭐ 模型通道总开关 ────────────────────────────────────
    # mock   = 零依赖演示（确定性伪向量，验证链路不评估质量）
    # local  = 公司内网自建（vLLM / Xinference / Ollama，OpenAI 兼容）
    # online = 线上 API（阿里云百炼 compatible-mode）
    # ⚠️ 切换 embedding 通道后向量空间不兼容，必须重建索引。
    model_channel: ProviderMode = "local"
    embedding_dim: int = Field(default=1024, ge=1)

    # 向量化的**分批并发度**（2026-10-15 新增）。
    # online 档单请求硬限 10 条，长文档只能分批；串行分批是上传慢的主因。
    # 设 1 = 退回串行（部分内网 embedding 服务不支持并发时用）。
    # ⚠️ 不要调太高：在线档过并发易触发 429 限流；8~10 是提速与限流的平衡点。
    embed_batch_concurrency: int = Field(default=8, ge=1)

    @field_validator("model_channel", mode="before")
    @classmethod
    def _accept_legacy_provider_mode(cls, v: object) -> object:
        """兼容旧键名 PROVIDER_MODE → MODEL_CHANNEL。"""
        return v

    # ─── online 档：线上 API（阿里云百炼 compatible-mode）────
    # embedding/rerank/LLM 三套端点与 Key 独立配置，便于分别切换。
    online_llm_base_url: str = ""
    online_llm_api_key: str = ""
    online_llm_model: str = "deepseek-v4.1-flash-1017"
    # 冲突判定**专用**模型（2026-10-15 新增）。留空 = 跟随 online_llm_model。
    # ⚠️ 为什么必须能拆：online_llm_model 同时服务「知识库问答」与「问数 agent」，
    #    那两处需要强模型（工具调用、长回答最稳）；而冲突判定只是四分类关系判断，
    #    用快模型即可（实测同网关 qwen-plus 比 qwen3.8-max 快约 4 倍）。
    #    一个键管三处，会把「优化判定速度」变成「顺带把问答与 agent 一起降级」。
    online_judge_model: str = ""
    online_embedding_base_url: str = ""
    online_embedding_api_key: str = ""
    online_embedding_model: str = "text-embedding-v4"
    online_rerank_base_url: str = ""
    online_rerank_api_key: str = ""
    online_rerank_model: str = "gte-rerank-v2"

    # ─── local 档：公司本地部署 ───────────────────────────────
    local_embedding_url: str = "http://127.0.0.1:9997/v1/embeddings"
    local_embedding_model: str = "bge-m3"
    # embedding/rerank 服务的鉴权 Key（部分内网网关强制 Bearer）。
    # 留空 = 不发 Authorization 头（兼容无需鉴权的 vLLM/Ollama）。
    local_embedding_api_key: str = ""
    local_rerank_url: str = "http://127.0.0.1:9997/v1/rerank"
    local_rerank_model: str = "bge-rerank-m3"
    local_rerank_api_key: str = ""
    local_llm_base_url: str = "http://127.0.0.1:8000/v1"
    local_llm_model: str = "deepseek-chat"
    local_llm_api_key: str = ""
    # 冲突判定专用模型（local 档）。留空 = 跟随 local_llm_model。
    local_judge_model: str = ""

    # ─── 文件存储 ────────────────────────────────────────────
    upload_dir: str = "./data/uploads"
    max_upload_mb: int = 100

    # ─── 冲突检测 ────────────────────────────────────────────
    # 上传后是否**自动**做冲突检测。
    # ⚠️ 关掉它等于「新文档永远标不出冲突」，与人工逐份点检测无异 ——
    #    除非在跑批量导入（不想为每份文档付 LLM 调用），否则应保持开启。
    auto_conflict_check: bool = True
    # 冲突判定的 LLM **并发度**（2026-10-15 新增，原硬编码在 detector.py）。
    # 判定彼此独立，并发后总耗时 ≈ 最慢那次。实测单次 qwen 判定 8~20s。
    # ⚠️ 过高会触发在线 LLM 限流（429）；8 左右兼顾提速与稳定。
    conflict_judge_concurrency: int = Field(default=8, ge=1)
    # 冲突候选**精排（rerank）并发度**（2026-10-15 新增）。
    # ⚠️ 精排是「每个新切片一次请求」（rerank 接口不支持多 query 批处理），
    #    大文档的候选组数可达数百 —— 串行时这是仅次于向量化的第二大耗时源。
    conflict_rerank_concurrency: int = Field(default=8, ge=1)
    # 单次冲突检测**精排的候选组数上限**（防病态输入打爆外部 rerank 服务）。
    # 超出时按「组内最高向量相似度」取前 N 组 —— 它们覆盖最可能冲突的候选。
    conflict_rerank_max_groups: int = Field(default=200, ge=1)
    # 单次检测允许的 LLM 判定上限（安全阀）。
    # ⚠️ 自动检测在**上传请求内同步执行**，若不设上限，
    #    一份超长文档会产生几十次 LLM 调用，把上传接口拖到几十秒 ——
    #    用户会以为系统卡死。超出上限时返回警告，并建议管理员跑全库扫描。
    conflict_max_llm_calls: int = 12

    # ─── CORS ────────────────────────────────────────────────
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    # 我们用 Authorization 头传 JWT、不用 cookie ⇒ 关掉 credentials 才能用 allow_origins=*
    cors_allow_credentials: bool = False

    # ── 第三方系统免密换 token（sga 前端直连用，2026-10-19）────
    # 开启后 POST /api/v1/auth/sso/login 可凭 login_name 直接换本平台 JWT。
    # ⚠️ 等于「知道用户名就能拿 token」，仅限内网/受信系统；对外暴露前关闭或配置 SSO_LOGIN_KEY。
    sso_login_enabled: bool = True
    sso_login_key: str = ""

    # ══════════════════════════════════════════════════════════
    #  派生属性
    # ══════════════════════════════════════════════════════════

    @field_validator("api_v1_prefix")
    @classmethod
    def _normalize_prefix(cls, v: str) -> str:
        if not v.startswith("/"):
            v = "/" + v
        return v.rstrip("/")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def upload_path(self) -> Path:
        """上传目录绝对路径，不存在时自动创建。"""
        p = Path(self.upload_dir)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def is_mock(self) -> bool:
        return self.model_channel == "mock"

    @property
    def provider_mode(self) -> str:
        """兼容旧代码：等价于 model_channel。"""
        return self.model_channel

    def provider_summary(self) -> dict[str, str]:
        """返回当前生效的模型配置摘要（用于 /models/config 接口，已脱敏）。"""
        mode = self.model_channel
        if mode == "mock":
            return {
                "mode": mode,
                "embedding": f"mock-embedding-{self.embedding_dim}d",
                "rerank": "mock-rerank",
                "llm": "mock-llm",
            }
        if mode == "online":
            return {
                "mode": mode,
                "embedding": f"online/{self.online_embedding_model}",
                "rerank": f"online/{self.online_rerank_model}",
                "llm": f"online/{self.online_llm_model}",
            }
        return {
            "mode": mode,
            "embedding": f"local/{self.local_embedding_model}",
            "rerank": f"local/{self.local_rerank_model}",
            "llm": f"local/{self.local_llm_model}",
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """带缓存的配置单例。"""
    return Settings()


settings = get_settings()


# ══════════════════════════════════════════════════════════════
#  .env 回写
# ══════════════════════════════════════════════════════════════


def env_file_path() -> Path | None:
    """返回实际生效的 .env 路径（与 Settings 的 env_file 顺序一致）。"""
    for candidate in (PROJECT_ROOT / ".env", BASE_DIR / ".env"):
        if candidate.exists():
            return candidate
    return None


def persist_model_channel(mode: str) -> bool:
    """把 MODEL_CHANNEL 写回 .env，使界面上的通道切换能持久生效。

    为什么需要它
    ------------
    界面切换通道原本只改内存，重启即回退到 .env ——
    用户会困惑「明明切了怎么又变回去」。写回 .env 后两处保持一致。

    ⚠️ 只替换已存在的 MODEL_CHANNEL 行（不存在则追加），
       不重写整个文件，保留用户写的注释与其它配置。

    :return: 是否写入成功（失败不影响本次运行时切换）
    """
    path = env_file_path()
    if path is None:
        return False

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return False

    lines = raw.splitlines(keepends=True)
    found = False
    out: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if not stripped.startswith("#") and stripped.startswith("MODEL_CHANNEL="):
            # 保留原有行尾风格
            ending = "\n" if line.endswith("\n") else ""
            out.append(f"MODEL_CHANNEL={mode}{ending}")
            found = True
        else:
            out.append(line)

    if not found:
        if out and not out[-1].endswith("\n"):
            out.append("\n")
        out.append(f"MODEL_CHANNEL={mode}\n")

    try:
        path.write_text("".join(out), encoding="utf-8")
    except OSError:
        return False
    return True
