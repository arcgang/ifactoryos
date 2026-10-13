-- Migration: 20261012_001_thread_event_ingest_log
-- Creates the idempotency log and status tracking table for VIN thread event ingestion.
-- EU data residency: Azure Germany West Central / on-prem Munich only.
-- Classification: confidential (may contain pseudonymised operator tokens).

BEGIN;

CREATE TABLE IF NOT EXISTS thread_event_ingest_log (
    event_id            UUID            NOT NULL,
    vin                 TEXT            NOT NULL,
    idempotency_key     UUID            NOT NULL,
    event_type          TEXT            NOT NULL,
    shop                TEXT            NOT NULL,
    station             TEXT            NOT NULL,
    occurred_at         TIMESTAMPTZ     NOT NULL,
    data_source         TEXT            NOT NULL,
    -- parameters stored with PII scrubbed (pseudonymous tokens only, never raw employee IDs)
    parameters          JSONB           NOT NULL DEFAULT '{}',
    stage_number        INTEGER         CHECK (stage_number BETWEEN 1 AND 14),
    tunnel_position_mm  NUMERIC(12,4),
    status              TEXT            NOT NULL DEFAULT 'PENDING'
                            CHECK (status IN ('PENDING', 'LINKED', 'FAILED')),
    linked_at           TIMESTAMPTZ,
    ingested_by         TEXT            NOT NULL,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    CONSTRAINT thread_event_ingest_log_pkey PRIMARY KEY (event_id),
    CONSTRAINT uq_thread_event_ingest_log_idempotency UNIQUE (idempotency_key)
);

COMMENT ON TABLE thread_event_ingest_log IS
    'Idempotency log and status tracking for VIN thread event ingestion. '
    'data_residency=eu-germany-west-central classification=confidential. '
    'parameters column stores only pseudonymised values — raw employee IDs are never persisted.';

CREATE INDEX IF NOT EXISTS idx_teil_vin ON thread_event_ingest_log (vin);
CREATE INDEX IF NOT EXISTS idx_teil_status ON thread_event_ingest_log (status)
    WHERE status != 'LINKED';
CREATE INDEX IF NOT EXISTS idx_teil_occurred_at ON thread_event_ingest_log (occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_teil_event_type ON thread_event_ingest_log (event_type);

COMMIT;
