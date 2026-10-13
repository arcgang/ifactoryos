-- Migration: create optimizer_run table
-- Up

BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'optimizer_result_type') THEN
        CREATE TYPE optimizer_result_type AS ENUM (
            'monte_carlo',
            'heuristic_fallback'
        );
    END IF;
END$$;

CREATE TABLE IF NOT EXISTS optimizer_run (
    run_id                       UUID                  PRIMARY KEY DEFAULT gen_random_uuid(),
    week_iso                     TEXT                  NOT NULL,
    result_type                  optimizer_result_type NOT NULL,
    ranked_sequence              JSONB                 NOT NULL,
    confidence_score             DOUBLE PRECISION      NOT NULL CHECK (confidence_score >= 0 AND confidence_score <= 1),
    duration_ms                  INTEGER               NOT NULL,
    my26_config_version          UUID,
    constraint_snapshot          JSONB                 NOT NULL,
    idempotency_key              UUID                  UNIQUE,
    completed_at                 TIMESTAMPTZ           NOT NULL DEFAULT now(),
    created_at                   TIMESTAMPTZ           NOT NULL DEFAULT now(),
    data_region                  TEXT                  NOT NULL DEFAULT 'eu-de-west-central'
);

COMMENT ON TABLE optimizer_run IS 'data_residency: eu-de-west-central';

CREATE INDEX IF NOT EXISTS idx_optimizer_run_week_iso
    ON optimizer_run (week_iso);

CREATE INDEX IF NOT EXISTS idx_optimizer_run_idempotency_key
    ON optimizer_run (idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_optimizer_run_completed_at
    ON optimizer_run (completed_at DESC);

COMMIT;

-- Down
-- BEGIN;
-- DROP TABLE IF EXISTS optimizer_run;
-- DROP TYPE IF EXISTS optimizer_result_type;
-- COMMIT;
