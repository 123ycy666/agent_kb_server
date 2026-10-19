"""专项验证：① 孤儿版本自愈 ② 上传不再阻塞全局。

对应修复（2026-10-08）
----------------------
① 上传新版会把旧版下架（is_active=False + 旧切片 is_retrievable=False）。
   若新版被删除，旧版原本永久不可检索（孤儿版本）。
   修复：superseded_by 记录取代关系，删除时自动回滚复活。
② upload 曾是唯一的 async def 端点，内部同步阻塞占死事件循环，
   上传大文件期间所有并发请求（含检索）全部排队。
   修复：改为 def 走线程池 + 后台任务处理，请求内立即返回 202。

运行：python tests/verify_lifecycle.py
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from e2e_check import Api, search  # noqa: E402

KB_ID = 11  # 存在的知识库；测试文档会在结束时清理
RESULTS: list[tuple[bool, str]] = []


def check(cond: bool, label: str, extra: str = "") -> None:
    RESULTS.append((bool(cond), label))
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}{('  → ' + extra) if extra and not cond else ''}")


def text_of(n: int, tag: str) -> str:
    return "\n".join(
        f"第{i}条 {tag}：本条款规定了关于差旅报销与审批时限的具体要求，金额上限为 {300 + i} 元。"
        for i in range(n)
    )


def unique_text(n: int, marker: str) -> str:
    """带唯一标记的正文。

    ⚠️ 内容必须与库中其他文档**明显不同**：早期版本用「差旅报销」这类通用词，
       会与压测文档互相干扰，检索被压测文档挤掉后误判为「复活文档搜不到」。
    """
    return "\n".join(
        f"第{i}条 专项{marker}：{marker}号专项物资的采购报价上限为 {700 + i} 元，"
        f"由{marker}专员组审批，超出部分需上报{marker}委员会复核。"
        for i in range(n)
    )


def test_orphan_rollback(admin: Api) -> None:
    print("\n【1】孤儿版本自愈（删新版 → 旧版自动复活并可检索）")
    name = f"生命周期验证_{int(time.time())}.txt"
    marker = f"M{int(time.time()) % 1000000}"

    v1 = admin.upload(KB_ID, name, unique_text(3, marker + "甲"), is_public=True, tags=[])
    ok1 = v1.status_code == 200 and v1.json().get("doc_id")
    check(ok1, "上传 v1", str(v1.json())[:150])
    if not ok1:
        return
    doc1 = v1.json()["doc_id"]

    v2 = admin.upload(KB_ID, name, unique_text(3, marker + "乙"), is_public=True, tags=[])
    ok2 = v2.status_code == 200 and v2.json().get("doc_id")
    check(ok2, "上传 v2（同文件名 → 版本升级）", str(v2.json())[:150])
    if not ok2:
        return
    doc2 = v2.json()["doc_id"]

    r1 = admin.get(f"/documents/{doc1}").json()
    check(r1.get("is_active") is False, "v1 上传 v2 后被下架", f"is_active={r1.get('is_active')}")

    d = admin.delete(f"/documents/{doc2}")
    warns = (d.json().get("warnings") or []) if d.status_code == 200 else []
    check(d.status_code == 200, "删除 v2 成功", str(d.json())[:150])
    check(any("恢复" in w for w in warns), "响应明确告知已回滚复活旧版", str(warns))

    r2 = admin.get(f"/documents/{doc1}").json()
    check(r2.get("is_active") is True, "v1 自动复活（is_active=True）", f"is_active={r2.get('is_active')}")

    s = search(admin, f"{marker}甲 专项物资 采购报价上限", [KB_ID])
    hit_ids = {x["doc_id"] for x in s.get("results", [])}
    check(doc1 in hit_ids, "复活后的 v1 能被检索命中", f"命中={hit_ids}")

    # 手动 restore 端点：把 v1 顶回活跃
    admin.delete(f"/documents/{doc1}")
    up = admin.upload(KB_ID, name, unique_text(4, marker + "丙"), is_public=True, tags=[])
    if up.status_code == 200 and up.json().get("doc_id"):
        new_doc = up.json()["doc_id"]
        rst = admin.post(f"/documents/{new_doc}/restore")
        check(rst.status_code == 200, "restore 端点可用", str(rst.json())[:120])
        admin.delete(f"/documents/{new_doc}")
    admin.delete(f"/documents/{doc1}")


def test_non_blocking(admin: Api) -> None:
    print("\n【2】上传期间不阻塞其他请求")
    big = text_of(1500, "压测")
    latencies: list[float] = []
    stop = threading.Event()

    def probe() -> None:
        while not stop.is_set():
            t0 = time.perf_counter()
            try:
                admin.get("/system/health")
            except Exception:  # noqa: BLE001
                pass
            latencies.append((time.perf_counter() - t0) * 1000)
            time.sleep(0.2)

    th = threading.Thread(target=probe, daemon=True)
    th.start()
    time.sleep(1)
    # ⚠️ 必须用裸 client.post 测「受理耗时」，不能用 Api.upload()——
    #    后者内部会等任务跑完，量到的是整个任务时长而非接口响应时间。
    import json as _json

    name = f"压测_{int(time.time())}.txt"
    t0 = time.perf_counter()
    r = admin.client.post(
        f"/kb/{KB_ID}/documents",
        headers=admin.headers(),
        files={"file": (name, big.encode("utf-8"), "text/plain")},
        data={"is_public": "false", "tags": _json.dumps([])},
    )
    accept_ms = (time.perf_counter() - t0) * 1000
    stop.set()
    th.join(timeout=3)

    check(r.status_code == 202, "上传立即受理（202）", f"{r.status_code} {r.json()}")
    check(accept_ms < 3000, f"上传响应 <3s（实测 {accept_ms:.0f}ms）")

    inflight = [ms for ms in latencies if ms > 5000]
    worst = max(latencies) if latencies else 0
    check(not inflight, "上传期间无请求超时（>5s）", f"超时 {len(inflight)} 次")
    check(worst < 3000, f"上传期间最大延迟 {worst:.0f}ms < 3s")

    if r.status_code == 202:
        data = r.json()
        final = admin.wait_task(data["task_id"])
        if final.get("doc_id"):
            admin.delete(f"/documents/{final['doc_id']}")
    print(f"     探针 {len(latencies)} 次，最大 {worst:.0f}ms")


def main() -> int:
    print("=" * 72)
    print("  生命周期专项验证：孤儿版本自愈 + 上传非阻塞")
    print("=" * 72)
    admin = Api()
    admin.login("admin", "admin123456")
    try:
        test_orphan_rollback(admin)
        test_non_blocking(admin)
    finally:
        passed = sum(1 for ok, _ in RESULTS if ok)
        print("\n" + "=" * 72)
        print(f"  结果：{passed}/{len(RESULTS)} 通过")
        if passed != len(RESULTS):
            for ok, label in RESULTS:
                if not ok:
                    print(f"    失败：{label}")
        print("=" * 72)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())