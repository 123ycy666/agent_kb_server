---
name: project-secretary-acceptance-points
description: 分析问答的验收方式：docs/分析问答-要点清单.json 定要点，output/judge_points.py 打记号（不许再用关键词判据）
metadata:
  node_type: memory
  type: project
---

**结论**：验收分析问答只用 `output/judge_points.py`，条款来自 `docs/分析问答-要点清单.json`；每题输出 `output/test_results/拆解落地测试_<TAG>_要点记号.md`（逐条 ✓/✗ + 证据）。

**Why**：关键词判据两头都错——正文里写了「环潭供电所」但没用「供电所」三个字就判缺项（12 题里 5 处假阴性）；反之出现「建议」两字就判命中。用假判据得出的「7/12 未达标」结论本身不成立。

**How to apply**
1. 全局项（无思考话术／无 Markdown／无重复段落／首句即结论／不露表名）用确定性正则，机器可复核。
2. 业务要点交给验收模型逐条判，**必须回引原文**，引不到原文按「没答」算（防验收方替答题方脑补）。
3. 验收模型偏松：要点里凡要求「算出／写出」某结果数的，只有原料数字（只列第一、第二名分数）→ 必须判 false（`judge_points.py` 规则 5）。
4. **先验一题再跑全套**：改完先 `TEST_ONLY=N` 跑单题 + `judge_points.py --only N`，人工把正文读一遍，机器记号与人工判断一致（或差异有解释）才跑 12 题。
5. 一条要点连续两次改提示词都不生效 → 停手，重新审这条要点本身是否值得要求，别继续加词。

[[project-secretary-dsv4-thinking-mode]]
