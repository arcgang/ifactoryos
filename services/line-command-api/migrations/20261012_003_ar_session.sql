-- Migration: create ar_session
-- Records AR Workbench session starts for adoption instrumentation
-- (AC-9c6cd996, AC-fd1f00c6). No biometric or gaze data stored.

BEGIN;

CREATE TABLE IF NOT EXISTS ar_session (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    vin             TEXT        NOT NULL,
    station_id      TEXT        NOT NULL,
    device_type     TEXT        NOT NULL CHECK (device_type IN ('HoloLens2', 'iPad')),
    user_id         TEXT        NOT NULL,
    idempotency_key UUID        NOT NULL,
    event_id        UUID        NOT NULL DEFAULT gen_random_uuid(),
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_ar_session_idempotency UNIQUE (idempotency_key)
);

COMMENT ON TABLE ar_session IS 'data_residency=eu-germany-west-central classification=internal no_biometric=true';

CREATE INDEX IF NOT EXISTS idx_ar_session_vin
    ON ar_session (vin);

CREATE INDEX IF NOT EXISTS idx_ar_session_user_id
    ON ar_session (user_id);

CREATE INDEX IF NOT EXISTS idx_ar_session_station_id
    ON ar_session (station_id);

CREATE INDEX IF NOT EXISTS idx_ar_session_started_at
    ON ar_session (started_at DESC);

COMMIT;
