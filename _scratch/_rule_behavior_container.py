# -*- coding: utf-8 -*-
import json, sys, urllib.request
sys.path.insert(0, "/app")
from app.core.security import create_access_token

TOK = create_access_token(1)
Q = "两水供电所 2026 年分配预算多少"


def ask(q=Q):
    body = {"question": q, "user_name": "Fred", "channel": "probe", "history_turns": 0}
    req = urllib.request.Request(
        "http://127.0.0.1:8200/api/ask",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + TOK})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read().decode("utf-8"))


def show(tag, d):
    print("\n===== %s =====" % tag)
    print("personal：", json.dumps(d.get("personal"), ensure_ascii=False))
    print("trace：", [(t.get("kind"), t.get("tool")) for t in (d.get("trace") or [])])
    print("回答：", (d.get("answer") or "")[:400])


from app.core.database import SessionLocal
from app.core.deps import CurrentActor
from app.models import KnowledgeBase
from app.secretary import personal_kb, pgdb
from app.services.ingestion import pipeline

# ① 没有个人规则的对照组
show("① 无个人规则（对照）", ask())

# ② 加个人规则：金额用元
kid = personal_kb.ensure(1)
db = SessionLocal()
actor = CurrentActor(type="user", user_id=1, username="admin", display_name="管理员",
                     role_codes=["admin"])
body = ("# 我的汇报习惯\n\n## 金额单位\n"
        "所有回答里的金额一律用「元」表示，不要换算成万元（暗号 M9）。\n")
res = pipeline.ingest_document(db, kb=db.get(KnowledgeBase, kid),
                               filename="我的汇报习惯规则.md",
                               content=body.encode("utf-8"), actor=actor)
db.close()
print("\n[配置] 已上传规则 doc_id=%s" % res.doc_id)
show("② 有个人规则（金额用元）", ask())

# ③ 下架规则后再问
c = pgdb.connect(autocommit=True)
cur = c.cursor()
cur.execute("UPDATE document SET is_active=false WHERE id=%s", (res.doc_id,))
cur.execute("UPDATE document_chunk SET is_retrievable=false WHERE doc_id=%s", (res.doc_id,))
cur.close()
c.close()
print("\n[清理] 已下架 doc_id=%s" % res.doc_id)
show("③ 规则下架后（应回到万元）", ask())
