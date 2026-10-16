-- ══════════════════════════════════════════════════════════════
--  PostgreSQL 初始化脚本（容器首次启动时执行）
--  作用：启用 pgvector 与 pg_bigm 扩展
-- ══════════════════════════════════════════════════════════════

-- 向量检索扩展（pgvector 官方镜像已预装）
CREATE EXTENSION IF NOT EXISTS vector;

-- 中文全文检索扩展（pg_bigm）
-- 若镜像未预装，此处会失败但不影响 pgvector 使用，
-- 应用层会自动降级为 tsvector + ILIKE 模糊匹配。
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS pg_bigm;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'pg_bigm 未安装，将降级为 tsvector 模式。如需中文全文检索请安装 pg_bigm。';
END
$$;
