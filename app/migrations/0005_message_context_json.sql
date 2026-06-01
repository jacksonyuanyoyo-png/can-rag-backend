-- Message-level citation / sources payloads for API parity (camelCase JSON in app layer).

ALTER TABLE app.t_fact_message
    ADD COLUMN IF NOT EXISTS citations_json jsonb;

ALTER TABLE app.t_fact_message
    ADD COLUMN IF NOT EXISTS sources_json jsonb;
