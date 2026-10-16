# -*- coding: utf-8 -*-
"""最终全链路验收：一个账号走完所有功能（2026-10-15）

覆盖四阶段：
  A 统一用户（t_employee → ad_new 投影 + RBAC 常量矩阵）
  B 知识库（建库/上传/入库任务/检索/上传人关联/标签）
  C 问数智能体（问答/对话记录挂用户/历史/统计/缺口/反馈）
  D 页面与文档（SPA/问答页/语音页/技能台/健康/文档）

用法：python tests/acceptance.py
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE = "http://127.0.0.1:8200"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SEED_DOC = PROJECT_ROOT / "data" / "seed_docs" / "信息安全管理制度.md"

PASS: list[str] = []
FAIL: list[str] = []


def req(method, path, body=None, token=None, timeout=180, raw=None, ctype=None):
    """返回 (status, body_bytes)。status=0 表示网络层异常。"""
    headers = {"Authorization": "Bearer " + token} if token else {}
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    if ctype:
        headers["Content-Type"] = ctype
    elif data is not None:
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:300]
    except Exception as e:  # noqa: BLE001
        return 0, str(e).encode()[:200]


def jload(b):
    try:
        return json.loads(b)
    except Exception:  # noqa: BLE001
        return {}


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(("  [OK] " if ok else "  [FAIL] ") + name + ((" | " + str(detail)) if detail else ""))


def main() -> int:
    # ── 阶段 A：统一用户 ────────────────────────────────────────
    print("=== A. 统一用户（t_employee -> ad_new 投影 + RBAC）===")
    s, b = req("POST", "/api/v1/auth/login", {"username": "kbadmin", "password": "Kb@123456"})
    d = jload(b) if s == 200 else {}
    tok = d.get("access_token", "")
    check("A1 员工账号登录", s == 200 and bool(tok),
          "roles=%s 权限数=%d" % (d.get("roles"), len(d.get("permissions", []))))

    s, _ = req("POST", "/api/v1/auth/login", {"username": "kbadmin", "password": "wrong"})
    check("A2 错误密码被拒", s == 401, "HTTP %s" % s)

    s, b = req("GET", "/api/v1/auth/me", None, tok)
    me = jload(b) if s == 200 else {}
    check("A3 /me 返回权限集", s == 200 and len(me.get("permissions", [])) == 19,
          "%d 个权限点" % len(me.get("permissions", [])))

    s, b = req("GET", "/api/v1/users", None, tok)
    users = jload(b) if s == 200 else []
    kb = [u for u in users if u.get("username") == "kbadmin"] if isinstance(users, list) else []
    check("A4 员工列表", s == 200 and len(users) > 100, "%d 名员工" % len(users))
    check("A5 投影已建 + 角色对齐",
          bool(kb) and kb[0].get("projected") and kb[0].get("role_names") == ["admin"],
          "role=%s" % (kb[0].get("role_names") if kb else "?"))

    # ── 阶段 B：知识库 ──────────────────────────────────────────
    print("=== B. 知识库（上传/入库/检索/关联）===")
    s, b = req("GET", "/api/v1/kb", None, tok)
    kbs = jload(b).get("items", []) if s == 200 else []
    check("B1 知识库列表", s == 200 and len(kbs) >= 2,
          "%d 个: %s" % (len(kbs), [k.get("name") for k in kbs]))
    if not kbs:
        return 1
    kb_id = kbs[0]["id"]

    # 每次跑都追加一段唯一标记，避免被系统的 SHA256 内容去重拦成 failed
    content = SEED_DOC.read_bytes() + (
        f"\n\n<!-- acceptance run {uuid.uuid4().hex} -->\n".encode()
    )
    boundary = "----WB" + uuid.uuid4().hex
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="is_public"\r\n\r\ntrue\r\n'.encode(),
        f'--{boundary}\r\nContent-Disposition: form-data; name="tags"\r\n\r\n["安全制度"]\r\n'.encode(),
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
        f'filename="acceptance-{uuid.uuid4().hex[:6]}.md"\r\n'
        f'Content-Type: text/markdown\r\n\r\n'.encode() + content + b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    s, b = req("POST", f"/api/v1/kb/{kb_id}/documents", token=tok, raw=b"".join(parts),
               ctype=f"multipart/form-data; boundary={boundary}", timeout=60)
    task_id = jload(b).get("task_id") if s in (200, 201, 202) else None
    check("B2 文档上传受理(202)", s in (200, 201, 202) and bool(task_id), "task_id=%s" % task_id)

    status = "timeout"
    stage = ""
    t: dict = {}
    if task_id:
        # online 档每篇文档要调多次向量化接口，放宽到 300s
        for _ in range(100):
            time.sleep(3)
            s, b = req("GET", f"/api/v1/tasks/{task_id}", None, tok)
            t = jload(b) if s == 200 else {}
            if t.get("status") in ("done", "failed"):
                status = t["status"]
                stage = str(t.get("error") or t.get("stage") or "")[:80]
                break
    check("B3 入库任务完成", status == "done", "status=%s %s" % (status, stage))
    # ⚠️ 两段式回归保护（2026-10-15）：任务 done 时必须带上冲突检测状态，
    #    否则调用方会把「还没判完」误当成「没有冲突」。
    b3_result = t.get("result") if isinstance(t.get("result"), dict) else {}
    check("B3b 任务结果含冲突检测状态", "conflict_status" in b3_result,
          "conflict_status=%s" % b3_result.get("conflict_status"))

    s, b = req("POST", "/api/v1/search",
               {"query": "信息安全 保密管理", "kb_ids": [kb_id], "top_k": 3}, tok, timeout=90)
    r = jload(b) if s == 200 else {}
    check("B4 检索命中", s == 200 and r.get("total", 0) > 0, "命中 %s 条" % r.get("total"))

    s, b = req("GET", f"/api/v1/kb/{kb_id}/documents", None, tok)
    docs = jload(b) if s == 200 else {}
    items = docs.get("items", []) if isinstance(docs, dict) else (docs if isinstance(docs, list) else [])
    check("B5 文档列表", s == 200 and len(items) > 0, "%d 份文档" % len(items))
    # DocumentOut 里的关联字段是 owner_id（指向 app_user.id），不是 owner_name
    kb_uid = kb[0].get("id") if kb else None
    owner_ids = {i.get("owner_id") for i in items if i.get("owner_id")}
    check("B6 文档上传人关联到登录用户", bool(owner_ids) and owner_ids == {kb_uid},
          "owner_id=%s（应等于 kbadmin 的投影 id=%s）" % (sorted(owner_ids), kb_uid))

    s, b = req("GET", "/api/v1/tags", None, tok)
    tags = jload(b) if s == 200 else []
    check("B7 标签", s == 200, "%d 个" % len(tags))

    # ── 阶段 C：问数智能体 ──────────────────────────────────────
    print("=== C. 问数智能体（问答 + 对话记录挂用户）===")
    s, b = req("GET", "/health")
    h = jload(b) if s == 200 else {}
    ag = h.get("agent", {})
    check("C1 健康检查", s == 200,
          "name_fix=%s kb=%s channel=%s" % (ag.get("name_fix"), ag.get("kb"), ag.get("channel")))

    s, b = req("POST", "/api/ask", {"question": "环潭供电所上月线损情况"}, tok, timeout=240)
    qa = jload(b) if s == 200 else {}
    check("C2 问数回答", s == 200 and bool(qa.get("answer")),
          "qa_id=%s | %s" % (qa.get("qa_id"), str(qa.get("answer"))[:45]))
    sid = qa.get("session_id")

    s, b = req("GET", "/api/sessions", None, tok)
    sess = jload(b) if s == 200 else {}
    check("C3 我的会话(按 user_id 过滤)", s == 200 and sess.get("total", 0) > 0,
          "total=%s" % sess.get("total"))
    mine = [x for x in sess.get("sessions", []) if x.get("session_id") == sid]
    check("C4 本次会话归属当前用户", bool(mine), "session=%s" % str(sid)[:16])

    s, b = req("GET", f"/api/history?session_id={sid}", None, tok)
    hist = jload(b) if s == 200 else {}
    check("C5 会话历史", s == 200 and len(hist.get("messages", [])) > 0,
          "%d 条消息" % len(hist.get("messages", [])))

    s, b = req("GET", "/api/stats", None, tok)
    st = jload(b) if s == 200 else {}
    check("C6 统计", s == 200,
          "会话%s 消息%s 步骤%s 缺口%s" % (st.get("sessions"), st.get("messages"),
                                        st.get("steps"), st.get("gaps")))

    s, b = req("GET", "/api/gaps", None, tok)
    g = jload(b) if s == 200 else {}
    check("C7 缺口统计", s == 200, "%d 条" % len(g.get("rows", [])))

    # /api/feedback 的契约是 1（赞）/ 0（踩），不是字符串
    s, _ = req("POST", "/api/feedback", {"qa_id": qa.get("qa_id"), "feedback": 1}, tok)
    check("C8 反馈写入", s == 200, "HTTP %s" % s)

    # ── 阶段 D：页面 ────────────────────────────────────────────
    print("=== D. 页面与文档 ===")
    pages = [
        ("/", "SPA 首页"),
        ("/login", "登录页"),
        ("/agent/", "智能问答页"),
        ("/agent/asr", "语音问答页"),
        ("/agent/skills", "技能台"),
        ("/health", "健康接口"),
        ("/docs", "API 文档"),
        ("/api/v1/system/health", "系统健康"),
    ]
    for path, name in pages:
        s, _ = req("GET", path, timeout=20)
        check("D %s" % name, s == 200, "HTTP %s" % s)

    # ⚠️ 静态资源必须返回正确的 MIME（2026-10-15 白屏事故的根因：
    #    catch-all 把 /assets/*.js 也返回成了 index.html / text/html，
    #    而状态码仍是 200 ⇒ 只查状态码的验收会漏掉，必须查内容形态）
    print("--- D5. 静态资源 MIME（防白屏回归）---")
    s, body = req("GET", "/", timeout=20)
    html = body.decode("utf-8", "replace")
    check("D5a SPA 返回 index.html", s == 200 and 'id="root"' in html,
          "含 #root=%s" % ('id="root"' in html))

    m_js = re.search(r'src="(/assets/[^"]+\.js)"', html)
    m_css = re.search(r'href="(/assets/[^"]+\.css)"', html)
    if m_js:
        s, b = req("GET", m_js.group(1), timeout=60)
        # JS 内容不会以 <!doctype 开头；HTML 会
        is_js = not b.lstrip()[:20].lower().startswith(b"<!doctype")
        check("D5b JS 返回 JS 而非 HTML", s == 200 and is_js and len(b) > 10000,
              "%s → HTTP %s, %d bytes, 是JS=%s" % (m_js.group(1), s, len(b), is_js))
    else:
        check("D5b JS 返回 JS 而非 HTML", False, "index.html 里没找到 /assets/*.js")
    if m_css:
        s, b = req("GET", m_css.group(1), timeout=30)
        is_css = not b.lstrip()[:20].lower().startswith(b"<!doctype")
        check("D5c CSS 返回 CSS", s == 200 and is_css,
              "%s → HTTP %s, %d bytes" % (m_css.group(1), s, len(b)))
    else:
        check("D5c CSS 返回 CSS", False, "index.html 里没找到 /assets/*.css")

    # D5d：问答页已对齐 SPA 的 Ant Design 主色（2026-10-15 视觉融合）
    s, b = req("GET", "/agent/", timeout=20)
    agent_html = b.decode("utf-8", "replace")
    check("D5d 问答页已对齐 Ant Design 主色", s == 200 and "#1677ff" in agent_html,
          "#1677ff 出现 %d 次（旧主色 #1a6fd4 出现 %d 次）"
          % (agent_html.count("#1677ff"), agent_html.count("#1a6fd4")))

    # D5e：问答页要有「检索知识库」多选（2026-10-15 新增的库选择功能）
    check("D5e 问答页含知识库多选控件",
          'id="kbpick"' in agent_html and "检索知识库" in agent_html,
          "kbpick=%s 文案=%s" % ('id="kbpick"' in agent_html, "检索知识库" in agent_html))

    # ── 汇总 ────────────────────────────────────────────────────
    print("\n" + "=" * 56)
    print("验收结果：%d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：", FAIL)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())