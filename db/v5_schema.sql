-- =====================================================================
-- v5 结构：kb / cfg / qa / base 四分组 + public 兼容视图
-- 幂等：可重复执行。表搬迁与视图由 db/migrate_v5.py 生成执行（需要动态 SQL），
-- 本文件放"建 schema + 新表 + 授权"这些静态 DDL。
-- =====================================================================

CREATE SCHEMA IF NOT EXISTS kb;
CREATE SCHEMA IF NOT EXISTS cfg;
CREATE SCHEMA IF NOT EXISTS qa;
CREATE SCHEMA IF NOT EXISTS base;

COMMENT ON SCHEMA kb   IS '知识库：空间/文档/切片/说明卡/引用/角色/版本/标签/冲突';
COMMENT ON SCHEMA cfg  IS '配置：注入文本/注入预算/问数链路/词典/补全表/口径/字典覆盖';
COMMENT ON SCHEMA qa   IS '问答记录：会话/消息/每轮口径/每步执行/缺口/审计/评测';
COMMENT ON SCHEMA base IS '基础资料：用户/角色/偏好/人设/模型/任务/预警';
