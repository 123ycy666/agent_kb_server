"""回归验证：overwrite 后删除新文档，被覆盖的旧内容必须恢复可检索。

用户报告的缺陷：
    ① 上传与旧文档冲突的新文件 → 处理为「覆盖」（新内容取代旧内容）
    ② 再删除这个新文件
    ③ 被覆盖的那段旧内容**依旧检索不到**（永久失联）

用法：python tests/verify_overwrite_rollback.py
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
    r = requests.post(
        f"{B}/kb/{kb_id}/documents",
        headers=H,
        files={"file": (name, text.encode("utf-8"), "text/markdown")},
        data={"is_public": "true", "tags": "[]"},
        timeout=90,
    )
    if r.status_code != 202:
        print("    上传失败:", r.status_code, r.text[:160])
        return None
    tid = r.json()["task_id"]
    for _ in range(150):
        t = requests.get(f"{B}/tasks/{tid}", headers=H, timeout=20).json()
        if t.get("status") in ("done", "failed"):
            return t
        time.sleep(1)
    return None


def search(kb_id: int, q: str) -> set[int]:
    r = requests.post(
        f"{B}/search",
        headers={**H, "Content-Type": "application/json"},
        json={"query": q, "kb_ids": [kb_id]},
        timeout=90,
    )
    return {x.get("chunk_id") for x in (r.json().get("results") or [])}


def chunk_state(doc_id: int) -> dict[int, tuple[bool, str | None, str]]:
    """{chunk_id: (is_retrievable, retired_reason, content[:40])}"""
    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.models import DocumentChunk

    db = SessionLocal()
    try:
        rows = (
            db.execute(select(DocumentChunk).where(DocumentChunk.doc_id == doc_id))
            .scalars()
            .all()
        )
        return {c.id: (c.is_retrievable, c.retired_reason, (c.content or "")[:40]) for c in rows}
    finally:
        db.close()


def main() -> int:
    global H
    H = {"Authorization": f"Bearer {_login()}"}
    ts = uuid.uuid4().hex[:6]
    kb = requests.post(
        f"{B}/kb",
        headers=H,
        json={"name": f"覆盖回滚验证_{ts}", "description": "回归验证"},
        timeout=20,
    ).json()
    kb_id = kb["id"]

    print("=" * 72)
    print("overwrite 后删除新文档 → 旧内容恢复验证")
    print("=" * 72)

    common = [
        "第一条 差旅住宿标准：一线城市 600 元/晚，二线城市 400 元/晚。",
        "第二条 餐饮补助标准：每人每天 100 元，超标部分自理。",
        "第四条 报销须在费用发生后 30 天内提交，逾期不予受理。",
        "第五条 报销发票必须为增值税专用发票，否则不予报销。",
    ]
    body_a = (
        "# 报销制度\n\n第三条 单次报销上限 500 元，超出部分需部门总监审批。\n\n"
        + "\n\n".join(common)
    )
    body_b = (
        "# 报销制度（修订）\n\n第三条 单次报销上限 1200 元，超出部分需部门总监审批。\n\n"
        + "\n\n".join(common)
    )

    try:
        ta = upload(kb_id, f"报销制度_原版_{ts}.md", body_a)
        check(bool(ta) and ta.get("status") == "done", "上传原版（旧文档）")
        tb = upload(kb_id, f"报销制度_修订_{ts}.md", body_b)
        check(bool(tb) and tb.get("status") == "done", "上传修订版（新文档）")

        res = requests.get(f"{B}/conflicts", headers=H, params={"kb_id": kb_id}, timeout=30).json()
        items = res.get("items") or []
        check(len(items) >= 1, f"检出冲突 {len(items)} 条")
        if not items:
            return 1

        old_doc = items[0].get("old_doc_id")
        new_doc = items[0].get("new_doc_id")
        cid = items[0].get("id")
        print(f"    冲突对：new={new_doc} vs old={old_doc}")

        before = chunk_state(old_doc)
        blocked_before = {k for k, v in before.items() if not v[0]}
        check(not blocked_before, "覆盖前：旧文档切片全部可检索")

        # ① 处理为「覆盖」
        r = requests.post(
            f"{B}/conflicts/{cid}/resolve",
            headers={**H, "Content-Type": "application/json"},
            json={"resolution": "overwrite"},
            timeout=90,
        )
        check(r.status_code == 200, f"处理冲突=overwrite（HTTP {r.status_code}）")
        time.sleep(2)

        after = chunk_state(old_doc)
        blocked_after = {k for k, v in after.items() if not v[0]}
        print(f"    覆盖后旧文档被隔离切片：{blocked_after}")
        for k in blocked_after:
            print(f"      #{k} reason={after[k][1]} {after[k][2]!r}")
        check(bool(blocked_after), "覆盖后：冲突的那片旧内容被隔离（不再参与检索）")
        check(
            len(blocked_after) < len(after),
            f"覆盖后：旧文档其余切片仍可检索（{len(after) - len(blocked_after)}/{len(after)} 片保留）",
        )

        # ② 查询被覆盖内容的检索结果（应搜不到）
        hit_before = search(kb_id, "单次报销上限")
        check(
            not (blocked_after & hit_before),
            "覆盖后：被取代的旧内容检索不到",
            f"命中={hit_before}",
        )

        # ③ 删除新文档 → 旧内容应自动恢复
        rd = requests.delete(f"{B}/documents/{new_doc}", headers=H, timeout=60)
        check(rd.status_code == 200, f"删除新文档（HTTP {rd.status_code}）")
        time.sleep(2)

        restored = chunk_state(old_doc)
        still_blocked = {k for k, v in restored.items() if not v[0]}
        print(f"    删除新文档后仍被隔离的切片：{still_blocked}")
        check(
            not still_blocked,
            "⭐ 删除新文档后：旧文档全部切片恢复可检索（缺陷修复点）",
            f"仍隔离={still_blocked}",
        )

        hit_after = search(kb_id, "单次报销上限")
        check(
            bool(blocked_after & hit_after),
            "⭐ 删除新文档后：被覆盖的旧内容重新可检索",
            f"恢复片={blocked_after} 命中={hit_after}",
        )
    finally:
        requests.delete(f"{B}/kb/{kb_id}", headers=H, timeout=60)

    print("-" * 72)
    print(f"  结果：{_ok}/{_ok + _fail} 通过")
    print("=" * 72)
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
