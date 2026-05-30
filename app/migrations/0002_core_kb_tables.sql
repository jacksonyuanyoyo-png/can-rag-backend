-- Migration: 0002_core_kb_tables
-- Description: 知识库核心维度表与事实表（含 pgvector 256 维向量块）
-- Depends on: 0001_database_bootstrap (app schema + schema_migrations)
--
-- Apply:
--   psql "$DATABASE_URL" -f app/migrations/0002_core_kb_tables.sql

BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS app;

-- ---------------------------------------------------------------------------
-- t_dim_knowledge_base — 知识库主数据
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app.t_dim_knowledge_base (
    id              text PRIMARY KEY,
    name            text NOT NULL,
    description     text,
    scope           text NOT NULL,
    visibility      text NOT NULL,
    status          text NOT NULL DEFAULT 'active',
    file_count      integer NOT NULL DEFAULT 0,
    chunk_count     integer NOT NULL DEFAULT 0,
    total_bytes     bigint NOT NULL DEFAULT 0,
    owner_user_id   text,
    team_id         text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT t_dim_knowledge_base_scope_chk
        CHECK (scope IN ('personal', 'team')),
    CONSTRAINT t_dim_knowledge_base_visibility_chk
        CHECK (visibility IN ('private', 'team_read', 'team_write')),
    CONSTRAINT t_dim_knowledge_base_status_chk
        CHECK (status IN ('active', 'indexing', 'error', 'archived')),
    CONSTRAINT t_dim_knowledge_base_file_count_chk
        CHECK (file_count >= 0),
    CONSTRAINT t_dim_knowledge_base_chunk_count_chk
        CHECK (chunk_count >= 0),
    CONSTRAINT t_dim_knowledge_base_total_bytes_chk
        CHECK (total_bytes >= 0)
);

CREATE UNIQUE INDEX IF NOT EXISTS t_dim_knowledge_base_name_uq
    ON app.t_dim_knowledge_base (name);

CREATE INDEX IF NOT EXISTS t_dim_knowledge_base_status_idx
    ON app.t_dim_knowledge_base (status);

CREATE INDEX IF NOT EXISTS t_dim_knowledge_base_owner_user_id_idx
    ON app.t_dim_knowledge_base (owner_user_id);

CREATE INDEX IF NOT EXISTS t_dim_knowledge_base_team_id_idx
    ON app.t_dim_knowledge_base (team_id);

-- ---------------------------------------------------------------------------
-- t_dim_kb_file — 知识库文件主数据
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app.t_dim_kb_file (
    id              text PRIMARY KEY,
    kb_id           text NOT NULL REFERENCES app.t_dim_knowledge_base (id) ON DELETE CASCADE,
    file_name       text NOT NULL,
    mime_type       text NOT NULL,
    size_bytes      bigint NOT NULL DEFAULT 0,
    storage_key     text NOT NULL,
    status          text NOT NULL DEFAULT 'uploaded',
    chunk_strategy  text NOT NULL DEFAULT 'fixed_size',
    error_message   text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT t_dim_kb_file_size_bytes_chk
        CHECK (size_bytes >= 0),
    CONSTRAINT t_dim_kb_file_status_chk
        CHECK (status IN ('uploaded', 'parsing', 'chunking', 'indexing', 'ready', 'failed')),
    CONSTRAINT t_dim_kb_file_chunk_strategy_chk
        CHECK (chunk_strategy IN ('fixed_size', 'semantic', 'page'))
);

CREATE UNIQUE INDEX IF NOT EXISTS t_dim_kb_file_storage_key_uq
    ON app.t_dim_kb_file (storage_key);

CREATE INDEX IF NOT EXISTS t_dim_kb_file_kb_id_idx
    ON app.t_dim_kb_file (kb_id);

CREATE INDEX IF NOT EXISTS t_dim_kb_file_kb_id_status_idx
    ON app.t_dim_kb_file (kb_id, status);

