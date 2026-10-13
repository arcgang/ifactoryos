-- Migration: 20261013_001_fabric_anchor_attempt
-- Records every Fabric submission attempt for SLO monitoring and the immutable audit trail.
-- status CHECK enforces the three-state machine: pending → committed | timeout.

BEGIN;

CREATE TABLE IF NOT EXISTS fabric_anchor_attempt (
    attempt_id       UUID         PRIMARY KEY,
    certificate_id   UUID         NOT NULL,
    status           VARCHAR(20)  NOT NULL
        CHECK (status IN ('pending', 'committed', 'timeout')),
    started_at       TIMESTAMPTZ  NOT NULL,
    completed_at     TIMESTAMPTZ,
    latency_ms       INTEGER
);

-- Index to look up attempts by certificate quickly (e.g. SLO dashboard, audit)
CREATE INDEX IF NOT EXISTS ix_fabric_anchor_attempt_certificate_id
    ON fabric_anchor_attempt (certificate_id);

COMMIT;
