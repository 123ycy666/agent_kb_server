-- 提示词材料 / 补全表：**只建表，不灌内容**（2026-11-03 定稿，Fred）
--
-- 为什么改成空的：
--   原来这个文件由 tools/import_rules_to_materials.py 生成，开头是
--     DELETE FROM prompt_material WHERE slot IN ('fetch','judge','answer','check');
--     DELETE FROM complete_rule;
--   然后灌 53 条业务口径。主分支要的是"空壳 + 页面上自己写"，这份种子文件正好相反：
--   它会在**空表时**把业务规则原样灌回线上（2026-11-03 部署实测：清空后重新部署，
--   deploy.sh 判「表是空的，首次初始化」，又导了 53 行回来）。
--   原文整份备份：docs/08-历史归档/2026-11-03-删除的提示词规则/prompt_materials-原文.sql
--
-- 现在这个文件只负责"表不存在时建出来"，内容由 Fred 在 /prompts 页面上写。

CREATE TABLE IF NOT EXISTS prompt_material (
  id bigserial PRIMARY KEY,
  slot varchar(16) NOT NULL,
  section varchar(160) NOT NULL,
  body text NOT NULL,
  sort_no int NOT NULL DEFAULT 0,
  enabled boolean NOT NULL DEFAULT true,
  version_no int NOT NULL DEFAULT 1,
  updated_by varchar(64) NOT NULL DEFAULT '',
  updated_at timestamptz NOT NULL DEFAULT now());

CREATE INDEX IF NOT EXISTS idx_material_slot
  ON prompt_material (slot, enabled, sort_no);

ALTER TABLE prompt_material
  ADD COLUMN IF NOT EXISTS pinned boolean NOT NULL DEFAULT true;

CREATE TABLE IF NOT EXISTS complete_rule (
  id bigserial PRIMARY KEY,
  kind varchar(16) NOT NULL DEFAULT 'norm',
  match_text varchar(200) NOT NULL,
  fill_text varchar(300) NOT NULL,
  priority int NOT NULL DEFAULT 0,
  enabled boolean NOT NULL DEFAULT true,
  updated_at timestamptz NOT NULL DEFAULT now());
