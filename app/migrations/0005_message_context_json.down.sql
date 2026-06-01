ALTER TABLE app.t_fact_message
    DROP COLUMN IF EXISTS sources_json;

ALTER TABLE app.t_fact_message
    DROP COLUMN IF EXISTS citations_json;
