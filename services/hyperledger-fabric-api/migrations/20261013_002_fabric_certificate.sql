-- Migration: 20261013_002_fabric_certificate
-- Mirrors every anchored Digital Birth Certificate for queryable operational use.
-- The Fabric ledger is the tamper-evident anchor; this table serves list/filter/export.
-- Retained 7 years per EU Battery Passport compliance and immutable-audit-log policy.
-- sourceSystem column enables A7 metric (≥90% certs via quality-studio).

BEGIN;

CREATE TABLE IF NOT EXISTS fabric_certificate (
    certificate_id          UUID            PRIMARY KEY,
    vin                     TEXT            NOT NULL,
    fabric_transaction_id   TEXT            NOT NULL,
    fabric_block_number     BIGINT          NOT NULL,
    anchored_at             TIMESTAMPTZ     NOT NULL,
    certificate_hash        TEXT            NOT NULL,
    validator_id            UUID            NOT NULL,
    health_score            NUMERIC(6,3)    NOT NULL,
    gate_result_snapshot    JSONB           NOT NULL,
    source_system           TEXT            NOT NULL DEFAULT 'quality-studio'
                                                CHECK (source_system IN ('quality-studio', 'legacy')),
    shift_id                UUID,
    issued_at               TIMESTAMPTZ     NOT NULL,
    status                  TEXT            NOT NULL DEFAULT 'committed'
                                                CHECK (status IN ('committed')),
    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_fabric_certificate_tx UNIQUE (fabric_transaction_id)
);

COMMENT ON TABLE fabric_certificate IS 'data_residency=eu-germany-west-central classification=confidential retain=7y';

-- Shift export: filter by shift_id with recency ordering
CREATE INDEX IF NOT EXISTS idx_fabric_certificate_vin
    ON fabric_certificate (vin);

CREATE INDEX IF NOT EXISTS idx_fabric_certificate_shift_id
    ON fabric_certificate (shift_id) WHERE shift_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_fabric_certificate_anchored_at
    ON fabric_certificate (anchored_at DESC);

CREATE INDEX IF NOT EXISTS idx_fabric_certificate_source_system
    ON fabric_certificate (source_system);

CREATE INDEX IF NOT EXISTS idx_fabric_certificate_validator_id
    ON fabric_certificate (validator_id);

COMMIT;
