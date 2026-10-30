# -*- coding: utf-8 -*-
"""线上端到端：三类问题各问一次（正常问题 / 业务问题 / 知识库配置）。

配置类会**真的**往 user_id=1 的个人资料库上传一份规则文档（真实入库流程），
问完把这份文档删掉；其余两类只调 `/api/ask`，不写库。

用法（宿主机）：py -X utf8 _scratch/probe_e2e_online.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[1]
HOST = '47.121.183.81'
USER = 'root'

IN_CONTAINER = r'''# -*- coding: utf-8 -*-
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
'''


def main() -> int:
    txt = (ROOT / '.env').read_text(encoding='utf-8')
    pwd = re.search(r'^SERVER_SSH_PASSWORD\s*=\s*(.+)$', txt, re.M).group(1).strip()
    local = ROOT / '_scratch' / '_e2e_container.py'
    local.write_text(IN_CONTAINER, encoding='utf-8')
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=pwd, timeout=20, look_for_keys=False,
              allow_agent=False)
    try:
        sftp = c.open_sftp()
        sftp.put(str(local), '/tmp/_e2e.py')
        sftp.close()
        chan = c.get_transport().open_session()
        chan.settimeout(900)
        chan.set_combine_stderr(True)
        chan.exec_command('docker cp /tmp/_e2e.py kb-merged:/tmp/_e2e.py && '
                          'docker exec -w /app -e PYTHONPATH=/app kb-merged python /tmp/_e2e.py')
        out = b''
        while True:
            b = chan.recv(65535)
            if not b:
                break
            out += b
        text = out.decode('utf-8', 'replace')
        dest = ROOT / 'output/test_results/2026-10-30-提示词重构'
        dest.mkdir(parents=True, exist_ok=True)
        (dest / 'e2e-online.txt').write_text(text, encoding='utf-8')
        print(text)
        print('[exit]', chan.recv_exit_status(), '| 已存 e2e-online.txt')
        return 0
    finally:
        c.close()


if __name__ == '__main__':
    sys.exit(main())
