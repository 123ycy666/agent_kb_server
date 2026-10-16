# 推送到 GitHub 操作方案

> 目标仓库：https://github.com/HengKang666/Project-Management-Secretary
> 生成时间：2026-10-15

## 一、结论

**可以推。** 本地项目与线上仓库方向一致——本地是线上 `secretary` 智能体重构合并后的**升级版**。
但必须清楚：这不是「增量更新」，而是**用新版整体替换线上结构**。

## 二、现状对比

| 维度 | 线上 (main) | 本地 |
|---|---|---|
| 顶层结构 | `secretary/` `skills/` `tools/` `verify/` `deploy/` `docs/` | `server/` `web/` `docs/` `docker/` `deploy/` |
| 文件数 | 113 | 213（排除 node_modules/dist 后） |
| 技术形态 | 纯 Python 脚本 + 静态 HTML | FastAPI + Vue/Vite 全栈工程 |
| 路径交集 | 仅 `.env.example` `.gitignore` `README.md` 3 个，且内容均已改动 | 同左 |
| 关系 | 合并前的 PMS-fresh 问数智能体 | 合并后的「统一知识库与智能问数平台」 |

## 三、已完成的准备（本地操作，未碰远程）

1. `git init -b main` —— 分支名与线上一致。
2. 首次提交审计：**213 个文件待提交，确认无 `.env`、无 `node_modules`、无 `dist`、无 `__pycache__`、无上传文件**。
3. `.gitignore` 补充 `.workbuddy/`，避免本地记忆日志与临时分析文件入库。

## 四、推荐方案：新建分支推送（不动 main，可回退）

**理由**：main 与旧历史原样保留，新版以新分支 + PR 形式对比后再合并，最安全；也顺带解决「旧内容是否保留」——**旧内容天然留在 main 与历史里**，无需特殊处理。

```bash
cd "D:/Temporary download/work-space/合并系统+统一用户表"

# 1) 配置提交身份（首次使用需配置）
git config user.name  "HengKang666"        # 换成你的 GitHub 用户名
git config user.email "你的邮箱"

# 2) 首次提交
git add -A
git status --short                          # ★ 再核一眼：不应出现 .env / node_modules
git commit -m "refactor: 合并知识库与问数智能体为统一平台 v2"

# 3) 关联远程并推送为新分支（不动 main）
git remote add origin https://github.com/HengKang666/Project-Management-Secretary.git
git push -u origin main:refactor/unified-v2
```

推送后在 GitHub 上对 `refactor/unified-v2` 开 PR，与 `main` 对比 review，确认无误再合并。

## 五、备选方案：强推覆盖 main

仅当「这仓库只归你一个人、就是要把线上换成新版」时使用。
**强推前先备份旧版**：

```bash
git remote add origin https://github.com/HengKang666/Project-Management-Secretary.git
git push origin main:backup/before-v2 --force     # 先把线上现版存成一个备份分支
git push -f origin main:main                      # 再覆盖 main
```

## 六、推送前 60 秒自检

- [ ] `git status --short` 无 `.env`、`node_modules`、`dist`、`data/`
- [ ] `git config user.name / user.email` 已设置
- [ ] 如希望把种子示例文档 `data/seed_docs` 一起入库，需在 `.gitignore` 中放开（当前 `data/` 被整体忽略）
- [ ] 确认远程仓库你**有写权限**（是 owner 或 collaborator）

## 七、风险与回滚

| 风险 | 说明 | 应对 |
|---|---|---|
| 历史无关被拒推 | 两边无共同祖先，普通 push 会 non-fast-forward | 用新分支推送，或 `--force`（仅覆盖 main 时） |
| 强推丢失线上提交 | 线上现有 commit 从 main 移除 | 先推 `backup/before-v2` 备份分支 |
| 线上存在他人 PR | 线上有 `refs/pull/1`、`refs/pull/2` | 若他人有协作，优先用新分支 + PR，避免强推 |
| 密钥泄漏 | `.env` 含数据库密码与模型 API Key | 已确认被 `.gitignore` 排除；提交前务必再核 |
