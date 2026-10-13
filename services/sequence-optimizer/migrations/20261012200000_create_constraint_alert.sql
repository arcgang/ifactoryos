-- Migration: create constraint_alert table
-- Up

BEGIN;

CREATE TABLE IF NOT EXISTS constraint_alert (
    alert_id            UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    constraint_type     constraint_type NOT NULL,
    current_value       NUMERIC(18,6)   NOT NULL,
    threshold_value     NUMERIC(18,6)   NOT NULL,
    affected_vins       JSONB           NOT NULL DEFAULT '[]',
    status              TEXT            NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active','acknowledged','resolved')),
    detected_at         TIMESTAMPTZ     NOT NULL DEFAULT now(),
    acknowledged_at     TIMESTAMPTZ,
    acknowledged_by     TEXT,
    resolved_at         TIMESTAMPTZ,
    data_region         TEXT            NOT NULL DEFAULT 'eu-de-west-central',
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT now()
);

COMMENT ON TABLE constraint_alert IS 'data_residency: Azure Germany West Central; classification: internal';

CREATE INDEX IF NOT EXISTS idx_alert_status
    ON constraint_alert (status);

CREATE INDEX IF NOT EXISTS idx_alert_detected_at
    ON constraint_alert (detected_at DESC);

CREATE INDEX IF NOT EXISTS idx_alert_type
    ON constraint_alert (constraint_type);

COMMIT;

-- Down
-- BEGIN;
-- DROP TABLE IF EXISTS constraint_alert;
-- COMMIT;
