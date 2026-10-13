-- Migration: create shift_handover
-- Structured digital shift handover records.
-- (AC-e37bcf34, AC-5f4b73cb, AC-04cd3711)
-- line-command-api is the write-side owner; postgresql-citus shifthandover
-- table is the read-side replica.

BEGIN;

CREATE TABLE IF NOT EXISTS shift_handover (
    id                      UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    shift_id                UUID            NOT NULL,
    line_id                 TEXT            NOT NULL,
    shift_start_ts          TIMESTAMPTZ     NOT NULL,
    shift_end_ts            TIMESTAMPTZ     NOT NULL,
    submitted_by_user_id    TEXT            NOT NULL,
    alert_dispositions      JSONB           NOT NULL DEFAULT '[]',
    oee_delta_pct           NUMERIC(6,3)    NOT NULL,
    held_vins               TEXT[]          NOT NULL DEFAULT '{}',
    notes                   TEXT,
    idempotency_key         UUID            NOT NULL,
    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_shift_handover_idempotency UNIQUE (idempotency_key),
    CONSTRAINT uq_shift_handover_shift_line UNIQUE (shift_id, line_id)
);

COMMENT ON TABLE shift_handover IS 'data_residency=eu-germany-west-central classification=internal';

CREATE INDEX IF NOT EXISTS idx_shift_handover_shift_id
    ON shift_handover (shift_id);

CREATE INDEX IF NOT EXISTS idx_shift_handover_line_id
    ON shift_handover (line_id);

CREATE INDEX IF NOT EXISTS idx_shift_handover_submitted_by
    ON shift_handover (submitted_by_user_id);

CREATE INDEX IF NOT EXISTS idx_shift_handover_shift_end_ts
    ON shift_handover (shift_end_ts DESC);

COMMIT;
