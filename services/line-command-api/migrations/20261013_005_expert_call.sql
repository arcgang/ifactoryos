-- Migration: create expert_call and works_council_config
-- Records remote expert call sessions initiated from AR Workbench.
-- Works Council config drives video vs voice-only call mode selection.
-- No biometric or gaze data stored. (AC-7293efd6, AC-48bbc2a6, AC-e30375d9)

BEGIN;

CREATE TABLE IF NOT EXISTS works_council_config (
    id                          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    plant_id                    TEXT        NOT NULL UNIQUE,
    video_streaming_approved    BOOLEAN     NOT NULL DEFAULT FALSE,
    approved_by_user_id         TEXT,
    approved_at                 TIMESTAMPTZ,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE works_council_config IS 'data_residency=eu-germany-west-central classification=internal';

CREATE INDEX IF NOT EXISTS idx_works_council_config_plant_id
    ON works_council_config (plant_id);

-- Default row: MUNICH plant starts with video NOT approved (voice-only fallback active)
INSERT INTO works_council_config (plant_id, video_streaming_approved)
VALUES ('MUNICH', FALSE)
ON CONFLICT (plant_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS expert_call (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    vin                 TEXT        NOT NULL,
    station_id          TEXT        NOT NULL,
    ar_session_id       UUID        REFERENCES ar_session (id) ON DELETE SET NULL,
    call_mode           TEXT        NOT NULL CHECK (call_mode IN ('video', 'voice')),
    expert_user_id      TEXT,
    duration_seconds    INTEGER     CHECK (duration_seconds >= 0),
    status              TEXT        NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'ended')),
    idempotency_key     UUID        NOT NULL,
    audit_entry_id      UUID,
    initiated_by_user_id TEXT       NOT NULL,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at            TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_expert_call_idempotency UNIQUE (idempotency_key)
);

COMMENT ON TABLE expert_call IS 'data_residency=eu-germany-west-central classification=internal no_biometric=true no_gaze=true';

CREATE INDEX IF NOT EXISTS idx_expert_call_vin
    ON expert_call (vin);

CREATE INDEX IF NOT EXISTS idx_expert_call_ar_session_id
    ON expert_call (ar_session_id);

CREATE INDEX IF NOT EXISTS idx_expert_call_status
    ON expert_call (status);

CREATE INDEX IF NOT EXISTS idx_expert_call_started_at
    ON expert_call (started_at DESC);

COMMIT;
