-- Rollback: 0002_core_kb_tables
--
-- Apply:
--   psql "$DATABASE_URL" -f app/migrations/0002_core_kb_tables.down.sql

BEGIN;

DROP TABLE IF EXISTS app.t_fact_kb_chunk;
DROP TABLE IF EXISTS app.t_fact_idempotency_key;
DROP TABLE IF EXISTS app.t_fact_import_job;
DROP TABLE IF EXISTS app.t_dim_kb_file;
DROP TABLE IF EXISTS app.t_dim_knowledge_base;

DELETE FROM app.schema_migrations
WHERE version = '0002_core_kb_tables';

COMMIT;
