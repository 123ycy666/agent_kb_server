"""P0-A 自检脚本：验证不需要数据库的核心能力。

运行：python tests/selfcheck.py
覆盖：Mock 确定性、语义相关性、精排、冲突判定、安全模块、标签治理
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PASS, FAIL = "  [PASS]", "  [FAIL]"
results: list[tuple[bool, str]] = []


def check(cond: bool, label: str, extra: str = "") -> None:
    results.append((cond, label))
    print(f"{PASS if cond else FAIL} {label}{('  → ' + extra) if extra and not cond else ''}")


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def main() -> int:
    print("\n" + "=" * 70)
    print("  P0-A 自检")
    print("=" * 70)

    # ── 1. Mock Embedding ────────────────────────────────────
    print("\n【1】Mock Embedding")
    from app.providers.mock import MockEmbeddingProvider

    emb = MockEmbeddingProvider(dim=1024)

    v1 = emb.embed(["报销标准是 500 元"])[0]
    v2 = emb.embed(["报销标准是 500 元"])[0]

    check(len(v1) == 1024, f"向量维度为 1024（实际 {len(v1)}）")
    check(v1 == v2, "确定性：相同文本 → 相同向量")

    norm = math.sqrt(sum(x * x for x in v1))
    check(abs(norm - 1.0) < 1e-6, f"向量已归一化（模长 {norm:.6f}）")

    sim_same = cosine(v1, emb.embed(["报销标准是 800 元"])[0])
    sim_diff = cosine(v1, emb.embed(["今天天气很好，适合出去散步"])[0])
    check(
        sim_same > sim_diff + 0.2,
        f"语义相关性：相似文本更接近（相似={sim_same:.3f} vs 无关={sim_diff:.3f}）",
    )

    empty_vec = emb.embed([""])[0]
    check(
        len(empty_vec) == 1024 and any(x != 0 for x in empty_vec),
        "空文本兜底：返回非零确定性向量（不会产生 NaN）",
    )

    # ── 2. Mock Rerank ───────────────────────────────────────
    print("\n【2】Mock Rerank")
    from app.providers.mock import MockRerankProvider

    rr = MockRerankProvider()
    ranked = rr.rerank(
        "报销标准是多少",
        ["今天天气很好", "员工报销标准为每次不超过 500 元", "公司组织团建活动"],
        top_k=3,
    )
    check(ranked[0].index == 1, f"精排把最相关文档排到第一（实际 index={ranked[0].index}）")
    check(ranked[0].score > ranked[-1].score, "分数随相关度递减")

    # ── 3. Mock LLM 冲突判定 ─────────────────────────────────
    print("\n【3】Mock LLM 冲突判定（结构化输出）")
    import json

    from app.providers.base import MARK_END, MARK_NEW, MARK_OLD
    from app.providers.mock import MockLLMProvider

    llm = MockLLMProvider()
    prompt = (
        f"{MARK_NEW}\n单次报销上限 800 元，超出部分不予报销。\n{MARK_END}\n\n"
        f"{MARK_OLD}\n单次报销上限 500 元，超出部分不予报销。\n{MARK_END}"
    )
    raw = llm.chat([type("M", (), {"role": "user", "content": prompt})()], json_mode=True)
    verdict = json.loads(raw)
    check(verdict["relation"] == "contradiction", f"检出数值冲突（实际 {verdict['relation']}）")
    check(verdict["conflict_type"] == "fact", f"冲突类型为事实型（实际 {verdict['conflict_type']}）")
    check("500" in str(verdict.get("old_value", "")), "能指出旧值 500")

    prompt2 = (
        f"{MARK_NEW}\n公司团建活动安排在明年春天。\n{MARK_END}\n\n"
        f"{MARK_OLD}\n单次报销上限 500 元，超出部分不予报销。\n{MARK_END}"
    )
    v2 = json.loads(llm.chat([type("M", (), {"role": "user", "content": prompt2})()], json_mode=True))
    check(v2["relation"] == "unrelated", f"无关文本不误报冲突（实际 {v2['relation']}）")

    # 回归测试：切片正文以「【标题路径】」开头时，判定不能被截断
    # （这是曾经踩过的坑：用【】做 prompt 分隔符会与切片正文撞车，
    #   导致解析结果为空、冲突检测静默失效）
    prompt3 = (
        f"{MARK_NEW}\n【公司制度 > 3.2 报销标准】\n单次报销上限 1200 元，超出需审批。\n"
        f"【公司制度 > 3.3 住宿标准】\n一线城市不超过 600 元。\n{MARK_END}\n\n"
        f"{MARK_OLD}\n【公司制度 > 3.2 报销标准】\n单次报销上限 500 元，超出需审批。\n"
        f"【公司制度 > 3.3 住宿标准】\n一线城市不超过 600 元。\n{MARK_END}"
    )
    v3 = json.loads(llm.chat([type("M", (), {"role": "user", "content": prompt3})()], json_mode=True))
    check(
        v3["relation"] == "contradiction",
        f"切片正文含【】时判定仍正确（实际 {v3['relation']}）",
    )

    # ── 4. 安全模块 ──────────────────────────────────────────
    print("\n【4】安全模块")
    from app.core.security import (
        hash_password,
        mask_secret,
        verify_password,
    )

    h = hash_password("admin123456")
    check(verify_password("admin123456", h), "密码校验通过")
    check(not verify_password("wrong", h), "错误密码被拒绝")
    check(h != hash_password("admin123456"), "同一密码两次哈希不同（加盐生效）")
    long_pw = "x" * 300
    check(verify_password(long_pw, hash_password(long_pw)), "超长密码（>72字节）正确处理")

    # API 令牌（kb_live_*）已在 2026-10-12 移除，仅保留密钥脱敏能力
    check(mask_secret("sk-abcdef123456").count("*") >= 6, f"脱敏有效（{mask_secret('sk-abcdef123456')}）")
    check(mask_secret("") == "", "空密钥脱敏返回空")

    # ── 5. 标签规范化 ────────────────────────────────────────
    print("\n【5】标签规范化（标签即自由字符串）")
    from app.services.tag_governance import (
        MAX_TAGS_PER_DOC,
        TagValidationError,
        normalize_tag,
        normalize_tags,
    )

    check(normalize_tag("  行政  ") == "行政", "去掉首尾空白")
    check(normalize_tag("预算\u3000管理") == "预算 管理", "全角空格规范化 + 内部空白折叠")
    check(normalize_tag("行政") == "行政", "中文标签直接可用（无需英文 key）")

    try:
        normalize_tag("   ")
        check(False, "空标签应被拒绝")
    except TagValidationError:
        check(True, "空标签被拒绝")

    try:
        normalize_tag("x" * 40)
        check(False, "超长标签应被拒绝")
    except TagValidationError:
        check(True, "超长标签被拒绝（上限 32 字符）")

    tags, warns = normalize_tags(["行政", "行政 ", "A", "a", "", None, "预算"])
    check(tags == ["行政", "A", "预算"], f"大小写不敏感去重，结果：{tags}")
    check(any("空标签" in w for w in warns), "空标签被跳过并记警告（不阻断上传）")

    many, warns2 = normalize_tags([f"t{i}" for i in range(20)])
    check(len(many) == MAX_TAGS_PER_DOC, f"超数量截断到 {MAX_TAGS_PER_DOC} 个")
    check(any("上限" in w for w in warns2), "超数量产生警告而非报错")

    # ── 6. 切片器 ────────────────────────────────────────────
    print("\n【6】切片器（标题路径保留）")
    from app.services.ingestion.chunker import chunk_document
    from app.services.ingestion.parsers.text_parser import parse_markdown

    md = """# 公司制度

