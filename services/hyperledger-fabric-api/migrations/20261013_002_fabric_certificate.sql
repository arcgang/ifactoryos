-- Migration: 20261013_002_fabric_certificate
-- Mirrors every anchored Digital Birth Certificate for queryable operational use.
-- The Fabric ledger is the tamper-evident anchor; this table serves list/filter/export.

BEGIN;

CREATE TABLE IF NOT EXISTS fabric_certificate (
    certificate_id       UUID              PRIMARY KEY,
    vin                  VARCHAR(17)       NOT NULL,
    gate_result_snapshot JSONB             NOT NULL,
    health_score         DOUBLE PRECISION  NOT NULL,
    validator_id         UUID              NOT NULL,
    issued_at            TIMESTAMPTZ       NOT NULL,
    certificate_hash     CHAR(64)          NOT NULL,
    fabric_transaction_id TEXT             NOT NULL,
    fabric_block_number  BIGINT            NOT NULL,
    anchored_at          TIMESTAMPTZ       NOT NULL,
    source_system        VARCHAR(20)       NOT NULL DEFAULT 'quality-studio',
    shift_id             UUID,
    idempotent           BOOLEAN           NOT NULL DEFAULT FALSE
);

-- Shift export: filter by shift_id with recency ordering
CREATE INDEX IF NOT EXISTS ix_fabric_certificate_shift_issued
    ON fabric_certificate (shift_id, issued_at DESC);

-- VIN history: filter by VIN with recency ordering
CREATE INDEX IF NOT EXISTS ix_fabric_certificate_vin_issued
    ON fabric_certificate (vin, issued_at DESC);

-- Keyset pagination primary sort column
CREATE INDEX IF NOT EXISTS ix_fabric_certificate_issued_at
    ON fabric_certificate (issued_at DESC);

COMMIT;