CREATE UNIQUE INDEX IF NOT EXISTS t_dim_kb_file_kb_id_file_name_uq
    ON app.t_dim_kb_file (kb_id, file_name);

-- ---------------------------------------------------------------------------
-- t_fact_import_job — 导入任务头
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app.t_fact_import_job (
    id                  text PRIMARY KEY,
    kb_id               text NOT NULL REFERENCES app.t_dim_knowledge_base (id) ON DELETE CASCADE,
    status              text NOT NULL DEFAULT 'queued',
    progress            smallint NOT NULL DEFAULT 0,
    stage               text NOT NULL DEFAULT 'upload',
    error_code          text,
    error_message       text,
    created_by_user_id  text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT t_fact_import_job_status_chk
        CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
    CONSTRAINT t_fact_import_job_progress_chk
        CHECK (progress >= 0 AND progress <= 100),
    CONSTRAINT t_fact_import_job_stage_chk
        CHECK (stage IN ('upload', 'parse', 'chunk', 'embed', 'index', 'done'))
);

CREATE INDEX IF NOT EXISTS t_fact_import_job_kb_id_idx
    ON app.t_fact_import_job (kb_id);

CREATE INDEX IF NOT EXISTS t_fact_import_job_kb_id_status_idx
    ON app.t_fact_import_job (kb_id, status);

CREATE INDEX IF NOT EXISTS t_fact_import_job_created_at_idx
    ON app.t_fact_import_job (created_at DESC);

-- ---------------------------------------------------------------------------
-- t_fact_idempotency_key — 写接口幂等键记录
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app.t_fact_idempotency_key (
    id              text PRIMARY KEY,
    user_id         text NOT NULL,
    idempotency_key text NOT NULL,
    request_hash    text NOT NULL,
    response_body   jsonb,
    http_method     text,
    request_path    text,
    expires_at      timestamptz NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS t_fact_idempotency_key_user_key_uq
    ON app.t_fact_idempotency_key (user_id, idempotency_key);

CREATE INDEX IF NOT EXISTS t_fact_idempotency_key_expires_at_idx
    ON app.t_fact_idempotency_key (expires_at);

-- ---------------------------------------------------------------------------
-- t_fact_kb_chunk — 知识库向量块（pgvector 256 维）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app.t_fact_kb_chunk (
    id              bigserial PRIMARY KEY,
    kb_id           text NOT NULL REFERENCES app.t_dim_knowledge_base (id) ON DELETE CASCADE,
    file_id         text NOT NULL REFERENCES app.t_dim_kb_file (id) ON DELETE CASCADE,
    chunk_id        text NOT NULL,
    chunk_index     integer NOT NULL DEFAULT 0,
    text            text NOT NULL,
    embedding       vector(256) NOT NULL,
    citation        jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT t_fact_kb_chunk_chunk_index_chk
        CHECK (chunk_index >= 0),
    CONSTRAINT t_fact_kb_chunk_kb_file_chunk_uq
        UNIQUE (kb_id, file_id, chunk_id)
);

CREATE INDEX IF NOT EXISTS t_fact_kb_chunk_kb_id_idx
    ON app.t_fact_kb_chunk (kb_id);

CREATE INDEX IF NOT EXISTS t_fact_kb_chunk_file_id_idx
    ON app.t_fact_kb_chunk (file_id);

CREATE INDEX IF NOT EXISTS t_fact_kb_chunk_kb_id_file_id_idx
    ON app.t_fact_kb_chunk (kb_id, file_id);

CREATE INDEX IF NOT EXISTS t_fact_kb_chunk_embedding_hnsw_idx
    ON app.t_fact_kb_chunk
    USING hnsw (embedding vector_cosine_ops);

INSERT INTO app.schema_migrations (version)
VALUES ('0002_core_kb_tables')
ON CONFLICT (version) DO NOTHING;

COMMIT;
