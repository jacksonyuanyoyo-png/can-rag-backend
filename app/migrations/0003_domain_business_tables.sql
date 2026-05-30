-- Migration: 0003_domain_business_tables
-- Description: 补齐 backend-api-dev-doc 全域业务表（t_dim_ / t_fact_ 命名规范）
-- Depends on: 0002_core_kb_tables
--
-- Apply:
--   psql "$DATABASE_URL" -f app/migrations/0003_domain_business_tables.sql

BEGIN;

CREATE SCHEMA IF NOT EXISTS app;

-- ---------------------------------------------------------------------------
-- 维度表：用户 / 团队 / 权限
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app.t_dim_user (
    id              text PRIMARY KEY,
    email           text NOT NULL,
    display_name    text NOT NULL,
    password_hash   text,
    status          text NOT NULL DEFAULT 'active',
    default_team_id text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT t_dim_user_status_chk
        CHECK (status IN ('active', 'inactive', 'suspended'))
);

CREATE UNIQUE INDEX IF NOT EXISTS t_dim_user_email_uq
    ON app.t_dim_user (lower(email));

CREATE INDEX IF NOT EXISTS t_dim_user_status_idx
    ON app.t_dim_user (status);

CREATE TABLE IF NOT EXISTS app.t_dim_team (
    id          text PRIMARY KEY,
    name        text NOT NULL,
    status      text NOT NULL DEFAULT 'active',
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT t_dim_team_status_chk
        CHECK (status IN ('active', 'inactive', 'archived'))
);

CREATE UNIQUE INDEX IF NOT EXISTS t_dim_team_name_uq
    ON app.t_dim_team (name);

CREATE TABLE IF NOT EXISTS app.t_dim_role (
    id          text PRIMARY KEY,
    code        text NOT NULL,
    name        text NOT NULL,
    scope       text NOT NULL DEFAULT 'team',
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT t_dim_role_scope_chk
        CHECK (scope IN ('system', 'team'))
);

CREATE UNIQUE INDEX IF NOT EXISTS t_dim_role_code_uq
    ON app.t_dim_role (code);

