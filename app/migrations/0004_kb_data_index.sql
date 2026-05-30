-- Migration: 0004_kb_data_index
-- Description: FastGPT「数据↔多向量」模型，知识库原文表与向量索引表（含 pgvector 256 维）
--              并为导入任务选项表补齐 chunking_config jsonb
-- Depends on: 0003_domain_business_tables
--
-- Apply:
--   psql "$DATABASE_URL" -f app/migrations/0004_kb_data_index.sql

BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS app;

-- ---------------------------------------------------------------------------
-- t_fact_kb_data — 知识库原文数据（检索返回单位，1 data 对 N index）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app.t_fact_kb_data (
    id              bigserial PRIMARY KEY,
    kb_id           text NOT NULL REFERENCES app.t_dim_knowledge_base (id) ON DELETE CASCADE,
    file_id         text NOT NULL REFERENCES app.t_dim_kb_file (id) ON DELETE CASCADE,
    data_id         text NOT NULL,
    text            text NOT NULL,
    page            integer,
    chunk_index     integer NOT NULL DEFAULT 0,
    citation        jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT t_fact_kb_data_chunk_index_chk
        CHECK (chunk_index >= 0),
    CONSTRAINT t_fact_kb_data_page_chk
        CHECK (page IS NULL OR page >= 0),
    CONSTRAINT t_fact_kb_data_kb_file_data_uq
        UNIQUE (kb_id, file_id, data_id)
);

CREATE INDEX IF NOT EXISTS t_fact_kb_data_kb_id_idx
    ON app.t_fact_kb_data (kb_id);

CREATE INDEX IF NOT EXISTS t_fact_kb_data_file_id_idx
    ON app.t_fact_kb_data (file_id);

CREATE INDEX IF NOT EXISTS t_fact_kb_data_kb_id_file_id_idx
    ON app.t_fact_kb_data (kb_id, file_id);

-- ---------------------------------------------------------------------------
-- t_fact_kb_index — 知识库向量索引（pgvector 256 维，逻辑关联 t_fact_kb_data）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app.t_fact_kb_index (
    id              bigserial PRIMARY KEY,
    kb_id           text NOT NULL REFERENCES app.t_dim_knowledge_base (id) ON DELETE CASCADE,
    file_id         text NOT NULL REFERENCES app.t_dim_kb_file (id) ON DELETE CASCADE,
    data_id         text NOT NULL,
    index_id        text NOT NULL,
    text            text NOT NULL,
    embedding       vector(256) NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT t_fact_kb_index_kb_file_index_uq
        UNIQUE (kb_id, file_id, index_id),
    CONSTRAINT t_fact_kb_index_data_fkey
        FOREIGN KEY (kb_id, file_id, data_id)
        REFERENCES app.t_fact_kb_data (kb_id, file_id, data_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS t_fact_kb_index_kb_id_idx
    ON app.t_fact_kb_index (kb_id);

CREATE INDEX IF NOT EXISTS t_fact_kb_index_file_id_idx
    ON app.t_fact_kb_index (file_id);

CREATE INDEX IF NOT EXISTS t_fact_kb_index_kb_id_file_id_idx
    ON app.t_fact_kb_index (kb_id, file_id);

CREATE INDEX IF NOT EXISTS t_fact_kb_index_kb_file_data_idx
    ON app.t_fact_kb_index (kb_id, file_id, data_id);

CREATE INDEX IF NOT EXISTS t_fact_kb_index_embedding_hnsw_idx
    ON app.t_fact_kb_index
    USING hnsw (embedding vector_cosine_ops);

-- ---------------------------------------------------------------------------
-- ALTER：导入任务选项表补齐 chunking_config jsonb
-- ---------------------------------------------------------------------------
ALTER TABLE app.t_fact_import_job_option
    ADD COLUMN IF NOT EXISTS chunking_config jsonb;

INSERT INTO app.schema_migrations (version)
VALUES ('0004_kb_data_index')
ON CONFLICT (version) DO NOTHING;

COMMIT;