## 第三章 费用管理

### 3.2 报销标准

单次报销上限 800 元，超出部分需部门总监审批。

餐饮发票必须附消费明细，否则不予报销。
"""
    parsed = parse_markdown(md, filename="制度")
    drafts = chunk_document(parsed, chunk_size=200, chunk_overlap=32)
    joined = "\n".join(d.content for d in drafts)
    check(len(drafts) > 0, f"切出 {len(drafts)} 个切片")
    check("第三章" in joined and "3.2" in joined, "切片保留了标题路径（提升检索命中率的关键）")

    # ── 7. 解析器格式支持 ────────────────────────────────────
    print("\n【7】解析器与文档格式")
    from app.services.ingestion import parsers as P

    for ext in (".pdf", ".docx", ".xlsx", ".md", ".txt"):
        check(P.is_supported(f"a{ext}"), f"支持格式 {ext}")
    check(not P.is_supported("a.doc"), "拒绝旧版 .doc（提示另存为 .docx）")
    check(not P.is_supported("a.exe"), "拒绝不支持格式")

    txt_parsed = P.parse_bytes("报销上限 800 元。\n\n餐饮需附明细。".encode(), "a.txt")
    check(len(txt_parsed.blocks) == 2, f"TXT 按空行分段（{len(txt_parsed.blocks)} 段）")

    md_parsed = P.parse_bytes(md.encode(), "b.md")
    check(
        any(b.heading_path for b in md_parsed.blocks),
        "Markdown 识别出标题层级",
    )

    # ── 8. 可见性过滤（两种视角）────────────────────────────
    print("\n【8】可见性过滤（后端强制注入，manage / search 两种视角）")
    from app.core.deps import CurrentActor
    from app.services.permission import (
        SCOPE_MANAGE,
        SCOPE_SEARCH,
        can_edit_document,
        visible_document_filter,
    )

    class _Doc:
        def __init__(self, owner_id):
            self.owner_id = owner_id

    admin = CurrentActor(type="user", user_id=1, role_codes=["admin"])
    user = CurrentActor(type="user", user_id=2, role_codes=["user"])
    anon = CurrentActor()  # 无任何身份（未登录：无 user_id、无角色）

    # 管理视图：管理员全知
    check(
        str(visible_document_filter(admin, SCOPE_MANAGE)) == "true",
        "管理视图：管理员可见全部文档",
    )
    # 检索视图：管理员同样受限（刻意设计）
    c_admin = str(visible_document_filter(admin, SCOPE_SEARCH))
    check(c_admin != "true", "检索视图：不对管理员放行全部（刻意设计）")
    check("visibility" in c_admin and "owner_id" in c_admin, "检索视图：管理员只见公开 + 自己")

    c_user = str(visible_document_filter(user, SCOPE_SEARCH))
    check("visibility" in c_user and "owner_id" in c_user, "普通用户：公开 + 自己上传")

    c_anon = str(visible_document_filter(anon, SCOPE_SEARCH))
    check(
        "visibility" in c_anon and "owner_id" not in c_anon,
        "无身份请求只能看公开（保守失败，不放行）",
    )

    # 改 / 删 判定
    check(can_edit_document(admin, _Doc(owner_id=999)), "管理员可改任意文档")
    check(can_edit_document(user, _Doc(owner_id=2)), "用户可改自己上传的文档")
    check(not can_edit_document(user, _Doc(owner_id=3)), "用户不能改他人上传的文档")
    check(not can_edit_document(anon, _Doc(owner_id=None)), "无身份不可改任何文档")

    # ── 汇总 ─────────────────────────────────────────────────
    passed = sum(1 for ok, _ in results if ok)
    total = len(results)
    print("\n" + "=" * 70)
    print(f"  结果：{passed}/{total} 通过")
    if passed < total:
        print("  失败项：")
        for ok, label in results:
            if not ok:
                print(f"    - {label}")
    print("=" * 70 + "\n")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
