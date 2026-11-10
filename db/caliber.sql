-- 指标契约（2026-11-10）：口径做成数据，代码只做解释。
-- 表在 PG（知识库同库），与 prompt_material 一起走版本与缓存失效。
-- 改口径 = 改这里的数据；改代码只有一处通用机制（server/app/secretary/caliber.py）。

CREATE TABLE IF NOT EXISTS caliber_metric (
  id          bigserial PRIMARY KEY,
  code        varchar(64)  NOT NULL UNIQUE,
  label       varchar(128) NOT NULL DEFAULT '',
  unit        varchar(16)  NOT NULL DEFAULT '',
  digits      smallint     NOT NULL DEFAULT 2,
  direction   varchar(16)  NOT NULL DEFAULT '',
  expr        text         NOT NULL DEFAULT '',
  notes       text         NOT NULL DEFAULT '',
  enabled     boolean      NOT NULL DEFAULT true,
  version_no  int          NOT NULL DEFAULT 1,
  updated_by  varchar(64)  NOT NULL DEFAULT '',
  updated_at  timestamptz  NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS caliber_base (
  id           bigserial PRIMARY KEY,
  metric_code  varchar(64) NOT NULL,
  table_name   varchar(64) NOT NULL,
  column_name  varchar(64) NOT NULL,
  agg          varchar(8)  NOT NULL DEFAULT 'sum',
  scope_col    varchar(64) NOT NULL DEFAULT '',
  must         jsonb       NOT NULL DEFAULT '[]',
  sort_key     varchar(64) NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_caliber_base_code ON caliber_base(metric_code);
CREATE UNIQUE INDEX IF NOT EXISTS ux_caliber_base ON caliber_base(metric_code, table_name, column_name, agg);

CREATE TABLE IF NOT EXISTS caliber_metric_ver (
  id        bigserial PRIMARY KEY,
  code      varchar(64) NOT NULL,
  version_no int NOT NULL,
  body      jsonb      NOT NULL,
  saved_by  varchar(64) NOT NULL DEFAULT '',
  saved_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_caliber_ver_code ON caliber_metric_ver(code, version_no DESC);

-- 初始口径：2026 年某供电所的预算/工单四项 + 派生执行率。
-- 列名按 ai_data（工单表用 power_company_id；三张表都没有 deleted_flag）。
-- 2026-11-10 切库：ai_data（原 dlj_data）。
INSERT INTO caliber_metric (code, label, unit, digits, direction, expr, notes) VALUES
  ('defect_work_order_count', '缺陷治理工单数量', '件', 0, '', '',
   '状态 2/3 的缺陷治理工单，按问题年份与供电所过滤'),
  ('defect_work_order_budget', '工单预算总额', '元', 2, '', '',
   '状态 2/3 的缺陷治理工单预算合计'),
  ('budget_allocated', '预算分配总额', '元', 2, '', '',
   '供电所年度分配预算'),
  ('budget_exec_rate', '预算执行率', '%', 2, 'bigger_better',
   'defect_work_order_budget / budget_allocated * 100', '工单预算总额除以预算分配总额，乘 100 是百分数')
ON CONFLICT (code) DO NOTHING;

INSERT INTO caliber_base (metric_code, table_name, column_name, agg, scope_col, must) VALUES
  ('defect_work_order_count', 't_power_ai_work_order', 'id', 'count', 'power_company_id',
   '[{"col":"year","op":"=","value":"$问题年份"},{"col":"work_status","op":"in","value":["2","3"]}]'),
  ('defect_work_order_budget', 't_power_ai_work_order', 'total_budget', 'sum', 'power_company_id',
   '[{"col":"year","op":"=","value":"$问题年份"},{"col":"work_status","op":"in","value":["2","3"]}]'),
  ('budget_allocated', 't_power_ai_company_budget_result', 'budget_amount', 'sum', 'power_station_id',
   '[{"col":"year","op":"=","value":"$问题年份"}]')
ON CONFLICT (metric_code, table_name, column_name, agg) DO UPDATE
  SET scope_col = EXCLUDED.scope_col, must = EXCLUDED.must;
