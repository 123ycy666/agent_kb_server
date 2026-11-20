# -*- coding: utf-8 -*-
"""AC-01 空库启动：只呈现「现场配置」页，业务页与业务接口不可用。

判据（SRS-01 AC-01 / FR-BOOT-001 / UI-002）：
① 引导配置读不到，或② 读到了但产品库连不上 —— 两种情况都必须：
   - `/api/v1/site/status` 可访问且 `ok=false`；
   - 业务接口返回 503 + `SITE_NOT_CONFIGURED`（不是 500、不是 200）；
   - 前端 `index.html` 仍能拿到（否则连配置页都打不开）。

    python tests/ac/ac01_empty_gate.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util as U  # noqa: E402

BUSINESS = [
    ("GET", "/api/v1/kb"),
    ("GET", "/api/v1/prompts/texts"),
    ("GET", "/api/v1/users"),
    ("GET", "/api/sessions"),
    ("POST", "/api/ask"),
]


def main() -> int:
    c = U.Checker("AC-01", "空库启动只出配置页")
    backup = U.BOOT.read_bytes() if U.BOOT.is_file() else None
    try:
        # ── 情况①：引导配置不存在 ─────────────────────────────
        U.header("情况① 引导配置不存在")
        if U.BOOT.is_file():
            U.BOOT.unlink()
        if not U.restart_backend():
            c.check("后端能起来（无引导配置也不崩）", False)
            return 1
        st = U.site_status()
        c.log("status(无引导) = %s" % st)
        c.check("status 可访问且未配置", st.get("configured") is False, st.get("configured"))
        c.check("status.ok=false 且给出原因", st.get("ok") is False and bool(st.get("error")),
                st.get("error"))
        for method, path in BUSINESS:
            code, body, _ = U.req(method, path, {} if method == "POST" else None)
            code_of = (body or {}).get("detail", {}).get("code") if isinstance(body, dict) else ""
            c.check("%s %s → 503 SITE_NOT_CONFIGURED" % (method, path),
                    code == 503 and code_of == "SITE_NOT_CONFIGURED",
                    "HTTP %s %s" % (code, code_of))
        code, body, raw = U.req("GET", "/")
        html = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        c.check("前端 index.html 仍可达（配置页要能加载）",
                code == 200 and 'id="root"' in html, "HTTP %s" % code)

        # ── 情况②：配置在，但库连不上 ────────────────────────
        U.header("情况② 引导配置在、产品库连不上")
        U.BOOT.parent.mkdir(parents=True, exist_ok=True)
        U.BOOT.write_text(U.json.dumps({
            "pg": {"host": "127.0.0.1", "port": 5999, "dbname": "no_such_db",
                   "user": "kb", "password": "x"},
            "admin": {"username": "admin", "password": "x"},
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        if not U.restart_backend():
            c.check("后端能起来（库连不上也不崩）", False)
            return 1
        st = U.site_status()
        c.log("status(连不上库) = %s" % st)
        c.check("status.configured=true 但 ok=false",
                st.get("configured") is True and st.get("ok") is False,
                "configured=%s ok=%s" % (st.get("configured"), st.get("ok")))
        c.check("给出了连不上的原因", "连不上" in (st.get("error") or ""), st.get("error"))
        for method, path in BUSINESS[:3]:
            code, body, _ = U.req(method, path, {} if method == "POST" else None)
            code_of = (body or {}).get("detail", {}).get("code") if isinstance(body, dict) else ""
            c.check("%s %s → 503（库连不上也走现场门）" % (method, path),
                    code == 503 and code_of == "SITE_NOT_CONFIGURED",
                    "HTTP %s %s" % (code, code_of))

        # UI-002 的真实判据：用浏览器渲染后看**页面上有什么**
        dom = U.dump_dom()
        c.log("浏览器渲染后的 DOM 长度：%d" % len(dom))
        c.check("浏览器渲染出「现场配置」页", "现场配置" in dom and "产品库" in dom,
                "含现场配置=%s 含产品库=%s" % ("现场配置" in dom, "产品库" in dom))
        c.check("登录页没有出现", "统一知识库与智能问数平台" not in dom)
        c.check("业务页没有出现（无侧边菜单）",
                "大屏问数" not in dom and "提示词" not in dom and "概览" not in dom)
    finally:
        if backup is not None:
            U.BOOT.write_bytes(backup)
        ok = U.restart_backend()
        c.log("恢复引导配置并重启：%s" % ok)
    st = U.site_status()
    c.check("恢复后现场可用（ok=true）", st.get("ok") is True, st.get("site_id"))
    c.save()
    return 0 if not c.failed else 1


if __name__ == "__main__":
    sys.exit(main())
