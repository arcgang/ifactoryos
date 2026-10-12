-- Migration: create ar_instruction_failure_log
-- Immutable append-only log of AR overlay load failures (AC-775df01c).
-- No UPDATE or DELETE is permitted on this table.

BEGIN;

CREATE TABLE IF NOT EXISTS ar_instruction_failure_log (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    vin             TEXT        NOT NULL,
    station_id      TEXT        NOT NULL,
    device_type     TEXT        NOT NULL CHECK (device_type IN ('HoloLens2', 'iPad')),
    failure_reason  TEXT        NOT NULL,
    correlation_id  UUID        NOT NULL,
    failed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE ar_instruction_failure_log IS 'data_residency=eu-germany-west-central classification=internal append_only=true';

CREATE INDEX IF NOT EXISTS idx_ar_failure_log_vin
    ON ar_instruction_failure_log (vin);

CREATE INDEX IF NOT EXISTS idx_ar_failure_log_failed_at
    ON ar_instruction_failure_log (failed_at DESC);

COMMIT;
