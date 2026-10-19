"""问答 / 文档任务用的临时文件底座（**不是知识库文档**）。

和知识库文档的区别（别混用）：

| | 知识库文档 `data/uploads` | 本模块 `data/doc_files` |
|---|---|---|
| 用途 | 长期资产，切片入库、可检索 | **一次性**：这次问答/这份任务用 |
| 入库 | 解析 → 切片 → 向量化 → 冲突检测 | 只解析，不切片不向量化 |
| 生命周期 | 长期 | 默认 30 天清理 |
| 可见性 | 按库权限 | 只属于上传者本人 |

存储布局（一个文件一个目录，删起来干净）：

    data/doc_files/u<用户id>/<file_id>/
        orig<扩展名>      原件（下载就是它）
        meta.json         元信息（名称/大小/sha256/解析概要/时间）
        parsed.json       解析结果（blocks，供 /blocks 与长文处理复用）

为什么用 JSON sidecar 而不是建表（这是一个明确的取舍，不是偷懒）：
- 服务是**单进程**（8200 一个进程，见 README），不存在多进程写竞争；
- 文件是临时资产，量在千级以内，扫目录完全够；
- 不引入建表/迁移成本（本项目 `db_init.py` 手工迁移，多一张表就多一处要维护）。
代价：将来要"全站按文件名搜文件""按用户统计"这类查询时，得改成 DB 表 —— 到那时再改。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services.ingestion import parsers
from app.services.ingestion.parsers import ParsedDocument, ParserError

# 用相对路径与现有 upload_dir（./data/uploads）保持同一口径：
# 启动脚本的工作目录就是仓库根，换机器/换盘只改 .env 的 DOC_FILES_DIR。
ROOT = Path(os.environ.get("DOC_FILES_DIR") or "./data/doc_files")

_MAX_PREVIEW = 200
_UNSAFE_NAME = re.compile(r'[\\/:*?"<>|\r\n\t]')


class DocFileError(RuntimeError):
    """文件层的业务错误（路由层直接翻成 HTTPException）。"""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


# ── 路径与序列化 ───────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _user_dir(user_id: int) -> Path:
    d = ROOT / f"u{int(user_id)}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _job_dir(user_id: int, file_id: str) -> Path:
    return _user_dir(user_id) / file_id


def _read_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, data: dict) -> None:
    # 先写临时文件再替换：中途崩了不会留下半截 JSON（读的时候会炸）
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def safe_name(filename: str) -> str:
    """原名留给人看；落盘只允许安全字符，防止路径穿越。"""
    name = (filename or "").strip().replace("\\", "/").split("/")[-1]
    name = _UNSAFE_NAME.sub("_", name)
    return name or "unnamed"


def ext_of(filename: str) -> str:
    return Path(safe_name(filename)).suffix.lower()


# ── 解析 ───────────────────────────────────────────────────────

def _parse(orig: Path) -> tuple[ParsedDocument | None, dict]:
    """解析原件。**失败不抛**——文件已经落盘，让人能先用起来（见 save 的说明）。"""
    try:
        doc = parsers.parse_file(orig)
    except ParserError as exc:
        return None, {"ok": False, "error": str(exc)}
    except Exception as exc:                        # noqa: BLE001
        return None, {"ok": False, "error": f"解析异常：{type(exc).__name__}: {exc}"}

    headings = sum(1 for b in doc.blocks if b.block_type == "heading")
    tables = sum(1 for b in doc.blocks if b.block_type == "table")
    text = doc.full_text
    info = {
        "ok": True,
        "chars": doc.char_count,
        "blocks": len(doc.blocks),
        "headings": headings,
        "tables": tables,
        "preview": text[:_MAX_PREVIEW],
        "warnings": list(doc.meta.get("warnings") or []),
        "meta": {k: v for k, v in doc.meta.items() if isinstance(v, (int, float, str))},
    }
    return doc, info


def _blocks_payload(doc: ParsedDocument | None) -> dict:
    if doc is None:
        return {"blocks": []}
    return {
        "title": doc.title,
        "blocks": [
            {
                "para_no": i,
                "text": b.text,
                "heading_path": b.heading_path,
                "block_type": b.block_type,
                "level": b.level,
            }
            for i, b in enumerate(doc.blocks, start=1)
        ],
    }


# ── 对外操作 ───────────────────────────────────────────────────

def save(
    user_id: int,
    filename: str,
    content: bytes,
    *,
    user_code: str = "",
    purpose: str = "chat",
) -> dict:
    """落盘 + 解析 + 记元信息。同用户同内容（sha256 相同）直接复用已有文件。

    ⚠️ 解析失败**仍然返回可用的 file_id**（meta.parsed.ok=false）：
       用户上传的是扫描件时，最想要的是"先问起来 + 知道为什么解析不了"，
       直接 500 会让人以为系统坏了。
    """
    if not user_id:
        raise DocFileError("NOT_AUTHENTICATED", "缺少用户身份", 401)
    if not isinstance(content, (bytes, bytearray)):
        # 调用方传错类型时给一句人话，别让它变成 sha256(TypeError) 这种莫名报错
        raise DocFileError("BAD_CONTENT", f"文件内容必须是字节，收到 {type(content).__name__}")
    content = bytes(content)

    name = safe_name(filename)
    ext = ext_of(name)
    if ext not in parsers.SUPPORTED_EXTENSIONS:
        raise DocFileError(
            "UNSUPPORTED_FORMAT",
            f"不支持的文件格式：{ext or '(无扩展名)'}。"
            f"支持：{', '.join(sorted(parsers.SUPPORTED_EXTENSIONS))}",
        )

    sha = hashlib.sha256(content).hexdigest()
    user_dir = _user_dir(user_id)

    # 同用户重复上传同一份文件 → 复用（省一次解析，也省一份磁盘）
    for meta_path in user_dir.glob("*/meta.json"):
        try:
            existing = _read_json(meta_path)
        except (OSError, json.JSONDecodeError):
            continue
        if existing.get("sha256") == sha and existing.get("ext") == ext:
            return public(existing)

    file_id = "f_" + uuid.uuid4().hex[:12]
    d = _job_dir(user_id, file_id)
    d.mkdir(parents=True, exist_ok=True)
    orig = d / f"orig{ext}"

    try:
        orig.write_bytes(content)
    except OSError as exc:
        raise DocFileError("DISK_ERROR", f"文件写入失败：{exc}", 500) from exc

    doc, parsed = _parse(orig)

    meta = {
        "file_id": file_id,
        "user_id": int(user_id),
        "user_code": user_code or "",
        "name": name,
        "ext": ext,
        "size": len(content),
        "sha256": sha,
        "purpose": purpose,
        "created_at": _now(),
        "parsed": parsed,
    }
    _write_json(d / "meta.json", meta)
    _write_json(d / "parsed.json", _blocks_payload(doc))
    return public(meta)


def load(file_id: str, user_id: int | None = None) -> dict:
    """取元信息。传了 user_id 就校验归属（**服务端校验，不信入参**）。"""
    fid = (file_id or "").strip()
    if not fid or not re.fullmatch(r"f_[0-9a-f]{12}", fid):
        raise DocFileError("FILE_NOT_FOUND", "文件不存在", 404)

    if user_id is None:
        # 不知道是谁 → 只能扫所有用户目录（仅在内部调用时使用）
        for meta_path in ROOT.glob(f"*/{fid}/meta.json"):
            return _read_json(meta_path)
        raise DocFileError("FILE_NOT_FOUND", "文件不存在", 404)

    meta_path = _job_dir(int(user_id), fid) / "meta.json"
    if not meta_path.exists():
        raise DocFileError("FILE_NOT_FOUND", "文件不存在或不属于当前用户", 404)
    return _read_json(meta_path)


def public(meta: dict) -> dict:
    """对外形态（去掉 user_id / 内部路径）。"""
    return {
        "file_id": meta["file_id"],
        "name": meta["name"],
        "ext": meta["ext"],
        "size": meta["size"],
        "sha256": meta["sha256"],
        "purpose": meta.get("purpose", "chat"),
        "created_at": meta["created_at"],
        "parsed": meta.get("parsed", {"ok": False}),
    }


def orig_path(meta: dict) -> Path:
    path = _job_dir(meta["user_id"], meta["file_id"]) / f"orig{meta['ext']}"
    if not path.exists():
        raise DocFileError("FILE_NOT_FOUND", "原件已过期或被清理，请重新上传", 404)
    return path


def blocks(file_id: str, user_id: int, *, offset: int = 0, limit: int = 50) -> dict:
    meta = load(file_id, user_id)
    pj = _job_dir(meta["user_id"], meta["file_id"]) / "parsed.json"
    if not pj.exists():
        return {"title": meta["name"], "blocks": [], "total": 0}
    data = _read_json(pj)
    items = data.get("blocks", [])
    offset = max(0, int(offset or 0))
    limit = max(1, min(int(limit or 50), 500))
    return {
        "title": data.get("title") or meta["name"],
        "blocks": items[offset:offset + limit],
        "total": len(items),
        "offset": offset,
        "limit": limit,
    }


def plain_text(file_id: str, user_id: int, *, with_heading: bool = True) -> str:
    """整篇纯文本（带标题路径）。

    长文不要直接拿去喂模型 —— 20 页就是两三万字，内网模型装不下。
    用 `blocks()` + `doc_context` 里的召回逻辑取相关段落。
    """
    meta = load(file_id, user_id)
    doc = blocks(file_id, user_id, offset=0, limit=100000)
    lines: list[str] = []
    for b in doc["blocks"]:
        if b["block_type"] == "heading":
            lines.append("")
            lines.append(b["text"])
            continue
        lines.append(b["text"] if not with_heading or not b["heading_path"]
                     else f"[{b['heading_path']}] {b['text']}")
    return "\n".join(lines).strip()


def list_files(user_id: int, *, page: int = 1, page_size: int = 20) -> dict:
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 20), 100))
    metas: list[dict] = []
    for meta_path in _user_dir(user_id).glob("*/meta.json"):
        try:
            metas.append(_read_json(meta_path))
        except (OSError, json.JSONDecodeError):
            continue
    metas.sort(key=lambda m: m.get("created_at", ""), reverse=True)
    start = (page - 1) * page_size
    return {
        "items": [public(m) for m in metas[start:start + page_size]],
        "total": len(metas),
        "page": page,
        "page_size": page_size,
    }


def delete(file_id: str, user_id: int) -> None:
    meta = load(file_id, user_id)
    shutil.rmtree(_job_dir(meta["user_id"], meta["file_id"]), ignore_errors=True)


def purge_expired(days: int = 30) -> int:
    """清理过期文件（不挂定时任务，由运维/启动脚本调用；返回清理数量）。"""
    if days <= 0:
        return 0
    deadline = datetime.now(timezone.utc).astimezone() - timedelta(days=days)
    removed = 0
    for meta_path in ROOT.glob("*/**/meta.json"):
        try:
            meta = _read_json(meta_path)
            created = datetime.fromisoformat(meta.get("created_at", ""))
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if created < deadline:
            shutil.rmtree(meta_path.parent, ignore_errors=True)
            removed += 1
    return removed
