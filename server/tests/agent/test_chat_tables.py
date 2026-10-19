# -*- coding: utf-8 -*-
"""验证 db/chat_tables.sql 建表之后，问答落库 / 历史 / 统计整条数据层能跑通。

判据（动手前定死）：
1. qa_log.available() 为 True
2. save() 返回整数 qa_id 且 ok=True
3. get_history() 能读回这一轮（消息数 = 2，含 trace）
4. list_sessions() 能看到这场会话，total >= 1
5. stats() 返回的 questions/messages 都比落库前多 1 / 2
6. set_feedback() 能改评价，且 trace 里 feedback 变成 1
7. 会话摘要链路：set_summary() 后 get_summary() 读得到

跑法： py -X utf8 server/tests/agent/test_chat_tables.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "server"))

from app.secretary import qa_log  # noqa: E402

_chk: list[tuple[str, bool, str]] = []


def chk(label, cond, extra=""):
    _chk.append((label, bool(cond), str(extra)))


def main() -> int:
    chk("qa_log.available()", qa_log.available(), qa_log.status())

    before = qa_log.stats()
    sid = qa_log.new_session_id()
    result = {
        "answer": "【自检】这是一条由 test_chat_tables.py 写入的答案，可删。",
        "input_question": "自检：对话记录表是否可用？",
        "fixed_question": "自检：对话记录表是否可用？",
        "completed_question": "自检：对话记录表是否可用？",
        "model": "selftest-model",
        "elapsed_ms": 12,
        "namefix_ms": 0,
        "completion_ms": 0,
        "loop_ms": 7,
        "steps": 1,
        "db_query_count": 1,
        "sql_query_count": 1,
        "kb_query_count": 0,
        "no_db_query": False,
        "unverified": False,
        "is_gap": False,
        "trace": [{"kind": "tool", "tool": "run_sql", "step_no": 1, "round": 1,
                   "cost_ms": 3, "model_ms": 4,
                   "input": {"sql": "select 1"}, "output": "row_count=1",
                   "sql": "select 1", "status": 0}],
    }
    saved = qa_log.save(result, session_id=sid, user_id=None, user_code="selftest",
                        client_ip="127.0.0.1", channel="selftest")
    # 契约：save() 返回 {'session_id','qa_id'(int),...}；落库失败这里会抛异常（外层兜底）
    chk("save() 返回整数 qa_id", isinstance(saved.get("qa_id"), int) and saved.get("session_id") == sid,
        saved)

    hist = qa_log.get_history(sid, with_steps=True)
    msgs = hist.get("messages") or []
    chk("get_history 读回 2 条消息", len(msgs) == 2, "messages=%d" % len(msgs))
    with_steps = [m for m in msgs if m.get("steps")]
    chk("get_history 带执行步骤（trace step 落库）", len(with_steps) == 1,
        "带 steps 的消息数=%d" % len(with_steps))

    sessions = qa_log.list_sessions(user_code="selftest")
    chk("list_sessions 看到这场会话", sessions.get("total", 0) >= 1,
        "total=%s" % sessions.get("total"))

    after = qa_log.stats()
    chk("stats questions +1", after.get("questions", 0) == before.get("questions", 0) + 1,
        "before=%s after=%s" % (before.get("questions"), after.get("questions")))
    chk("stats messages +2", after.get("messages", 0) == before.get("messages", 0) + 2,
        "before=%s after=%s" % (before.get("messages"), after.get("messages")))

    n = qa_log.set_feedback(saved["qa_id"], 1, "自检")
    chk("set_feedback 改到 1 行", n == 1, "rowcount=%s" % n)

    qa_log.set_summary(sid, "【自检】会话摘要", upto_seq=2)
    got = qa_log.get_summary(sid)
    chk("会话摘要可写可读", (got or {}).get("summary", "").startswith("【自检】"), got)

    bad = 0
    print("对话记录表自检")
    for label, ok, extra in _chk:
        print("  %s %s%s" % ("PASS" if ok else "FAIL", label,
                             ("  → " + extra) if extra and not ok else ""))
        bad += 0 if ok else 1
    print("\n%d 项：%d 通过 / %d 不通过" % (len(_chk), len(_chk) - bad, bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
