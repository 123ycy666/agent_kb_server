---
name: project-secretary-dsv4-thinking-mode
description: 内网 dsv4 绝不能关思考——关掉后过程话术会直接写进 content，领导看到的就是思考过程
metadata:
  node_type: memory
  type: project
---

**结论**：调 dsv4 时**不要**给 `chat_template_kwargs: {'thinking': False}`，让它开思考；只把 `content` 当答案，`reasoning` 永远不要当答案。

**Why**：关思考后模型没有隐藏草稿纸，推理直接落到 `content`。实测（`output/probe_thinking.py`、`output/probe_final_turn.py`）：

| 开关 | content | reasoning |
|---|---|---|
| 关思考 | 过程话术（「这是对策题。回答要点：…」「让我确认一下」） | 0 |
| 开思考／不加参数 | 最终回答（干净） | 过程（实测 847 字） |

2026-10-08 前一轮我用「关思考」去修「content 为空」，结果修出了更严重的毛病：12 题里多题正文混进思考过程、甚至整段回答写两遍，被 Fred 抓出来。

**How to apply**
1. `secretary/agent.py::_chat` 保持不加 thinking 参数，`max_tokens` 给足（默认 4000）：reasoning 也吃 completion token，给小了会把正文挤空。
2. 万一 content 为空：**加大额度重问一次**，绝不回退去读 `reasoning`（那是内心独白）。
3. 代价：单题耗时约翻倍（实测 66~77s → 150~240s）。
4. 换任何新模型都先跑这两个探针，确认过程话术落在哪个字段，再决定读哪个字段。

## 追加（2026-10-14，百炼 deepseek-v4.1-flash 实测）

- **开思考时 content 为空是常态级故障**：第 2 批跑批里 3/7 轮分析页面被丢，日志是 `页面模型返回空正文（thinking=True）` —— reasoning 把 completion token 吃满，正文没地方写。
- 处置（已验证）：**同一个请求换 thinking 再来一次**就出正文。`render_page()` 现在 thinking=True → False 各试一次；`_final_answer()` 是 False → True 各试一次。加完这一层，r4 复跑 7 轮分析题**全部**拿到页面。
- 判据：任何"要正文"的模型调用（作答、写报告、出页面）都必须有"空正文 → 换 thinking 重试"这一层；日志要打 `len(content)`，否则"未生成"这种说法查不出原因。

[[project-secretary-prompt-maintenance]]、[[llm-numeric-fidelity]]
