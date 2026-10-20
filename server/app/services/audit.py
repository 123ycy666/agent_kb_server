"""审计日志写入。

⚠️ 关键要求
-----------
检索类操作（search / chat）的 detail 中**必须包含 hit_doc_ids**。
否则当机密文档外泄时，无法追溯「谁通过搜索看到了它」——
这是企业级审计的核心留痕点，也是本系统区别于玩具 Demo 的地方。
"""

from __future__ import annotations

import ipaddress
from typing import Any

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import CurrentActor
from app.models import AuditLog


def _safe_ip(value: str | None) -> str | None:
    """把客户端 IP 归一成 audit_log.ip 能接受的合法值，非法则降级为 None。

    ⚠️ 这里必须兜住，否则**登录会直接 500**：

    1. `audit_log.ip` 是 PG 的 `inet` 类型，写入 `"testclient"`、`"unknown"` 这类
       非 IP 字符串会抛 `DataError`；而审计在登录流程里是**同步落库**的，
       异常会一路冒到接口，用户只看到"登录失败"。
    2. `X-Forwarded-For` 由**客户端可控** —— 不校验就等于给外部开了一个
       "塞垃圾值打崩登录口"的廉价开关（审计写入失败 → 500）。
       注意：校验只解决崩溃；XFF 的**可信度**仍需在反向代理层保证
       （只信任自己那层代理覆盖的值），否则审计里的 IP 可被伪造。
    """
    if not value:
        return None
    candidate = value.strip()
    try:
        ipaddress.ip_address(candidate)
        return candidate
    except ValueError:
        pass
    # 兼容 "1.2.3.4:5678" 这种带端口的写法（IPv4 只有一个冒号）
    host, sep, port = candidate.rpartition(":")
    if sep and port.isdigit() and host:
        try:
            ipaddress.ip_address(host)
            return host
        except ValueError:
            pass
    return None


class AuditAction:
    """动作码常量。命名规则：资源.操作。"""

    # 认证
    LOGIN = "auth.login"
    LOGIN_FAILED = "auth.login_failed"
    LOGOUT = "auth.logout"
    API_AUTH_FAILED = "auth.api_failed"

    # 知识库
    KB_CREATE = "kb.create"
    KB_UPDATE = "kb.update"
    KB_DELETE = "kb.delete"

    # 文档
    DOC_UPLOAD = "doc.upload"
    DOC_UPDATE = "doc.update"
    DOC_DELETE = "doc.delete"
    DOC_DOWNLOAD = "doc.download"
    DOC_TAG_UPDATE = "doc.tag_update"
    DOC_VERSION_SWITCH = "doc.version_switch"
    DOC_REINDEX = "doc.reindex"

    # 权限
    ACL_GRANT = "acl.grant"
    ACL_REVOKE = "acl.revoke"
    ROLE_CREATE = "role.create"
    ROLE_UPDATE = "role.update"
    ROLE_DELETE = "role.delete"

    # 检索
    SEARCH = "search"
    CHAT = "chat"
    # 历史对话删除（2026-10-20 新增）：删除会让人看不到自己的问答记录，
    # 属于「事后难以对证」的操作，必须留痕（谁、什么时候、删的哪一场）。
    CHAT_DELETE = "chat.delete"

    # 冲突
    CONFLICT_CREATE = "conflict.create"
    CONFLICT_RESOLVE = "conflict.resolve"
    CONFLICT_SCAN = "conflict.scan"

    # 标签治理
    TAG_CREATE = "tag.create"
    TAG_UPDATE = "tag.update"
    TAG_MERGE = "tag.merge"
    TAG_DEPRECATE = "tag.deprecate"

    # 运维
    FULL_CONSISTENCY_SCAN = "system.full_consistency_scan"
    INDEX_REBUILD = "system.index_rebuild"

    # 应用管理
    APP_CREATE = "app.create"
    APP_UPDATE = "app.update"
    APP_REVOKE = "app.revoke"


def log_action(
    db: Session,
    *,
    actor: CurrentActor | None,
    action: str,
    resource_type: str | None = None,
    resource_id: int | None = None,
    resource_name: str | None = None,
    detail: dict[str, Any] | None = None,
    result: str = "success",
    request: Request | None = None,
    commit: bool = False,
) -> AuditLog:
    """写入一条审计日志。

    Parameters
    ----------
    commit
        是否立即提交。默认 False，由调用方在同一个事务里提交；
        若审计需要独立于业务事务（如登录失败），传 True。
    """
    entry = AuditLog(
        actor_type=actor.type if actor else "system",
        actor_id=actor.user_id if actor else None,
        actor_name=actor.username if actor else None,
        actor_app_id=None,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        resource_name=resource_name,
        detail=detail,
        result=result,
    )

    if request is not None:
        forwarded = request.headers.get("x-forwarded-for")
        entry.ip = _safe_ip(
            forwarded.split(",")[0].strip()
            if forwarded
            else (request.client.host if request.client else None)
        )
        ua = request.headers.get("user-agent")
        entry.user_agent = ua[:256] if ua else None

    db.add(entry)
    if commit:
        db.commit()
    return entry


