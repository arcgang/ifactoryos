-- Migration: create vin_constraint_explanation table
-- Up

BEGIN;

CREATE TABLE IF NOT EXISTS vin_constraint_explanation (
    run_id      UUID        NOT NULL,
    vin         VARCHAR(17) NOT NULL CHECK (vin ~ '^[A-HJ-NPR-Z0-9]{17}$'),
    position    INTEGER     NOT NULL,
    top_factors JSONB       NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    data_region TEXT        NOT NULL DEFAULT 'eu-de-west-central',
    PRIMARY KEY (run_id, vin)
);

COMMENT ON TABLE vin_constraint_explanation IS 'data_residency: eu-de-west-central; top_factors stores up to 3 ConstraintFactor objects: [{factorName, weightedScore, dataSource, rawValue}]';

CREATE INDEX IF NOT EXISTS idx_vin_explanation_run_id
    ON vin_constraint_explanation (run_id);

COMMIT;

-- Down
-- BEGIN;
-- DROP TABLE IF EXISTS vin_constraint_explanation;
-- COMMIT;
