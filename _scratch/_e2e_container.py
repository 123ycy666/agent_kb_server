# -*- coding: utf-8 -*-
import json, sys, urllib.request
sys.path.insert(0, "/app")
from app.core.security import create_access_token

TOK = create_access_token(1)


def ask(q, extra=None):
    body = {"question": q, "user_name": "Fred", "channel": "e2e", "history_turns": 0}
    body.update(extra or {})
    req = urllib.request.Request(
        "http://127.0.0.1:8200/api/ask",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + TOK})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read().decode("utf-8"))


def show(tag, d):
    trace = [(t.get("kind"), t.get("tool")) for t in (d.get("trace") or [])]
    print("\n===== %s =====" % tag)
    print("问题：", d.get("input_question"))
    print("补全后：", d.get("question"))
    print("步数：%s ｜ 查库：%s ｜ 知识库调用：%s ｜ 耗时：%sms"
          % (d.get("steps"), d.get("db_query_count"), d.get("kb_query_count"), d.get("elapsed_ms")))
    print("personal：", json.dumps(d.get("personal"), ensure_ascii=False))
    print("trace：", trace)
    print("回答：", (d.get("answer") or "")[:300])


# ① 正常问题（寒暄/概念，不该查库）
show("① 正常问题", ask("你好，你能做什么"))

# ② 业务问题（问数，该查表 + SQL）
show("② 业务问题", ask("两水供电所 2026 年 1-9 月缺陷工单有多少张"))

# ③ 知识库配置：先往个人库传一份规则，再问，最后删掉
from app.core.database import SessionLocal
from app.core.deps import CurrentActor
from app.models import KnowledgeBase
from app.secretary import personal_kb, pgdb
from app.services.ingestion import pipeline

kid = personal_kb.ensure(1)
db = SessionLocal()
actor = CurrentActor(type="user", user_id=1, username="admin", display_name="管理员",
                     role_codes=["admin"])
body = ("# 我的口径\n\n## 线损口径\n"
        "本所对外汇报时线损率一律使用「台区线损率」，不要用分压线损（暗号 P9）。\n")
try:
    res = pipeline.ingest_document(db, kb=db.get(KnowledgeBase, kid),
                                   filename="我的口径规则.md",
                                   content=body.encode("utf-8"), actor=actor)
    print("\n[配置] 已上传规则文档 doc_id=%s 切片=%s" % (res.doc_id, res.chunk_count))
    pr = personal_kb.personal_rules(1)
    print("[配置] personal_rules =", json.dumps(
        {"files": pr.get("files"), "chars": pr.get("chars")}, ensure_ascii=False))
    show("③ 知识库配置后提问", ask("我们所线损率按哪个口径汇报？"))

    # 清理：软删这份文档（保留版本链，不物理删）
    cur = pgdb.connect(autocommit=True).cursor()
    cur.execute("UPDATE document SET is_active=false WHERE id=%s", (res.doc_id,))
    cur.execute("UPDATE document_chunk SET is_retrievable=false WHERE doc_id=%s", (res.doc_id,))
    cur.close()
    print("\n[清理] 已下架测试规则文档 doc_id=%s" % res.doc_id)
finally:
    db.close()
