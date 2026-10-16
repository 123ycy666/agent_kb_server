"""端到端验收脚本：通过真实 HTTP 接口验证核心流程。

前置条件
--------
数据库容器已启动，后端服务运行在 127.0.0.1:8000

运行
----
    python tests/e2e_check.py

覆盖的业务模型（重构后）
------------------------
- 角色只有 admin / user 两种
- 文档可见性只有 public / private 两种
- 标签是自由字符串（自动登记进词表）
- 改 / 删 = 上传者本人 或 管理员
- **管理员检索时同样只搜「公开 + 自己」**
"""

from __future__ import annotations

import sys
import time

import httpx

BASE = "http://127.0.0.1:8000/api/v1"
ADMIN = ("admin", "admin123456")

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str, extra: str = "") -> bool:
    results.append((ok, label))
    flag = "  [PASS]" if ok else "  [FAIL]"
    line = f"{flag} {label}"
    if not ok and extra:
        line += f"\n         → {extra[:300]}"
    print(line)
    return ok


def section(title: str) -> None:
    print(f"\n【{title}】")


class Api:
    def __init__(self) -> None:
        self.client = httpx.Client(base_url=BASE, timeout=180.0)
        self.token: str = ""

    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def login(self, username: str, password: str) -> dict:
        r = self.client.post(
            "/auth/login", json={"username": username, "password": password}
        )
        r.raise_for_status()
        data = r.json()
        self.token = data["access_token"]
        return data

    def get(self, url: str, **kw) -> httpx.Response:
        return self.client.get(url, headers=self.headers(), **kw)

    def post(self, url: str, **kw) -> httpx.Response:
        return self.client.post(url, headers=self.headers(), **kw)

    def patch(self, url: str, **kw) -> httpx.Response:
        return self.client.patch(url, headers=self.headers(), **kw)

    def delete(self, url: str, **kw) -> httpx.Response:
        return self.client.delete(url, headers=self.headers(), **kw)

    def upload(
        self, kb_id: int, filename: str, text: str, *, is_public: bool, tags: list[str]
    ) -> httpx.Response:
        """上传文档并**等待后台任务完成**。

        ⚠️ 上传接口已改为异步受理（202）：202 只代表「已收到」，
        doc_id 与冲突数要等后台任务跑完才写进 task.result。
        这里统一轮询并合成一个「同步语义」响应，
        上层断言代码无需感知这次架构调整。
        """
        import json as _json

        r = self.client.post(
            f"/kb/{kb_id}/documents",
            headers=self.headers(),
            files={"file": (filename, text.encode("utf-8"), "text/markdown")},
            data={"is_public": "true" if is_public else "false", "tags": _json.dumps(tags)},
        )
        if r.status_code != 202:
            return r
        data = r.json()
        merged = self.wait_task(data["task_id"])
        merged.setdefault("message", data.get("message"))
        return _MergedResponse(merged)

    def wait_task(self, task_id: str, timeout: float = 300.0) -> dict:
        """轮询任务直到结束，返回合并后的上传结果。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(0.5)
            t = self.get(f"/tasks/{task_id}").json()
            if t.get("status") in ("done", "failed"):
                result = t.get("result") or {}
                return {
                    "task_id": task_id,
                    "status": t["status"],
                    "error_msg": t.get("error_msg"),
                    "doc_id": result.get("doc_id"),
                    "chunk_count": result.get("chunk_count"),
                    "conflict_count": result.get("conflict_count") or 0,
                    "conflict_docs": result.get("conflict_docs") or [],
                    "warnings": [result["note"]] if result.get("note") else [],
                }
        raise TimeoutError(f"任务 {task_id} 超时未完成")


class _MergedResponse:
    """202 受理响应 + 后台任务最终结果的合并视图。"""

    def __init__(self, payload: dict):
        self._payload = payload

    @property
    def status_code(self) -> int:
        return 200 if self._payload.get("status") == "done" else 500

    @property
    def text(self) -> str:
        import json as _json

        return _json.dumps(self._payload, ensure_ascii=False, default=str)

    def json(self) -> dict:
        return self._payload


def err(r: httpx.Response) -> str:
    try:
        d = r.json().get("detail")
        if isinstance(d, dict):
            return f"HTTP {r.status_code} {d.get('code')}: {d.get('message')}"
        return f"HTTP {r.status_code} {d}"
    except Exception:  # noqa: BLE001
        return f"HTTP {r.status_code} {r.text[:200]}"


def search(api: Api, query: str, kb_ids: list[int], **kw) -> dict:
    """调用检索接口。

    ⚠️ 非 200 必须显式记为失败 —— 早期版本静默返回「0 条」，
       把接口 500 伪装成「没有匹配结果」，掩盖了一个真 bug（标签过滤抛异常）。
    """
    r = api.post("/search", json={"query": query, "kb_ids": kb_ids, "top_k": 5, **kw})
    if r.status_code != 200:
        check(False, f"检索接口异常（query={query!r}）", err(r))
        return {"results": [], "total": 0, "_raw": err(r)}
    return r.json()


def main() -> int:
    print("=" * 72)
    print("  端到端验收（简化权限模型：admin/user + public/private + 自由标签）")
    print("=" * 72)

    ts = int(time.time())
    admin = Api()

    # ── 0. 健康检查 ─────────────────────────────────────────
    section("0. 服务健康")
    r = admin.client.get("/system/health")
    h = r.json()
    check(r.status_code == 200 and h["database"]["ok"], "数据库连接正常")
    mode = h["provider"]["mode"]
    print(f"  [INFO] 当前模型档位：{mode}")

    # ── 1. 认证 ─────────────────────────────────────────────
    section("1. 认证")
    info = admin.login(*ADMIN)
    check(bool(admin.token), "管理员登录成功")
    check("kb:create" in info["permissions"], "返回权限点列表")
    check("admin" in info["roles"], "返回角色列表")

    me = admin.get("/auth/me")
    check(me.status_code == 200 and me.json()["username"] == "admin", "获取当前身份")

    r = admin.client.post("/auth/login", json={"username": "admin", "password": "wrong"})
    check(r.status_code == 401, "错误密码被拒绝（401）", err(r))

    r = admin.client.get("/kb")
    check(r.status_code == 401, "无凭证访问被拒绝（401）", err(r))

    # ── 2. 角色只有两种 ─────────────────────────────────────
    section("2. 角色模型（固定两种）")
    r = admin.get("/roles")
    role_names = sorted(x["name"] for x in r.json()) if r.status_code == 200 else []
    check(role_names == ["admin", "user"], f"角色仅 admin / user（实际 {role_names}）", err(r))

    # ── 3. 用户 ─────────────────────────────────────────────
    section("3. 用户管理")
    roles_map: dict[str, int] = {}
    rr = admin.get("/roles")
    if rr.status_code == 200 and isinstance(rr.json(), list):
        roles_map = {x["name"]: x["id"] for x in rr.json()}
    check(bool(roles_map.get("user")), f"取到 user 角色 id（{roles_map}）", err(rr))

    users = {}
    for i in (1, 2):
        uname = f"e2e_u{i}_{ts % 100000}"
        r = admin.post(
            "/users",
            json={
                "username": uname,
                "password": "test123456",
                "display_name": f"E2E用户{i}",
                "role_ids": [roles_map["user"]] if roles_map.get("user") else [],
            },
        )
        if r.status_code == 200:
            users[i] = uname
        else:
            check(False, f"创建用户 {uname}", err(r))

    u1 = Api()
    u2 = Api()
    if len(users) == 2:
        d1 = u1.login(users[1], "test123456")
        d2 = u2.login(users[2], "test123456")
        check("user" in d1["roles"], f"用户1 登录且角色为 user（{d1['roles']}）")
        check("user" in d2["roles"], f"用户2 登录且角色为 user（{d2['roles']}）")
        check("kb:create" not in d1["permissions"], "普通用户没有建库权限（权限点已收敛）")
    else:
        check(False, "用户创建失败，后续权限用例无法执行")

    # ── 4. 知识库权限 ───────────────────────────────────────
    section("4. 知识库（仅管理员可建）")
    r = admin.post("/kb", json={"name": f"E2E知识库_{ts % 100000}", "description": "端到端验收"})
    check(r.status_code == 200, "管理员创建知识库", err(r))
    kb_id = r.json()["id"] if r.status_code == 200 else 0

    r = u1.post("/kb", json={"name": f"用户建库_{ts % 100000}"})
    check(r.status_code == 403, "普通用户建库被拒绝（403）", err(r))

    r = u1.get("/kb")
    check(r.status_code == 200, "普通用户可以查看知识库列表", err(r))

    # ── 5. 上传与标签 ───────────────────────────────────────
    section("5. 上传（公开/私有 + 自由标签）")

    doc_a = f"E2E行政制度_{ts % 100000}.md"
    # 留存原始字节，供「下载原文件」逐字节比对
    DOC_A_BYTES = (
        "# 行政管理制度\n\n## 差旅报销\n\n单次报销上限 800 元，超出部分需部门总监审批。\n"
    ).encode("utf-8")
    r = admin.upload(
        kb_id,
        doc_a,
        DOC_A_BYTES.decode("utf-8"),
        is_public=True,
        tags=["行政", "预算"],
    )
    check(r.status_code == 200 or r.status_code == 202, "上传公开文档", err(r))

    r = admin.get(f"/kb/{kb_id}/documents")
    items = r.json().get("items", []) if r.status_code == 200 else []
    a = next((x for x in items if x["filename"] == doc_a), None)
    check(a is not None, "公开文档出现在列表中")
    if a:
        check(a["visibility"] == "public", f"可见性为 public（实际 {a.get('visibility')}）")
        check(
            set(a["tags"]) == {"行政", "预算"},
            f"标签正确（实际 {a.get('tags')}）",
        )
    doc_a_id = a["id"] if a else 0

    # 标签自动登记进词表（无需先建标签）
    r = admin.get("/tags")
    vocab = [x["name"] for x in r.json().get("items", [])] if r.status_code == 200 else []
    check("行政" in vocab and "预算" in vocab, f"标签自动登记进词表（{vocab[:6]}…）", err(r))

    r = admin.get("/tags/options")
    opts = [x["name"] for x in r.json().get("options", [])] if r.status_code == 200 else []
    check("行政" in opts, "标签筛选选项可用", err(r))

    # ── 6. 检索与标签过滤 ───────────────────────────────────
    section("6. 检索（双路融合 + 标签过滤）")
    sr = search(admin, "报销上限", [kb_id])
    check(sr.get("total", 0) > 0, f"基础检索命中（{sr.get('total')} 条）", sr.get("_raw", ""))
    if sr.get("results"):
        sources = {x["source"] for x in sr["results"]}
        check(
            any(s in ("hybrid", "vector", "keyword") for s in sources),
            f"返回结果带来源标记（{sources}）",
        )
        check(
            any("800" in x["content"] for x in sr["results"]),
            "命中内容包含关键数值 800",
        )

    sr_hit = search(admin, "报销上限", [kb_id], tag_filters=["行政"])
    check(sr_hit.get("total", 0) > 0, "标签过滤：命中（文档带「行政」标签）")

    sr_miss = search(admin, "报销上限", [kb_id], tag_filters=["财务"])
    check(sr_miss.get("total", 0) == 0, "标签过滤：不命中（文档无该标签）")
    check(sr_miss.get("filter_notice") is not None, "无结果时返回兜底提示（避免误报系统故障）")

    # ── 6b. 版本压制 ────────────────────────────────────────
    r = admin.upload(
        kb_id,
        doc_a,
        "# 行政管理制度\n\n## 差旅报销\n\n单次报销上限 1200 元，超出部分需部门总监审批。\n",
        is_public=True,
        tags=["行政"],
    )
    check(r.status_code in (200, 202), "上传同文件名生成新版本", err(r))

    sr = search(admin, "报销上限", [kb_id])
    if sr.get("results"):
        check(
            all(x["version_no"] == 2 for x in sr["results"]),
            "版本压制：只返回最新版本 v2",
        )
        check(any("1200" in x["content"] for x in sr["results"]), "命中新版本内容（1200）")
    else:
        check(False, "版本压制：检索无结果", sr.get("_raw", ""))

    # ── 7. 权限隔离（核心）──────────────────────────────────
    section("7. 权限隔离（核心）")

    secret = f"E2E私有文档_{ts % 100000}.md"
    r = u1.upload(
        kb_id,
        secret,
        "# 人事制度\n\n## 年假\n\n年假天数上限为 15 天，工龄满十年可增至 20 天。\n",
        is_public=False,
        tags=["人事"],
    )
    check(r.status_code in (200, 202), "普通用户上传私有文档", err(r))

    r = admin.get(f"/kb/{kb_id}/documents")
    items = r.json().get("items", []) if r.status_code == 200 else []
    sec_doc = next((x for x in items if x["filename"] == secret), None)
    check(sec_doc is not None, "管理员在**文档列表**（管理视图）能看到他人的私有文档")
    secret_id = sec_doc["id"] if sec_doc else 0
    if sec_doc:
        check(sec_doc["visibility"] == "private", "该文档可见性为 private")

    # ⚠️ 断言必须看「命中的 doc_id 里有没有它」，而不是 total 是否为 0 ——
    #    向量召回会返回 top-k 条**即使不相关**的切片，total 并不代表相关性。
    sr_admin = search(admin, "年假天数上限", [kb_id])
    admin_ids = {x["doc_id"] for x in sr_admin.get("results", [])}
    check(
        secret_id not in admin_ids,
        "管理员**检索不到**他人的私有文档（刻意设计：全知权限只用于管理视图）",
        f"命中文档 {admin_ids}，私有文档 {secret_id}",
    )

    sr_u1 = search(u1, "年假天数上限", [kb_id])
    check(
        any(x["doc_id"] == secret_id for x in sr_u1.get("results", [])),
        "上传者本人能检索到自己的私有文档",
    )

    sr_u2 = search(u2, "年假天数上限", [kb_id])
    check(
        all(x["doc_id"] != secret_id for x in sr_u2.get("results", [])),
        "其他普通用户检索不到该私有文档",
    )

    if secret_id:
        r = u2.get(f"/documents/{secret_id}")
        check(r.status_code == 404, "其他用户读取私有文档详情被拒（404 不泄露存在性）", err(r))

    # 公开文档：所有人都能检索到
    sr_u2_pub = search(u2, "报销上限", [kb_id])
    check(sr_u2_pub.get("total", 0) > 0, "普通用户能检索到公开文档")

    # ── 8. 改 / 删 权限 ─────────────────────────────────────
    section("8. 修改与删除（上传者本人 或 管理员）")

    if secret_id:
        # ① 他人的**私有**文档：按「不泄露存在性」原则统一 404
        r = u2.delete(f"/documents/{secret_id}")
        check(r.status_code == 404, "他人操作私有文档 → 404（不泄露存在性）", err(r))

        r = u2.patch(f"/documents/{secret_id}/visibility", json={"visibility": "public"})
        check(r.status_code == 404, "他人修改私有文档可见性 → 404", err(r))

        # ② 他人的**公开**文档：可见但非本人 → 403（明确告知无权限）
        if doc_a_id:
            r = u2.delete(f"/documents/{doc_a_id}")
            check(r.status_code == 403, "他人删除公开文档被拒（403：可见但非本人）", err(r))

            r = u2.patch(
                f"/documents/{doc_a_id}/visibility", json={"visibility": "private"}
            )
            check(r.status_code == 403, "他人修改公开文档可见性被拒（403）", err(r))

            # ③ 下载原文件：**可见 ≠ 可下载**。拿走原件是更高风险的动作。
            r = u2.get(f"/documents/{doc_a_id}/download")
            check(r.status_code == 403, "他人不能下载公开文档的原文件（403）", err(r))

            r = admin.get(f"/documents/{doc_a_id}/download")
            check(r.status_code == 200, "管理员可下载原文件", err(r))
            check(
                (r.headers.get("content-disposition") or "").startswith("attachment"),
                "下载响应带 attachment 头（浏览器直接存盘而非内联打开）",
                r.headers.get("content-disposition", ""),
            )
            check(
                r.content == DOC_A_BYTES,
                "下载内容与上传的原始字节完全一致（未截断 / 未转码）",
            )

        r = u1.patch(f"/documents/{secret_id}/visibility", json={"visibility": "public"})
        check(r.status_code == 200, "上传者本人可切换自己文档的可见性", err(r))
        if r.status_code == 200:
            check(r.json().get("visibility") == "public", "可见性已变为 public")

        sr_after = search(u2, "年假天数上限", [kb_id])
        check(
            any(x["doc_id"] == secret_id for x in sr_after.get("results", [])),
            "切换为公开后，其他用户可检索到该文档",
        )

        r = u1.patch(f"/documents/{secret_id}/visibility", json={"visibility": "private"})
        check(r.status_code == 200, "可再切回 private")

        # 管理员可删任意文档
        r = admin.delete(f"/documents/{secret_id}")
        check(r.status_code == 200, "管理员可删除他人文档", err(r))

    # ── 9. 标签治理（重命名 / 删除）─────────────────────────
    section("9. 标签治理")
    r = admin.post("/tags/rename", json={"old_name": "预算", "new_name": "预算管理"})
    check(r.status_code == 200, "重命名标签", err(r))
    if r.status_code == 200:
        r2 = admin.get(f"/documents/{doc_a_id}")
        tags_now = r2.json().get("tags", []) if r2.status_code == 200 else []
        check("预算管理" in tags_now, f"文档上的标签已同步改写（{tags_now}）")

    r = admin.delete("/tags/预算管理")
    check(r.status_code == 200, "删除标签", err(r))
    if r.status_code == 200 and doc_a_id:
        r2 = admin.get(f"/documents/{doc_a_id}")
        tags_now = r2.json().get("tags", []) if r2.status_code == 200 else []
        check("预算管理" not in tags_now, f"标签已从文档上摘除（{tags_now}）")

    # ── 10. 冲突检测 ────────────────────────────────────────
    section("10. 冲突检测")
    conflict_doc = f"E2E报销制度_{ts % 100000}.md"
    r = admin.upload(
        kb_id,
        conflict_doc,
        "# 报销制度\n\n## 差旅报销\n\n单次报销上限 500 元，超出部分不予报销。\n",
        is_public=True,
        tags=["行政"],
    )
    check(r.status_code in (200, 202), "上传可能冲突的文档", err(r))

    # ⚠️ 回归守卫：上传必须**自动**跑冲突检测。
    #    该功能曾长期只能靠人工点按钮触发，导致上传矛盾文档后毫无提示。
    up = r.json() if r.status_code in (200, 202) else {}
    check(
        up.get("conflict_count", 0) >= 1,
        "上传时**自动**检出冲突（无需人工点检测）",
        f"响应={up}",
    )

    r = admin.get(f"/kb/{kb_id}/documents")
    items = r.json().get("items", []) if r.status_code == 200 else []
    cdoc = next((x for x in items if x["filename"] == conflict_doc), None)
    if cdoc:
        d = admin.post("/conflicts/detect", json={"doc_id": cdoc["id"]})
        if d.status_code == 200:
            body = d.json()
            check(
                body.get("candidate_pairs", 0) > 0,
                f"召回到候选对比切片（{body.get('candidate_pairs')} 对）",
            )
            check(
                body.get("conflict_count", 0) >= 1,
                f"检出数值冲突（{body.get('conflict_count')} 条）：500 vs 1200",
                str(body.get("warnings", ""))[:200],
            )
        else:
            check(False, "冲突检测接口调用失败", err(d))

        # 冲突列表与统计
        r = admin.get("/conflicts", params={"kb_id": kb_id})
        check(r.status_code == 200, "查询冲突列表", err(r))
        conflicts = r.json().get("items", []) if r.status_code == 200 else []
        r = admin.get("/conflicts/stats", params={"kb_id": kb_id})
        check(r.status_code == 200, "查询冲突统计", err(r))

        # ⚠️ 越权防护：他人不能处理我的文档的冲突
        #    （resolution=keep_old 会删除新文档，没有归属校验就是越权删除）
        if conflicts:
            cid = conflicts[0]["id"]
            r = u2.post(
                f"/conflicts/{cid}/resolve",
                json={"resolution": "keep_old", "note": "E2E 越权尝试"},
            )
            check(
                r.status_code == 403,
                "普通用户不能处理他人文档的冲突（403，防越权删除）",
                err(r),
            )
    else:
        check(False, "冲突测试文档未入库")

    # ── 11. 审计 ────────────────────────────────────────────
    section("11. 审计留痕")
    r = admin.get("/audit-logs", params={"page_size": 50})
    logs = r.json().get("items", []) if r.status_code == 200 else []
    actions = {x["action"] for x in logs}
    check("auth.login" in actions, "记录了登录行为")
    check("kb.create" in actions, "记录了知识库创建")
    check("doc.upload" in actions, "记录了文档上传")
    check("search" in actions, "记录了检索行为")
    check("doc.update" in actions, "记录了可见性修改")

    search_logs = [x for x in logs if x["action"] == "search"]
    if search_logs:
        detail = search_logs[0].get("detail") or {}
        check("hit_doc_ids" in detail, "检索审计含命中文档 ID（可追溯数据流向）")

    # ── 12. 清理 ────────────────────────────────────────────
    section("12. 清理测试知识库")
    r = admin.delete(f"/kb/{kb_id}")
    check(r.status_code == 200, "删除测试知识库（级联清理文档）", err(r))

    # ── 汇总 ────────────────────────────────────────────────
    passed = sum(1 for ok, _ in results if ok)
    total = len(results)
    print("\n" + "=" * 72)
    print(f"  结果：{passed}/{total} 通过")
    if passed < total:
        print("  失败项：")
        for ok, label in results:
            if not ok:
                print(f"    - {label}")
    print("=" * 72 + "\n")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())


