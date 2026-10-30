# -*- coding: utf-8 -*-
"""线上验证：个人规则能不能**改变回答行为**（不只是"被念出来"）。

做法：往 user_id=1 的个人资料库真上传一份规则「回答里金额一律用元，不要换算成万元」，
然后问同一个预算问题，看回答单位是否从"万元"变成"元"；最后下架该规则再问一次做对照。

用法（宿主机）：py -X utf8 _scratch/probe_personal_rule_behavior.py
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
'''


def main() -> int:
    txt = (ROOT / '.env').read_text(encoding='utf-8')
    pwd = re.search(r'^SERVER_SSH_PASSWORD\s*=\s*(.+)$', txt, re.M).group(1).strip()
    local = ROOT / '_scratch' / '_rule_behavior_container.py'
    local.write_text(IN_CONTAINER, encoding='utf-8')
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=pwd, timeout=20, look_for_keys=False,
              allow_agent=False)
    try:
        sftp = c.open_sftp()
        sftp.put(str(local), '/tmp/_rule_behavior.py')
        sftp.close()
        chan = c.get_transport().open_session()
        chan.settimeout(900)
        chan.set_combine_stderr(True)
        chan.exec_command('docker cp /tmp/_rule_behavior.py kb-merged:/tmp/_rule_behavior.py && '
                          'docker exec -w /app -e PYTHONPATH=/app kb-merged python /tmp/_rule_behavior.py')
        out = b''
        while True:
            b = chan.recv(65535)
            if not b:
                break
            out += b
        text = out.decode('utf-8', 'replace')
        dest = ROOT / 'output/test_results/2026-10-30-提示词重构'
        dest.mkdir(parents=True, exist_ok=True)
        (dest / 'personal-rule-behavior.txt').write_text(text, encoding='utf-8')
        print(text)
        print('[exit]', chan.recv_exit_status(), '| 已存 personal-rule-behavior.txt')
        return 0
    finally:
        c.close()


if __name__ == '__main__':
    sys.exit(main())
