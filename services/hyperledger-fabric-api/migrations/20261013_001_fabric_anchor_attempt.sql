-- Migration: create fabric_anchor_attempt
-- Tracks every Fabric transaction submission attempt for SLO monitoring and audit.
-- Supports status: pending / committed / failed / timeout (AC: fabric-anchor-attempt-audit-table)

BEGIN;

CREATE TABLE IF NOT EXISTS fabric_anchor_attempt (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    certificate_id  TEXT        NOT NULL,
    status          TEXT        NOT NULL DEFAULT 'pending'
                                    CHECK (status IN ('pending', 'committed', 'failed', 'timeout')),
    latency_ms      INTEGER,
    error_detail    TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE fabric_anchor_attempt IS 'data_residency=eu-germany-west-central classification=confidential';

CREATE INDEX IF NOT EXISTS idx_fabric_anchor_attempt_certificate_id
    ON fabric_anchor_attempt (certificate_id);

CREATE INDEX IF NOT EXISTS idx_fabric_anchor_attempt_status
    ON fabric_anchor_attempt (status) WHERE status != 'committed';

CREATE INDEX IF NOT EXISTS idx_fabric_anchor_attempt_created_at
    ON fabric_anchor_attempt (created_at DESC);

COMMIT;
