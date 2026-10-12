-- Migration: create override_log table
-- Up

BEGIN;

CREATE TABLE IF NOT EXISTS override_log (
    override_id       UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id            UUID        NOT NULL,
    vin               VARCHAR(17) NOT NULL,
    original_position INTEGER     NOT NULL CHECK (original_position >= 1),
    new_position      INTEGER     NOT NULL CHECK (new_position >= 1),
    justification     TEXT        NOT NULL CHECK (char_length(justification) >= 10),
    planner_id        TEXT        NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    data_region       TEXT        NOT NULL DEFAULT 'eu-de-west-central'
);

COMMENT ON TABLE override_log IS 'data_residency: eu-de-west-central; classification: restricted; planner_id is the Entra ID subject claim (pseudonymous per GDPR)';

CREATE INDEX IF NOT EXISTS idx_override_log_created_at
    ON override_log (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_override_log_run_id_vin
    ON override_log (run_id, vin);

COMMIT;

-- Down
-- BEGIN;
-- DROP TABLE IF EXISTS override_log;
-- COMMIT;
