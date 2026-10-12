-- ============================================================
-- hyperledger-fabric-api owned tables
-- Region: Azure Germany West Central (EU data residency hard constraint)
-- Encryption: AES-256 at rest (Azure Disk Encryption + TDE)
-- ============================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ------------------------------------------------------------
-- fabric_certificate
-- Canonical record of every Digital Birth Certificate anchored
-- to Hyperledger Fabric. This is the local mirror of the ledger
-- entry; the Fabric transaction hash is the tamper-evidence link.
-- ------------------------------------------------------------
CREATE TABLE fabric_certificate (
    certificate_id          UUID            NOT NULL DEFAULT gen_random_uuid(),
    vin                     CHAR(17)        NOT NULL,
    gate_result_snapshot    JSONB           NOT NULL,
    -- JSONB structure: {gateId, stationResults:[{stationCode,result,details}]}
    health_score            NUMERIC(5,2)    NOT NULL CHECK (health_score >= 0 AND health_score <= 100),
    validator_id            UUID            NOT NULL,
    -- BMW Entra ID subject (sub claim) of certifying Quality Gate Validator
    issued_at               TIMESTAMPTZ     NOT NULL,
    -- Timestamp of the certify action in quality-studio
    certificate_hash        CHAR(64)        NOT NULL,
    -- SHA-256 hex digest of canonical certificate payload
    fabric_transaction_id   TEXT            NOT NULL,
    -- Hyperledger Fabric transaction ID (hex)
    fabric_block_number     BIGINT          NOT NULL,
    shift_id                UUID            NULL,
    source_system           TEXT            NOT NULL DEFAULT 'quality-studio'
                                            CHECK (source_system IN ('quality-studio','legacy')),
    anchored_at             TIMESTAMPTZ     NOT NULL,
    -- Timestamp of Fabric commit
    idempotency_status      TEXT            NOT NULL DEFAULT 'original'
                                            CHECK (idempotency_status IN ('original','duplicate')),
    -- EU data residency marker
    data_region             TEXT            NOT NULL DEFAULT 'eu-germany-west-central',
    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    CONSTRAINT pk_fabric_certificate PRIMARY KEY (certificate_id),
    CONSTRAINT uq_fabric_certificate_hash UNIQUE (certificate_hash),
    CONSTRAINT uq_fabric_transaction_id UNIQUE (fabric_transaction_id)
);

COMMENT ON TABLE fabric_certificate IS
    'Local mirror of Digital Birth Certificates anchored to Hyperledger Fabric. '
    'Data classification: confidential. EU residency: Azure Germany West Central. '
    'Retention: 7 years (GDPR Article 30 + EU Battery Passport pathway).';

-- Index: VIN lookups (certificate retrieval by vehicle)
CREATE INDEX idx_fabric_certificate_vin
    ON fabric_certificate (vin);

-- Index: shift export queries (AC-fb3fae1c — shift summary PDF)
CREATE INDEX idx_fabric_certificate_shift_id
    ON fabric_certificate (shift_id)
    WHERE shift_id IS NOT NULL;

-- Index: time-range queries for shift export and A7 metric
CREATE INDEX idx_fabric_certificate_issued_at
    ON fabric_certificate (issued_at DESC);

-- Index: source_system filter for A7 metric (≥90% certs via quality-studio)
CREATE INDEX idx_fabric_certificate_source_system
    ON fabric_certificate (source_system, issued_at DESC);

-- Index: validator audit queries
CREATE INDEX idx_fabric_certificate_validator_id
    ON fabric_certificate (validator_id);

-- Composite index: shift + issued_at for shift export pagination
CREATE INDEX idx_fabric_certificate_shift_issued
    ON fabric_certificate (shift_id, issued_at DESC)
    WHERE shift_id IS NOT NULL;

-- ------------------------------------------------------------
-- fabric_anchor_attempt
-- Audit trail of every Fabric submission attempt, including
-- failures. Supports tamper-evidence audit and SLO monitoring.
-- ------------------------------------------------------------
CREATE TABLE fabric_anchor_attempt (
    attempt_id              UUID            NOT NULL DEFAULT gen_random_uuid(),
    certificate_id          UUID            NOT NULL,
    -- References fabric_certificate; may not exist yet on first attempt
    attempt_number          SMALLINT        NOT NULL DEFAULT 1 CHECK (attempt_number >= 1),
    status                  TEXT            NOT NULL
                                            CHECK (status IN ('pending','committed','failed','timeout')),
    fabric_transaction_id   TEXT            NULL,
    error_code              TEXT            NULL,
    error_message           TEXT            NULL,
    attempted_at            TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    committed_at            TIMESTAMPTZ     NULL,
    latency_ms              INTEGER         NULL,
    -- Milliseconds from submission to Fabric commit (SLO: ≤55 000 ms)
    data_region             TEXT            NOT NULL DEFAULT 'eu-germany-west-central',
    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    CONSTRAINT pk_fabric_anchor_attempt PRIMARY KEY (attempt_id)
);

COMMENT ON TABLE fabric_anchor_attempt IS
    'Audit trail of all Hyperledger Fabric submission attempts. '
    'Data classification: confidential. EU residency: Azure Germany West Central. '
    'Retention: 7 years.';

-- Index: look up all attempts for a certificate (retry audit)
CREATE INDEX idx_fabric_anchor_attempt_certificate_id
    ON fabric_anchor_attempt (certificate_id, attempt_number);

-- Index: failed/timeout attempts for alerting and SLO dashboards
CREATE INDEX idx_fabric_anchor_attempt_status
    ON fabric_anchor_attempt (status, attempted_at DESC)
    WHERE status IN ('failed','timeout');

-- Index: latency monitoring
CREATE INDEX idx_fabric_anchor_attempt_attempted_at
    ON fabric_anchor_attempt (attempted_at DESC);

-- ------------------------------------------------------------
-- Trigger: keep updated_at current on both tables
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_fabric_certificate_updated_at
    BEFORE UPDATE ON fabric_certificate
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_fabric_anchor_attempt_updated_at
    BEFORE UPDATE ON fabric_anchor_attempt
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
