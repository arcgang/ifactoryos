-- Migration: 20261013_001_fabric_anchor_attempt
-- Records every Fabric submission attempt for SLO monitoring and the immutable audit trail.
-- status CHECK enforces the four-state machine: pending → committed | failed | timeout.

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

-- Index to look up attempts by certificate quickly (e.g. SLO dashboard, audit)
CREATE INDEX IF NOT EXISTS idx_fabric_anchor_attempt_certificate_id
    ON fabric_anchor_attempt (certificate_id);

CREATE INDEX IF NOT EXISTS idx_fabric_anchor_attempt_status
    ON fabric_anchor_attempt (status) WHERE status != 'committed';

CREATE INDEX IF NOT EXISTS idx_fabric_anchor_attempt_created_at
    ON fabric_anchor_attempt (created_at DESC);

COMMIT;
