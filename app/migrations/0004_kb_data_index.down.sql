-- Rollback: 0004_kb_data_index
--
-- Apply:
--   psql "$DATABASE_URL" -f app/migrations/0004_kb_data_index.down.sql

BEGIN;

-- 回滚导入任务选项表 chunking_config 列
ALTER TABLE app.t_fact_import_job_option
    DROP COLUMN IF EXISTS chunking_config;

-- 回滚向量索引表（先 index 后 data，遵循外键依赖逆序）
DROP INDEX IF EXISTS app.t_fact_kb_index_embedding_hnsw_idx;
DROP INDEX IF EXISTS app.t_fact_kb_index_kb_file_data_idx;
DROP INDEX IF EXISTS app.t_fact_kb_index_kb_id_file_id_idx;
DROP INDEX IF EXISTS app.t_fact_kb_index_file_id_idx;
DROP INDEX IF EXISTS app.t_fact_kb_index_kb_id_idx;
DROP TABLE IF EXISTS app.t_fact_kb_index;

DROP INDEX IF EXISTS app.t_fact_kb_data_kb_id_file_id_idx;
DROP INDEX IF EXISTS app.t_fact_kb_data_file_id_idx;
DROP INDEX IF EXISTS app.t_fact_kb_data_kb_id_idx;
DROP TABLE IF EXISTS app.t_fact_kb_data;

DELETE FROM app.schema_migrations
WHERE version = '0004_kb_data_index';

COMMIT;
