---
name: project-secretary-shell-branch
description: 仓库分两条分支：main=空壳（不预设规则）、online=规则入口打开；空壳开关是 prompt_material.SLOTS
metadata:
  type: project
  scope: project
  date: 2026-11-03
---

# 主分支空壳 / online 测规则（2026-11-03 Fred 定）

## 为什么

规则与部署形态原来混在一条分支：同一套代码既是线上（带 60 条判据）又要当测试环境（要空壳），
删规则只能靠「记得别跑导入脚本」，下次部署又回来了。现在拆成两条 + 代码开关，空壳才可复现。

## 两条分支

| 分支 | 给谁 | 开关 |
| --- | --- | --- |
| `main` | 部署 / 测试环境的空壳 | `prompt_material.SLOTS = ()` —— 连规则入口都关，库里剩行也不注入 |
| `online` | 线上，从头测规则 | `SLOTS = ('fetch', 'judge', 'answer')`，规则内容从空开始，页面可维护 |

差别只有提交 `chore(online): 打开规则入口`；main 上的修正 `git merge main` 同步。
旧规则可恢复：`47b791e`（规则齐全）或 `docs/08-历史归档/2026-11-03-删除的提示词规则/`。

## 那天删掉的 6 份材料（Fred 点名，不再回到链路里）

补全表 `complete_rule`、查数口径 `fetch`、判定标准 `judge`、作答规则 `answer`、
汇报口吻（`agent.COMMON`）、自检清单（`agent.CHECK`）。
S4（汇报口吻 + 交卷检查）与 `prompts_data.ANSWER_DATA` 不再注入 ——
**所以 `project-secretary-answer-discipline` 里「回答纪律只在 agent.COMMON 一处」这条已经过时**。

## 同一天改的三处行为

1. 时间地点规则：没提时间且上一轮也没有 → 默认**上一个月**（11 月问 → 补 10 月），不再是系统当月。
2. 业务词表：命中「不查库就能答」的词（寒暄 / 问概念 / 出文件 / 写作）→ 正常聊天、处理文件，
   不纠错不补全；**没命中 → 一律算业务问题**，走补全 + 纠错。
3. 业务相关的问题词表（`business_topics`、`preprocess._BIZ`）删除 —— 方向就是上面第 2 条：
   用闭集（直通词）判，不拿开集（业务词）去猜。

## 坑

- 判「是不是业务问题」别再加词表：加一次就有人问「补不完吧」（见 gate-not-by-question-side-wordlist）。
- `_scratch/verify_shell.py` 只在 main 有意义；在 online 上它会自己跳过。
- 本机没有 PG（5433 未起）/ MySQL 远端不通时，`py server/tests/...` 会**卡在 connect 上**（拒绝也走 3s+）；
  跑逻辑类测试先 stub `pgdb.connect` / `tools_db._exec`（见 `_scratch/verify_shell.py` 开头）。
  要真库的用 `pgserver`（test_prompt_crud / test_page_admin 自带）。

## 2026-11-03 第二轮：真正的空壳（Fred 原话：「整个是空壳，我填什么提示词就是什么生效」）

- **清空**（从代码删、页面写成 key、默认空）：S0 红线、S1 优先级、装配说明、「今天的日期是…」、
  【判断标准从哪来】、两处「第 N 轮了…」催促语。新增页面 key：`redlines` / `priority`。
  原文备份 `docs/08-历史归档/2026-11-03-删除的提示词规则/红线-优先级-装配说明-原文.md`。
- **身份保留**（`identity` key）；模式做法（`modes`）保留 —— 现在 system 只剩这两段，约 978 字。
- **守卫放宽**：`evidence_gate` 取消 `ACT_BLOCK`（只提醒一次就放行）；同会话历史查过的数
  （答案断言都能在历史回答里找到）直接 pass；数字闸池纳入历史金额；对话历史里
  「不许引用历史数字、必须重查」改成「口径没变可直接沿用」。
  **判据**：这是智能体不是查数工具 —— 守卫拦的应该是"凭空编"，不是"复用了上一轮查到的数"。
- **地点文案**：没认出单位名且无上一轮时，原来写「沿用上一轮：全市」（假话），现在写「按全市取数」。
- **坑（2026-11-03 修）**：`prompts_data.IDENTITY` 里的 `{SITE_FULL}` / `{SITE_NAME}` 从来没被替换，
  模型看到的是花括号。现在装配与页面都走 `project.fill()`。
- **未动**：`report.py`（出报告链路仍是技能文档 + 判据规则 + 12 条纪律）—— Fred 说先不动，只清问答链路。
