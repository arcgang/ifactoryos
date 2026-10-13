-- Migration: create oee_snapshot
-- OEE snapshots received from OpCenter GraphQL subscription.
-- No operator-identifiable data — station-level aggregation only.
-- Residency: Azure Germany West Central. Classification: internal.
-- Retention: 90 days hot, then purge (raw data in TimescaleDB).

BEGIN;

CREATE TABLE IF NOT EXISTS oee_snapshot (
    oee_snapshot_id     UUID            NOT NULL DEFAULT gen_random_uuid(),
    line_id             TEXT            NOT NULL,
    oee_percent         NUMERIC(5,2)    NOT NULL CHECK (oee_percent BETWEEN 0 AND 100),
    availability        NUMERIC(5,2)    NOT NULL CHECK (availability BETWEEN 0 AND 100),
    performance         NUMERIC(5,2)    NOT NULL CHECK (performance BETWEEN 0 AND 100),
    quality             NUMERIC(5,2)    NOT NULL CHECK (quality BETWEEN 0 AND 100),
    measured_at         TIMESTAMPTZ     NOT NULL,
    received_at         TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    opcenter_event_id   TEXT            NULL,
    kafka_offset        BIGINT          NULL,
    correlation_id      UUID            NOT NULL DEFAULT gen_random_uuid(),
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    CONSTRAINT pk_oee_snapshot PRIMARY KEY (oee_snapshot_id)
);

-- Query: latest snapshot per line (most recent measured_at)
CREATE INDEX IF NOT EXISTS idx_oee_snapshot_line_measured
    ON oee_snapshot (line_id, measured_at DESC);

-- Query: freshness check — snapshots newer than N seconds
CREATE INDEX IF NOT EXISTS idx_oee_snapshot_received_at
    ON oee_snapshot (received_at DESC);

-- Dedup: prevent duplicate ingestion of same OpCenter event
CREATE UNIQUE INDEX IF NOT EXISTS uq_oee_snapshot_opcenter_event_id
    ON oee_snapshot (opcenter_event_id)
    WHERE opcenter_event_id IS NOT NULL;

COMMENT ON TABLE oee_snapshot IS
    'OEE snapshots received from OpCenter GraphQL subscription. '
    'No operator-identifiable data — station-level aggregation only. '
    'Residency: Azure Germany West Central. Classification: internal. '
    'Retention: 90 days hot, then purge (raw data in TimescaleDB).';

COMMIT;
