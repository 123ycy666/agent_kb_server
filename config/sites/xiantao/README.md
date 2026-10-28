# 仙桃（第二期占位）

本目录是**第二期的站点包**，第一期不做。做法：

1. 整目录复制 `suizhou/` 过来，改名 `xiantao/`；
2. 改 `site.yml` 的 `name` / `full_name` / `code` / `dept.city_dept_id`；
3. 用导出脚本或手抄，把仙桃的四道业务口径（`business_rules` / `caliber_rules` /
   `sql_plan_rules` / `work_order_rules`）与 `answer_data` 换成本单位的；
4. 若表名/字段名与随州不同：改 `data/tables.yml`、`data/columns.yml`（P3 落地）；
   差异太大时用 `data/views.sql` 建兼容视图；
5. `.env` 改 `SITE=xiantao`，重启。

**验收：一行代码都没改**（判据 S2）。改不动就说明第一期没重构到位，要回来补。
