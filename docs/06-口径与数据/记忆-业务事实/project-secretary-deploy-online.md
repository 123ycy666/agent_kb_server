---
name: project-secretary-deploy-online
description: 项目管理秘书的线上部署拓扑（47.121.183.81）与一键部署脚本 tools/deploy_update.py 的用法与坑
metadata:
  node_type: memory
  type: project
---

**结论**：线上 = `47.121.183.81:8200`，代码在 `/root/agentkownlg/deploy_server/`，systemd 单元 `secretary.service`（WorkingDirectory 指向该目录下的 `secretary/`，`/bin/python3` = 3.9.6，Restart=always）。更新一律用仓库里的 `tools/deploy_update.py`（2026-09-22 建）。

**为什么有这条**：.env 里的 `SERVER_HOST=47.121.127.9` 是**已废弃的旧机**（公网不可达），照它连会一直超时；真正的机器是 `SERVER_SSH_PASSWORD` 对应的 **183.81**（同时也是 MySQL 那台）。

**How to apply**
1. 只读对比：`py -X utf8 tools/deploy_update.py --check`（跑前先看差异，不写任何东西）。
2. 全量更新：`py -X utf8 tools/deploy_update.py`。流程 = 备份 → 传 `_staging_deploy` → 逐文件 md5 校验 → 原子切换目录 → 重启 → /health + /api/skills + /api/skill/title + 冒烟；任一步失败自动把 .bak 换回来再重启。
3. 脚本**默认保留服务器上的 .env / output/**，不覆盖运行态；要连 .env 一起推加 `--with-env`。
4. 上传名单由 `.gitignore` 过滤（`git check-ignore`）—— 口径库/数据地图/metric_catalog 这类业务数据不会被传上去。**注意**：往 `git check-ignore --stdin` 里传 str 时 Windows 会把 \n 变 \r\n，路径带 \r 就匹配不上，必须传 bytes。
5. 备份保留最近 3 份：`/root/agentkownlg/deploy_server.bak.<时间戳>`，整目录 `cp -a`，可直接 `mv` 回去回滚（2026-09-22 演练过：备份能在备用端口起起来、/health 四字段正常）。
6. **线上原本没有 skills/ 目录**（部署文档说 skills 不用传），导致 /api/skills 与 /api/skill/* 在线上一直是坏的（空）。脚本已把 `skills/`、`tools/` 纳入上传。

[[project-secretary-kb-retrieval-auth]]
