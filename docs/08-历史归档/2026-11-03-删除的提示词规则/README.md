# 2026-11-03 从提示词链路删掉的规则材料（备份）

这一天把提示词链路里的 6 样东西删了，主分支做成「空壳」（不预设任何规则）。
删之前把**原文**导到这里，一份不少，随时可恢复。

## 删了什么 / 原文在哪

| 删掉的东西 | 链路里的节点 | 原文备份 | 代码位置 |
| --- | --- | --- | --- |
| 补全表 | 4 补全 | `补全表.md` | `prompt_material` 同级的 `complete_rule` 表 + `server/app/secretary/complete_rules.py` |
| 查数口径 | 7 去查 | `判据规则-原文.md`（`fetch` 那一份） | `prompt_material` 表 slot=fetch |
| 判定标准 | 9 要不要判 | `判据规则-原文.md`（`judge`） | `prompt_material` 表 slot=judge |
| 作答规则 | 10 作答 | `判据规则-原文.md`（`answer`）+ `汇报口吻-自检清单-作答规则.md` | `prompt_material` slot=answer；`prompts_data.ANSWER_DATA` |
| 汇报口吻 | 10 作答 | `汇报口吻-自检清单-作答规则.md`（COMMON） | `agent.COMMON` |
| 自检清单 | 11 自检 | `汇报口吻-自检清单-作答规则.md`（CHECK） | `agent.CHECK`；`prompt_material` slot=check |
| 业务相关的问题词表 | （判模式 / 预处理） | `业务相关问题词表.md` | `modes._FALLBACK_RULES`、`prompts_data.MODE_RULES`、`preprocess._BIZ` |

## 怎么恢复

**整条链路整份恢复（推荐）**：切到 `online` 分支（线上规则测试分支，规则原样保留）：

```bash
git checkout online
```

**只恢复某一份材料到主分支**：把上表里「原文备份」那一列的内容，按
`docs/01-接口/提示词管理接口.md` 的 `PUT /api/v1/prompts/material/{slot}`
贴回去；或者跑 `py -X utf8 tools/import_rules_to_materials.py --apply`
从 `判据规则-原文.md` 整体重建四份。

**恢复汇报口吻 / 自检清单 / 作答规则（代码常量）**：把
`汇报口吻-自检清单-作答规则.md` 里三段原文贴回 `agent.COMMON`、`agent.CHECK`、
`prompts_data.ANSWER_DATA`，再把 `agent_v2.system()` 的 `answer_format=` 参数接回来。

**恢复业务相关问题词表**：`git revert` 删除它的那次提交即可（词表原文在
`业务相关问题词表.md`，三处都在）。
