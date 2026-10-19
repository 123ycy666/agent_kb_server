"""把 data/seed_docs/ 下的示例文档导入一个知识库（演示 / 验收用）。

用法::

    cd server
    python scripts/import_seed_docs.py                 # 默认库名「公司制度」
    python scripts/import_seed_docs.py --kb 我的制度    # 指定库名

行为
----
- 知识库不存在则创建（管理员账号 admin）
- 目录下的 .docx / .md / .txt / .pdf / .xlsx 全部以**公开**方式上传，
  并按文件名自动打标签
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

BASE = "http://127.0.0.1:8000/api/v1"
SEED_DIR = Path(__file__).resolve().parents[2] / "data" / "seed_docs"
SUPPORTED = {".pdf", ".docx", ".xlsx", ".md", ".txt"}

# 文件名关键词 → 标签
TAG_RULES = [
    ("考勤", ["行政", "人事"]),
    ("信息安全", ["信息安全", "合规"]),
    ("报", ["行政", "财务"]),
]


def tags_for(filename: str) -> list[str]:
    for key, tags in TAG_RULES:
        if key in filename:
            return tags
    return ["制度"]


def main() -> int:
    kb_name = "公司制度"
    if "--kb" in sys.argv:
        kb_name = sys.argv[sys.argv.index("--kb") + 1]

    if not SEED_DIR.exists():
        print(f"❌ 目录不存在：{SEED_DIR}")
        return 1

    files = [f for f in sorted(SEED_DIR.iterdir()) if f.suffix.lower() in SUPPORTED]
    if not files:
        print(f"❌ {SEED_DIR} 下没有可导入的文档")
        return 1

    c = httpx.Client(base_url=BASE, timeout=300)
    tok = c.post(
        "/auth/login", json={"username": "admin", "password": "admin123456"}
    ).json()["access_token"]
    H = {"Authorization": f"Bearer {tok}"}

    kbs = c.get("/kb", headers=H).json().get("items", [])
    kb = next((k for k in kbs if k["name"] == kb_name), None)
    if kb is None:
        kb = c.post("/kb", headers=H, json={"name": kb_name, "description": "示例文档"}).json()
        print(f"已创建知识库「{kb_name}」(id={kb['id']})")
    else:
        print(f"复用已有知识库「{kb_name}」(id={kb['id']})")

    import json as _json

    for f in files:
        tags = tags_for(f.name)
        r = c.post(
            f"/kb/{kb['id']}/documents",
            headers=H,
            files={"file": (f.name, f.read_bytes())},
            data={"is_public": "true", "tags": _json.dumps(tags)},
        )
        mark = "✓" if r.status_code in (200, 202) else "✗"
        detail = ""
        if r.status_code not in (200, 202):
            try:
                detail = r.json().get("detail", {}).get("message", "")
            except Exception:  # noqa: BLE001
                detail = r.text[:100]
        print(f"  {mark} {f.name}  标签={tags}  {detail}")

    print(f"\n完成。打开 http://127.0.0.1:5173 查看「{kb_name}」。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
