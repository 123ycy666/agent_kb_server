# -*- coding: utf-8 -*-
"""合并契约测试：新旧仓库合流后，秘书本体必须同时满足两件事。

**A. 本地独有的能力还在**（Fred 明确要求"新的项目没有的功能以本地为准"）
**B. 新仓库对秘书做的平台适配也还在**（导入路径 / 进程内检索 / PG 数据层 / 技能目录）

判据（动手前定死，事后不许改）：
1. `agent.TOOLS` 里必须有 `finish`（本地收尾工具；新仓库版本把它丢了）
2. 四道守卫 + 答案清洗函数必须存在（时间/口径/单位/数字池/无出处金额/系数话术）
3. `config.PAGE_ON` 必须是 False（2026-10-19 Fred：正常流程先把页面生成关了）
4. `config.NUMBER_GUARD_ON` / `QUESTION_BUDGET_S` 必须还在（新仓库版本的 config 丢了这两个键）
5. `config.MODEL_CHANNEL` 必须在（新仓库的三通道开关，必须保留）
6. `report.SKILL_DIR` 必须指向 `app/secretary/skills` 且能读到 ≥15 篇（平台布局）
7. 预警三件套可导入，`ALERT_TYPES` 非空，`daily()` 可调用
8. `app/secretary/*.py` 里不许再留裸兄弟模块 import（`import config` 这种，单进程下会 ImportError）
9. `tools_kb` 必须走 `kb_adapter`（进程内检索），不是 `kb_client`
10. `qa_log` / `lex_source` 必须走 `pgdb`，且 `list_sessions` 支持 `user_id`（按登录用户过滤）

运行： python server/tests/agent/test_merge_contract.py
"""
from __future__ import annotations

import inspect
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SECRETARY = HERE.parents[1] / "app" / "secretary"
sys.path.insert(0, str(HERE.parents[1]))

# 用固定夹具配 .env，不碰真实配置；必须在 import config 之前设好
os.environ["SECRETARY_ENV"] = str(HERE / "env.fixture")

_chk: list[tuple[str, bool, str]] = []


def chk(label: str, cond, extra: str = "") -> None:
    _chk.append((label, bool(cond), extra))


def main() -> int:
    try:
        from app.secretary import agent, alerts, config, lex_source, qa_log, report, tools_kb
    except Exception as e:                                    # noqa: BLE001
        print("导入失败：%s: %s" % (type(e).__name__, e))
        print("\n0 项：0 通过 / 1 不通过（导入即失败，其余判据无法评估）")
        return 1

    # 1 ── 本地收尾工具还在
    names = {t["function"]["name"] for t in agent.TOOLS}
    chk("TOOLS 含 finish（本地收尾工具）", "finish" in names, sorted(names))
    chk("TOOLS 含 kb_search/find_column/run_sql",
        {"kb_search", "find_column", "run_sql"} <= names, sorted(names))

    # 2 ── 守卫层
    for fn in ("_time_guard", "_scope_guard", "_unit_guard", "_num_pool",
               "_untraceable_money", "_coefficient_talk", "_clean_answer", "_final_answer"):
        chk("守卫函数 %s 存在" % fn, callable(getattr(agent, fn, None)))

    # 3/4/5 ── 配置开关
    chk("PAGE_ON 为 False（页面生成关闭）", getattr(config, "PAGE_ON", None) is False,
        str(getattr(config, "PAGE_ON", "<缺失>")))
    chk("NUMBER_GUARD_ON 为 True", getattr(config, "NUMBER_GUARD_ON", None) is True,
        str(getattr(config, "NUMBER_GUARD_ON", "<缺失>")))
    chk("QUESTION_BUDGET_S = 240", getattr(config, "QUESTION_BUDGET_S", None) == 240.0,
        str(getattr(config, "QUESTION_BUDGET_S", "<缺失>")))
    chk("KB_TIMEOUT 存在", isinstance(getattr(config, "KB_TIMEOUT", None), int),
        str(getattr(config, "KB_TIMEOUT", "<缺失>")))
    chk("MODEL_CHANNEL 存在且合法",
        getattr(config, "MODEL_CHANNEL", None) in ("local", "online"),
        str(getattr(config, "MODEL_CHANNEL", "<缺失>")))
    chk("MODELS 非空", bool(getattr(config, "MODELS", None)), str(len(getattr(config, "MODELS", []))))

    # 6 ── 技能目录（平台布局）
    sd = Path(getattr(report, "SKILL_DIR", ""))
    chk("SKILL_DIR 指向 secretary/skills",
        sd.name == "skills" and sd.parent.name == "secretary", str(sd))
    docs = report.list_skill_docs()
    chk("技能文档 ≥ 15 篇", len(docs) >= 15, str(len(docs)))
    chk("《判据规则》在技能文档里", any("判据规则" in d["name"] for d in docs), str(len(docs)))

    # 7 ── 预警
    chk("ALERT_TYPES 非空", bool(getattr(alerts.R, "ALERT_TYPES", None)),
        str(len(getattr(alerts.R, "ALERT_TYPES", {}))))
    chk("alerts.daily 可调用", callable(getattr(alerts, "daily", None)))

    # 8 ── 不许留裸兄弟模块 import
    sibling = ("agent|config|gaps|lex_source|name_fix|qa_log|report|semantic|session_summary|"
               "time_scope|tools_asr|tools_db|tools_kb|alerts|alert_rules|alert_scope|"
               "alert_wording|kb_adapter|pgdb")
    pat = re.compile(r"^[ \t]*import[ \t]+(%s)[ \t]*$" % sibling, re.M)
    offenders = []
    for p in sorted(SECRETARY.glob("*.py")):
        hit = pat.findall(p.read_text(encoding="utf-8"))
        if hit:
            offenders.append("%s:%s" % (p.name, ",".join(hit)))
    chk("无裸兄弟模块 import", not offenders, "; ".join(offenders))

    # 9 ── 进程内检索
    chk("tools_kb 走 kb_adapter", hasattr(tools_kb, "kb_adapter"))
    chk("tools_kb 不再依赖 kb_client", not hasattr(tools_kb, "kb_client"))

    # 10 ── PG 数据层
    chk("qa_log 走 pgdb", hasattr(qa_log, "pgdb"))
    chk("lex_source 走 pgdb", hasattr(lex_source, "pgdb"))
    try:
        params = inspect.signature(qa_log.list_sessions).parameters
        chk("list_sessions 支持 user_id", "user_id" in params, str(list(params)))
    except (TypeError, ValueError) as e:
        chk("list_sessions 支持 user_id", False, "%s: %s" % (type(e).__name__, e))

    print("合并契约测试")
    bad = 0
    for label, ok, extra in _chk:
        print("  %s %s%s" % ("PASS" if ok else "FAIL", label, ("  → " + extra) if extra and not ok else ""))
        bad += 0 if ok else 1
    print("\n%d 项：%d 通过 / %d 不通过" % (len(_chk), len(_chk) - bad, bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