CREATE TABLE IF NOT EXISTS app.t_dim_permission (
    id          text PRIMARY KEY,
    code        text NOT NULL,
    domain      text NOT NULL,
    description text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS t_dim_permission_code_uq
    ON app.t_dim_permission (code);

CREATE INDEX IF NOT EXISTS t_dim_permission_domain_idx
    ON app.t_dim_permission (domain);

CREATE TABLE IF NOT EXISTS app.t_dim_role_permission (
    role_id         text NOT NULL REFERENCES app.t_dim_role (id) ON DELETE CASCADE,
    permission_id   text NOT NULL REFERENCES app.t_dim_permission (id) ON DELETE CASCADE,
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (role_id, permission_id)
);

-- ---------------------------------------------------------------------------
-- 维度表：模型 / 文件夹 / 模板 / 会话
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app.t_dim_model (
    id              text PRIMARY KEY,
    code            text NOT NULL,
    display_name    text NOT NULL,
    provider        text,
    icon            text,
    status          text NOT NULL DEFAULT 'active',
    visibility      text NOT NULL DEFAULT 'system',
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT t_dim_model_status_chk
        CHECK (status IN ('active', 'inactive', 'deprecated')),
    CONSTRAINT t_dim_model_visibility_chk
        CHECK (visibility IN ('system', 'team', 'personal'))
);

CREATE UNIQUE INDEX IF NOT EXISTS t_dim_model_code_uq
    ON app.t_dim_model (code);

CREATE INDEX IF NOT EXISTS t_dim_model_status_idx
    ON app.t_dim_model (status);

CREATE TABLE IF NOT EXISTS app.t_dim_folder (
    id              text PRIMARY KEY,
    name            text NOT NULL,
    owner_user_id   text REFERENCES app.t_dim_user (id) ON DELETE SET NULL,
    team_id         text REFERENCES app.t_dim_team (id) ON DELETE SET NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS t_dim_folder_owner_user_id_idx
    ON app.t_dim_folder (owner_user_id);

CREATE INDEX IF NOT EXISTS t_dim_folder_team_id_idx
    ON app.t_dim_folder (team_id);

CREATE UNIQUE INDEX IF NOT EXISTS t_dim_folder_owner_name_uq
    ON app.t_dim_folder (owner_user_id, name)
    WHERE owner_user_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS app.t_dim_template (
    id              text PRIMARY KEY,
    name            text NOT NULL,
    content         text NOT NULL,
    snippet         text,
    scope           text NOT NULL DEFAULT 'personal',
    owner_user_id   text REFERENCES app.t_dim_user (id) ON DELETE SET NULL,
    team_id         text REFERENCES app.t_dim_team (id) ON DELETE SET NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT t_dim_template_scope_chk
        CHECK (scope IN ('personal', 'team', 'system'))
);

CREATE INDEX IF NOT EXISTS t_dim_template_owner_user_id_idx
    ON app.t_dim_template (owner_user_id);

CREATE INDEX IF NOT EXISTS t_dim_template_team_id_idx
    ON app.t_dim_template (team_id);

CREATE TABLE IF NOT EXISTS app.t_dim_conversation (
    id                  text PRIMARY KEY,
    title               text NOT NULL DEFAULT 'New chat',
    owner_user_id       text REFERENCES app.t_dim_user (id) ON DELETE SET NULL,
    folder_id           text REFERENCES app.t_dim_folder (id) ON DELETE SET NULL,
    status              text NOT NULL DEFAULT 'active',
    pinned              boolean NOT NULL DEFAULT false,
    last_message_at     timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT t_dim_conversation_status_chk
        CHECK (status IN ('active', 'archived', 'deleted'))
);

CREATE INDEX IF NOT EXISTS t_dim_conversation_owner_user_id_idx
    ON app.t_dim_conversation (owner_user_id);

CREATE INDEX IF NOT EXISTS t_dim_conversation_folder_id_idx
    ON app.t_dim_conversation (folder_id);

CREATE INDEX IF NOT EXISTS t_dim_conversation_status_idx
    ON app.t_dim_conversation (status);

CREATE INDEX IF NOT EXISTS t_dim_conversation_last_message_at_idx
    ON app.t_dim_conversation (last_message_at DESC NULLS LAST);

-- ---------------------------------------------------------------------------
-- 事实表：鉴权 / 授权 / 会话绑定
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app.t_fact_refresh_token (
    id                  text PRIMARY KEY,
    user_id             text NOT NULL REFERENCES app.t_dim_user (id) ON DELETE CASCADE,
    refresh_token_hash  text NOT NULL,
    expires_at          timestamptz NOT NULL,
    revoked_at          timestamptz,
    user_agent          text,
    ip_address          inet,
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS t_fact_refresh_token_hash_uq
    ON app.t_fact_refresh_token (refresh_token_hash);

CREATE INDEX IF NOT EXISTS t_fact_refresh_token_user_id_idx
    ON app.t_fact_refresh_token (user_id);

CREATE INDEX IF NOT EXISTS t_fact_refresh_token_expires_at_idx
    ON app.t_fact_refresh_token (expires_at);

CREATE TABLE IF NOT EXISTS app.t_fact_user_team (
    id              text PRIMARY KEY,
    user_id         text NOT NULL REFERENCES app.t_dim_user (id) ON DELETE CASCADE,
    team_id         text NOT NULL REFERENCES app.t_dim_team (id) ON DELETE CASCADE,
    role_in_team    text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS t_fact_user_team_user_team_uq
    ON app.t_fact_user_team (user_id, team_id);

CREATE TABLE IF NOT EXISTS app.t_fact_user_role (
    id          text PRIMARY KEY,
    user_id     text NOT NULL REFERENCES app.t_dim_user (id) ON DELETE CASCADE,
    role_id     text NOT NULL REFERENCES app.t_dim_role (id) ON DELETE CASCADE,
    team_id     text REFERENCES app.t_dim_team (id) ON DELETE CASCADE,
    granted_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS t_fact_user_role_user_id_idx
    ON app.t_fact_user_role (user_id);

CREATE INDEX IF NOT EXISTS t_fact_user_role_team_id_idx
    ON app.t_fact_user_role (team_id);

CREATE TABLE IF NOT EXISTS app.t_fact_conversation_kb (
    id                  text PRIMARY KEY,
    conversation_id     text NOT NULL REFERENCES app.t_dim_conversation (id) ON DELETE CASCADE,
    kb_id               text NOT NULL REFERENCES app.t_dim_knowledge_base (id) ON DELETE CASCADE,
    is_active           boolean NOT NULL DEFAULT true,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS t_fact_conversation_kb_conv_kb_uq
    ON app.t_fact_conversation_kb (conversation_id, kb_id);

CREATE INDEX IF NOT EXISTS t_fact_conversation_kb_kb_id_idx
    ON app.t_fact_conversation_kb (kb_id);

-- ---------------------------------------------------------------------------
-- 事实表：消息 / 引用 / 用量 / 反馈
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app.t_fact_message (
    id                  text PRIMARY KEY,
    conversation_id     text NOT NULL REFERENCES app.t_dim_conversation (id) ON DELETE CASCADE,
    role                text NOT NULL,
    content             text NOT NULL DEFAULT '',
    status              text NOT NULL DEFAULT 'completed',
    model_id            text REFERENCES app.t_dim_model (id) ON DELETE SET NULL,
    edited_at           timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT t_fact_message_role_chk
        CHECK (role IN ('user', 'assistant', 'system', 'tool')),
    CONSTRAINT t_fact_message_status_chk
        CHECK (status IN ('pending', 'streaming', 'completed', 'failed', 'cancelled'))
);

CREATE INDEX IF NOT EXISTS t_fact_message_conversation_id_idx
    ON app.t_fact_message (conversation_id);

CREATE INDEX IF NOT EXISTS t_fact_message_conversation_created_idx
    ON app.t_fact_message (conversation_id, created_at);

CREATE INDEX IF NOT EXISTS t_fact_message_status_idx
    ON app.t_fact_message (status)
    WHERE status IN ('pending', 'streaming');

CREATE TABLE IF NOT EXISTS app.t_fact_message_citation (
    id              text PRIMARY KEY,
    message_id      text NOT NULL REFERENCES app.t_fact_message (id) ON DELETE CASCADE,
    file_id         text REFERENCES app.t_dim_kb_file (id) ON DELETE SET NULL,
    chunk_id        text,
    score           numeric(8, 6),
    snippet         text,
    page            integer,
    rank            integer,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS t_fact_message_citation_message_id_idx
    ON app.t_fact_message_citation (message_id);

CREATE INDEX IF NOT EXISTS t_fact_message_citation_file_id_idx
    ON app.t_fact_message_citation (file_id);

CREATE TABLE IF NOT EXISTS app.t_fact_message_usage (
    id                  text PRIMARY KEY,
    message_id          text NOT NULL UNIQUE REFERENCES app.t_fact_message (id) ON DELETE CASCADE,
    prompt_tokens       integer NOT NULL DEFAULT 0,
    completion_tokens   integer NOT NULL DEFAULT 0,
    total_tokens        integer NOT NULL DEFAULT 0,
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT t_fact_message_usage_tokens_chk
        CHECK (prompt_tokens >= 0 AND completion_tokens >= 0 AND total_tokens >= 0)
);

CREATE TABLE IF NOT EXISTS app.t_fact_message_feedback (
    id          text PRIMARY KEY,
    message_id  text NOT NULL REFERENCES app.t_fact_message (id) ON DELETE CASCADE,
    user_id     text NOT NULL REFERENCES app.t_dim_user (id) ON DELETE CASCADE,
    rating      text NOT NULL,
    comment     text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT t_fact_message_feedback_rating_chk
        CHECK (rating IN ('positive', 'negative'))
);

CREATE UNIQUE INDEX IF NOT EXISTS t_fact_message_feedback_message_user_uq
    ON app.t_fact_message_feedback (message_id, user_id);

CREATE INDEX IF NOT EXISTS t_fact_message_feedback_user_id_idx
    ON app.t_fact_message_feedback (user_id);

-- ---------------------------------------------------------------------------
-- 事实表：知识库授权 / 上传 / 命中测试
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app.t_fact_kb_grant (
    id          text PRIMARY KEY,
    kb_id       text NOT NULL REFERENCES app.t_dim_knowledge_base (id) ON DELETE CASCADE,
    user_id     text NOT NULL REFERENCES app.t_dim_user (id) ON DELETE CASCADE,
    grant_type  text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT t_fact_kb_grant_type_chk
        CHECK (grant_type IN ('read', 'write', 'admin'))
);

CREATE UNIQUE INDEX IF NOT EXISTS t_fact_kb_grant_kb_user_type_uq
    ON app.t_fact_kb_grant (kb_id, user_id, grant_type);

CREATE TABLE IF NOT EXISTS app.t_fact_upload_object (
    id              text PRIMARY KEY,
    kb_id           text NOT NULL REFERENCES app.t_dim_knowledge_base (id) ON DELETE CASCADE,
    user_id         text NOT NULL REFERENCES app.t_dim_user (id) ON DELETE CASCADE,
    file_id         text REFERENCES app.t_dim_kb_file (id) ON DELETE SET NULL,
    file_name       text NOT NULL,
    mime_type       text NOT NULL,
    size_bytes      bigint NOT NULL DEFAULT 0,
    storage_key     text NOT NULL,
    upload_url      text,
    etag            text,
    status          text NOT NULL DEFAULT 'pending',
    expires_at      timestamptz,
    completed_at    timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT t_fact_upload_object_size_bytes_chk
        CHECK (size_bytes >= 0),
    CONSTRAINT t_fact_upload_object_status_chk
        CHECK (status IN ('pending', 'uploading', 'uploaded', 'completed', 'failed', 'expired'))
);

CREATE UNIQUE INDEX IF NOT EXISTS t_fact_upload_object_storage_key_uq
    ON app.t_fact_upload_object (storage_key);

CREATE INDEX IF NOT EXISTS t_fact_upload_object_kb_id_idx
    ON app.t_fact_upload_object (kb_id);

CREATE INDEX IF NOT EXISTS t_fact_upload_object_user_id_idx
    ON app.t_fact_upload_object (user_id);

CREATE INDEX IF NOT EXISTS t_fact_upload_object_status_idx
    ON app.t_fact_upload_object (status);

CREATE TABLE IF NOT EXISTS app.t_fact_hit_test (
    id          text PRIMARY KEY,
    kb_id       text NOT NULL REFERENCES app.t_dim_knowledge_base (id) ON DELETE CASCADE,
    user_id     text NOT NULL REFERENCES app.t_dim_user (id) ON DELETE CASCADE,
    query       text NOT NULL,
    top_k       integer NOT NULL DEFAULT 5,
    latency_ms  integer,
    filters     jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT t_fact_hit_test_top_k_chk
        CHECK (top_k > 0 AND top_k <= 100)
);

CREATE INDEX IF NOT EXISTS t_fact_hit_test_kb_id_idx
    ON app.t_fact_hit_test (kb_id);

CREATE INDEX IF NOT EXISTS t_fact_hit_test_user_id_idx
    ON app.t_fact_hit_test (user_id);

CREATE TABLE IF NOT EXISTS app.t_fact_hit_test_result (
    id              text PRIMARY KEY,
    hit_test_id     text NOT NULL REFERENCES app.t_fact_hit_test (id) ON DELETE CASCADE,
    file_id         text REFERENCES app.t_dim_kb_file (id) ON DELETE SET NULL,
    chunk_id        text,
    score           numeric(8, 6),
    snippet         text,
    page            integer,
    rank            integer NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS t_fact_hit_test_result_hit_test_id_idx
    ON app.t_fact_hit_test_result (hit_test_id);

CREATE INDEX IF NOT EXISTS t_fact_hit_test_result_file_id_idx
    ON app.t_fact_hit_test_result (file_id);

-- ---------------------------------------------------------------------------
-- ALTER：补齐 0002 已有表
-- ---------------------------------------------------------------------------

-- t_dim_kb_file：前端文件列表字段 charCount / format / tags
ALTER TABLE app.t_dim_kb_file
    ADD COLUMN IF NOT EXISTS char_count integer,
    ADD COLUMN IF NOT EXISTS file_format text,
    ADD COLUMN IF NOT EXISTS tags jsonb;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 't_dim_kb_file_char_count_chk'
          AND conrelid = 'app.t_dim_kb_file'::regclass
    ) THEN
        ALTER TABLE app.t_dim_kb_file
            ADD CONSTRAINT t_dim_kb_file_char_count_chk
            CHECK (char_count IS NULL OR char_count >= 0);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS t_dim_kb_file_file_format_idx
    ON app.t_dim_kb_file (file_format);

-- t_fact_import_job：retry_of + 明细表
ALTER TABLE app.t_fact_import_job
    ADD COLUMN IF NOT EXISTS retry_of text;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 't_fact_import_job_retry_of_fkey'
          AND conrelid = 'app.t_fact_import_job'::regclass
    ) THEN
        ALTER TABLE app.t_fact_import_job
            ADD CONSTRAINT t_fact_import_job_retry_of_fkey
            FOREIGN KEY (retry_of) REFERENCES app.t_fact_import_job (id) ON DELETE SET NULL;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS t_fact_import_job_retry_of_idx
    ON app.t_fact_import_job (retry_of);

CREATE TABLE IF NOT EXISTS app.t_fact_import_job_file (
    id              text PRIMARY KEY,
    import_job_id   text NOT NULL
        REFERENCES app.t_fact_import_job (id) ON DELETE CASCADE,
    file_id         text NOT NULL
        REFERENCES app.t_dim_kb_file (id) ON DELETE CASCADE,
    file_status     text NOT NULL DEFAULT 'pending',
    error_code      text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT t_fact_import_job_file_status_chk
        CHECK (file_status IN ('pending', 'running', 'completed', 'failed', 'skipped'))
);

CREATE UNIQUE INDEX IF NOT EXISTS t_fact_import_job_file_job_file_uq
    ON app.t_fact_import_job_file (import_job_id, file_id);

CREATE INDEX IF NOT EXISTS t_fact_import_job_file_file_id_idx
    ON app.t_fact_import_job_file (file_id);

CREATE TABLE IF NOT EXISTS app.t_fact_import_job_option (
    id              text PRIMARY KEY,
    import_job_id   text NOT NULL UNIQUE
        REFERENCES app.t_fact_import_job (id) ON DELETE CASCADE,
    chunk_strategy  text NOT NULL,
    meta_filename   boolean NOT NULL DEFAULT true,
    meta_headings   boolean NOT NULL DEFAULT false,
    chunk_size      integer,
    chunk_overlap   integer,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- t_fact_idempotency_key：与 IdempotencyRepository 对齐 response_status
ALTER TABLE app.t_fact_idempotency_key
    ADD COLUMN IF NOT EXISTS response_status integer;

-- t_fact_kb_chunk：命中测试 / 引用检索索引
CREATE INDEX IF NOT EXISTS t_fact_kb_chunk_chunk_id_idx
    ON app.t_fact_kb_chunk (chunk_id);

CREATE INDEX IF NOT EXISTS t_fact_kb_chunk_kb_id_chunk_id_idx
    ON app.t_fact_kb_chunk (kb_id, chunk_id);

INSERT INTO app.schema_migrations (version)
VALUES ('0003_domain_business_tables')
ON CONFLICT (version) DO NOTHING;

COMMIT;
