"""切片级冲突隔离专项验证。

核心诉求：**一个条款冲突，只隔离那一片**，同文档其余条款照常可检索。
旧实现是写 doc.has_conflict → 整篇从检索里消失（「一小点废掉一大段」）。

用法：python tests/verify_slice_conflict.py
"""

from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

B = "http://127.0.0.1:8000/api/v1"
_ok = _fail = 0
H: dict[str, str] = {}


def check(cond: bool, name: str, extra: str = "") -> None:
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"  [PASS] {name}")
    else:
        _fail += 1
        print(f"  [FAIL] {name}  {extra}")


def _login() -> str:
    r = requests.post(
        f"{B}/auth/login",
        json={"username": "admin", "password": "admin123456"},
        timeout=20,
    )
    return r.json()["access_token"]


def upload(kb_id: int, name: str, text: str) -> dict | None:
    """上传并等待后台任务完成（异步受理 → 轮询）。"""
    r = requests.post(
        f"{B}/kb/{kb_id}/documents",
        headers=H,
        files={"file": (name, text.encode("utf-8"), "text/markdown")},
        data={"is_public": "true", "tags": "[]"},
        timeout=90,
    )
    if r.status_code != 202:
        print("    上传失败:", r.status_code, r.text[:180])
        return None
    tid = r.json()["task_id"]
    for _ in range(150):
        t = requests.get(f"{B}/tasks/{tid}", headers=H, timeout=20).json()
        if t.get("status") in ("done", "failed"):
            return t
        time.sleep(1)
    return None


def search(kb_id: int, q: str) -> list[dict]:
    r = requests.post(
        f"{B}/search",
        headers={**H, "Content-Type": "application/json"},
        json={"query": q, "kb_ids": [kb_id]},
        timeout=90,
    )
    return r.json().get("results") or []


