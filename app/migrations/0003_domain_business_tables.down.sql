-- Rollback: 0003_domain_business_tables
--
-- Apply:
--   psql "$DATABASE_URL" -f app/migrations/0003_domain_business_tables.down.sql

BEGIN;

-- 回滚 t_fact_kb_chunk 新增索引
DROP INDEX IF EXISTS app.t_fact_kb_chunk_kb_id_chunk_id_idx;
DROP INDEX IF EXISTS app.t_fact_kb_chunk_chunk_id_idx;

-- 回滚 t_fact_idempotency_key 新增列
ALTER TABLE app.t_fact_idempotency_key
    DROP COLUMN IF EXISTS response_status;

-- 回滚 t_fact_import_job 及明细表
DROP TABLE IF EXISTS app.t_fact_import_job_option;
DROP TABLE IF EXISTS app.t_fact_import_job_file;

DROP INDEX IF EXISTS app.t_fact_import_job_retry_of_idx;

ALTER TABLE app.t_fact_import_job
    DROP CONSTRAINT IF EXISTS t_fact_import_job_retry_of_fkey;

ALTER TABLE app.t_fact_import_job
    DROP COLUMN IF EXISTS retry_of;

-- 回滚 t_dim_kb_file 新增列与索引
DROP INDEX IF EXISTS app.t_dim_kb_file_file_format_idx;

ALTER TABLE app.t_dim_kb_file
    DROP CONSTRAINT IF EXISTS t_dim_kb_file_char_count_chk;

ALTER TABLE app.t_dim_kb_file
    DROP COLUMN IF EXISTS tags,
    DROP COLUMN IF EXISTS file_format,
    DROP COLUMN IF EXISTS char_count;

-- 删除 0003 新建事实表（按依赖逆序）
DROP TABLE IF EXISTS app.t_fact_hit_test_result;
DROP TABLE IF EXISTS app.t_fact_hit_test;
DROP TABLE IF EXISTS app.t_fact_upload_object;
DROP TABLE IF EXISTS app.t_fact_kb_grant;
DROP TABLE IF EXISTS app.t_fact_message_feedback;
DROP TABLE IF EXISTS app.t_fact_message_usage;
DROP TABLE IF EXISTS app.t_fact_message_citation;
DROP TABLE IF EXISTS app.t_fact_message;
DROP TABLE IF EXISTS app.t_fact_conversation_kb;
DROP TABLE IF EXISTS app.t_fact_user_role;
DROP TABLE IF EXISTS app.t_fact_user_team;
DROP TABLE IF EXISTS app.t_fact_refresh_token;

-- 删除 0003 新建维度表
DROP TABLE IF EXISTS app.t_dim_conversation;
DROP TABLE IF EXISTS app.t_dim_template;
DROP TABLE IF EXISTS app.t_dim_folder;
DROP TABLE IF EXISTS app.t_dim_model;
DROP TABLE IF EXISTS app.t_dim_role_permission;
DROP TABLE IF EXISTS app.t_dim_permission;
DROP TABLE IF EXISTS app.t_dim_role;
DROP TABLE IF EXISTS app.t_dim_team;
DROP TABLE IF EXISTS app.t_dim_user;

DELETE FROM app.schema_migrations
WHERE version = '0003_domain_business_tables';

COMMIT;