def log_search(
    db: Session,
    *,
    actor: CurrentActor,
    query: str,
    hit_doc_ids: list[int],
    kb_ids: list[int],
    result_count: int,
    filters: dict[str, Any] | None = None,
    took_ms: int = 0,
    request: Request | None = None,
    is_chat: bool = False,
) -> AuditLog:
    """记录检索行为（含命中文档 ID）。"""
    return log_action(
        db,
        actor=actor,
        action=AuditAction.CHAT if is_chat else AuditAction.SEARCH,
        resource_type="search",
        detail={
            "query": query[:500],
            "kb_ids": kb_ids,
            "hit_doc_ids": sorted(set(hit_doc_ids)),
            "hit_count": len(set(hit_doc_ids)),
            "result_count": result_count,
            "filters": filters or {},
            "took_ms": took_ms,
        },
        request=request,
    )


# ══════════════════════════════════════════════════════════════
#  查询与统计
# ══════════════════════════════════════════════════════════════

ACTION_LABELS: dict[str, tuple[str, str]] = {
    AuditAction.LOGIN: ("登录", "认证"),
    AuditAction.LOGIN_FAILED: ("登录失败", "认证"),
    AuditAction.API_AUTH_FAILED: ("API 鉴权失败", "认证"),
    AuditAction.KB_CREATE: ("创建知识库", "知识库"),
    AuditAction.KB_UPDATE: ("修改知识库", "知识库"),
    AuditAction.KB_DELETE: ("删除知识库", "知识库"),
    AuditAction.DOC_UPLOAD: ("上传文档", "文档"),
    AuditAction.DOC_UPDATE: ("修改文档", "文档"),
    AuditAction.DOC_DELETE: ("删除文档", "文档"),
    AuditAction.DOC_DOWNLOAD: ("下载文档", "文档"),
    AuditAction.DOC_TAG_UPDATE: ("修改文档标签", "文档"),
    AuditAction.DOC_REINDEX: ("重建文档索引", "文档"),
    AuditAction.ACL_GRANT: ("文档授权", "权限"),
    AuditAction.ACL_REVOKE: ("回收文档权限", "权限"),
    AuditAction.ROLE_CREATE: ("创建角色", "权限"),
    AuditAction.ROLE_UPDATE: ("修改角色", "权限"),
    AuditAction.ROLE_DELETE: ("删除角色", "权限"),
    AuditAction.SEARCH: ("检索", "检索"),
    AuditAction.CHAT: ("问答", "检索"),
    AuditAction.CONFLICT_CREATE: ("检出冲突", "冲突"),
    AuditAction.CONFLICT_RESOLVE: ("处理冲突", "冲突"),
    AuditAction.CONFLICT_SCAN: ("冲突扫描", "冲突"),
    AuditAction.TAG_CREATE: ("创建标签", "标签"),
    AuditAction.TAG_UPDATE: ("修改标签", "标签"),
    AuditAction.TAG_MERGE: ("合并标签", "标签"),
    AuditAction.TAG_DEPRECATE: ("废弃标签", "标签"),
    AuditAction.FULL_CONSISTENCY_SCAN: ("全库一致性扫描", "运维"),
    AuditAction.INDEX_REBUILD: ("重建索引", "运维"),
    AuditAction.APP_CREATE: ("创建 API 应用", "应用"),
    AuditAction.APP_UPDATE: ("修改 API 应用", "应用"),
    AuditAction.APP_REVOKE: ("吊销 API 密钥", "应用"),
}


def get_action_catalog() -> list[dict[str, str]]:
    """返回动作码目录（供前端筛选下拉）。"""
    return [
        {"action": code, "label": label, "group": group}
        for code, (label, group) in ACTION_LABELS.items()
    ]


def get_audit_stats(db: Session) -> dict[str, Any]:
    """审计概览统计。"""
    from datetime import UTC, datetime, timedelta

    total = int(db.execute(select(func.count(AuditLog.id))).scalar_one())
    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    today = int(
        db.execute(
            select(func.count(AuditLog.id)).where(AuditLog.created_at >= today_start)
        ).scalar_one()
    )

    by_action = {
        row[0]: row[1]
        for row in db.execute(
            select(AuditLog.action, func.count(AuditLog.id)).group_by(AuditLog.action)
        ).all()
    }
    by_actor = {
        row[0]: row[1]
        for row in db.execute(
            select(AuditLog.actor_type, func.count(AuditLog.id)).group_by(AuditLog.actor_type)
        ).all()
    }
    return {"total": total, "today": today, "by_action": by_action, "by_actor_type": by_actor}