def main() -> int:
    global H
    H = {"Authorization": f"Bearer {_login()}"}
    ts = uuid.uuid4().hex[:6]
    kb = requests.post(
        f"{B}/kb",
        headers=H,
        json={"name": f"切片级冲突验证_{ts}", "description": "专项验证"},
        timeout=20,
    ).json()
    kb_id = kb["id"]

    print("=" * 72)
    print("切片级冲突隔离专项验证")
    print("=" * 72)

    common = [
        "第一条 差旅住宿标准：一线城市 600 元/晚，二线城市 400 元/晚。",
        "第二条 餐饮补助标准：每人每天 100 元，超标部分自理。",
        "第四条 报销须在费用发生后 30 天内提交，逾期不予受理。",
        "第五条 报销发票必须为增值税专用发票，否则不予报销。",
    ]
    # ⚠️ 条款之间必须用**空行**分隔：Markdown 把连续非空行视为同一个段落块，
    #    不加空行会导致整篇被切成一个切片（那样隔离一片 = 隔离全篇）。
    body_a = (
        "# 报销制度\n\n"
        "第三条 单次报销上限 500 元，超出部分需部门总监审批。\n\n" + "\n\n".join(common)
    )
    # ⚠️ 修订版的非冲突条款故意多一句「三线城市」：两版若有切片内容完全相同，
    #    向量无区分度，检索只会召回其中一条 —— 无法判断另一版是否可检索。
    #    用一个独有词，才能确定性地验证「修订版的非冲突切片仍参与检索」。
    body_b = (
        "# 报销制度（修订）\n\n"
        "第三条 单次报销上限 1200 元，超出部分需部门总监审批。\n\n"
        + common[0].replace("。", "，三线城市 300 元/晚。")
        + "\n\n"
        + "\n\n".join(common[1:])
    )

    try:
        ta = upload(kb_id, f"报销制度_原版_{ts}.md", body_a)
        check(bool(ta) and ta.get("status") == "done", f"上传原版（{(ta or {}).get('status')}）")
        tb = upload(kb_id, f"报销制度_修订版_{ts}.md", body_b)
        check(bool(tb) and tb.get("status") == "done", f"上传修订版（{(tb or {}).get('status')}）")

        res = requests.get(f"{B}/conflicts", headers=H, params={"kb_id": kb_id}, timeout=30).json()
        items = res.get("items") or res.get("data") or []
        check(len(items) >= 1, f"检出冲突 {len(items)} 条（期望 >=1）")
        if not items:
            print("  ⚠ 未检出冲突，无法验证隔离行为（可能与模型判定有关）")
            return 1

        new_doc = items[0].get("new_doc_id")
        old_doc = items[0].get("old_doc_id")
        print(f"    冲突对：new_doc={new_doc} vs old_doc={old_doc}")

        # 诊断：打印两文档的切片隔离状态
        from sqlalchemy import select

        from app.core.database import SessionLocal
        from app.models import Document, DocumentChunk

        _db = SessionLocal()
        blocked_ids: set[int] = set()
        try:
            for _id in (old_doc, new_doc):
                _d = _db.get(Document, _id)
                if _d is None:
                    continue
                _rows = (
                    _db.execute(
                        select(DocumentChunk)
                        .where(DocumentChunk.doc_id == _id)
                        .order_by(DocumentChunk.chunk_index)
                    )
                    .scalars()
                    .all()
                )
                print(
                    f"    #{_id} root={_d.root_id} v={_d.version_no} "
                    f"active={_d.is_active} conflict={_d.has_conflict}"
                )
                for _c in _rows:
                    if not _c.is_retrievable:
                        blocked_ids.add(_c.id)
                    print(
                        f"      [{_c.chunk_index}] retr={_c.is_retrievable} "
                        f"reason={_c.retired_reason} {(_c.content or '')[:44]!r}"
                    )
        finally:
            _db.close()

        # ⭐ 核心断言：修订版的**非冲突切片**必须仍可检索（旧实现整篇消失）
        r1 = search(kb_id, "三线城市 住宿标准")
        ids1 = {x.get("doc_id") for x in r1}
        check(
            new_doc in ids1,
            "修订版的非冲突条款仍可检索 ⇒ 文档级挂起已消除",
            f"命中 doc_ids={ids1}",
        )

        # 原版文档不受影响（切片级隔离不牵连他人）
        r1b = search(kb_id, "餐饮补助标准")
        ids1b = {x.get("doc_id") for x in r1b}
        check(
            old_doc in ids1b,
            "原版文档仍可检索（隔离不牵连其他文档）",
            f"命中 doc_ids={ids1b}",
        )

        # 冲突条款应被隔离 —— 精确到**切片**（同文档其余切片本就该可检索）
        r2 = search(kb_id, "单次报销上限 1200 元 总监审批")
        hit2 = {x.get("chunk_id") for x in r2}
        check(
            bool(blocked_ids) and not (blocked_ids & hit2),
            f"冲突切片已被隔离（隔离 {len(blocked_ids)} 片，检索不召回）",
            f"隔离={blocked_ids} 命中={hit2}",
        )

        # 处理冲突：coexist → 隔离解除
        cid = items[0].get("id")
        r3 = requests.post(
            f"{B}/conflicts/{cid}/resolve",
            headers={**H, "Content-Type": "application/json"},
            json={"resolution": "coexist"},
            timeout=60,
        )
        check(r3.status_code == 200, f"处理冲突 coexist（HTTP {r3.status_code}）")
        time.sleep(2)

        r4 = search(kb_id, "单次报销上限 1200 元 总监审批")
        hit4 = {x.get("chunk_id") for x in r4}
        check(
            bool(blocked_ids & hit4),
            "coexist 后冲突切片恢复可检索",
            f"曾被隔离={blocked_ids} 命中={hit4}",
        )
    finally:
        requests.delete(f"{B}/kb/{kb_id}", headers=H, timeout=60)

    print("-" * 72)
    print(f"  结果：{_ok}/{_ok + _fail} 通过")
    print("=" * 72)
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
