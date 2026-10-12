-- ============================================================
-- opcenter-sync-service owned tables
-- All tables: AES-256 at rest (Azure disk encryption + TDE)
-- EU data residency: Azure Germany West Central (primary)
--                    Azure Germany North (DR replica)
-- No PII stored — operator IDs are in immutable-audit-log only
-- ============================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ------------------------------------------------------------
-- sync_job: tracks each sequence publish attempt to OpCenter
-- ------------------------------------------------------------
CREATE TABLE sync_job (
    sync_job_id         UUID            NOT NULL DEFAULT gen_random_uuid(),
    sequence_plan_id    UUID            NOT NULL,
    idempotency_key     UUID            NOT NULL,
    opcenter_sequence_id TEXT           NULL,
    status              TEXT            NOT NULL
        CHECK (status IN ('published','queued','retrying','failed','rejected_by_opcenter')),
    source              TEXT            NOT NULL DEFAULT 'planning-cockpit'
        CHECK (source = 'planning-cockpit'),
    planner_user_id     TEXT            NULL,
    vin_count           INTEGER         NOT NULL CHECK (vin_count >= 1),
    attempt_count       INTEGER         NOT NULL DEFAULT 0,
    last_attempt_at     TIMESTAMPTZ     NULL,
    next_retry_at       TIMESTAMPTZ     NULL,
    published_at        TIMESTAMPTZ     NULL,
    opcenter_error      TEXT            NULL,
    request_payload     JSONB           NOT NULL,
    audit_event_id      UUID            NULL,
    correlation_id      UUID            NOT NULL DEFAULT gen_random_uuid(),
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    CONSTRAINT pk_sync_job PRIMARY KEY (sync_job_id)
);

-- Unique idempotency key to prevent duplicate publishes
CREATE UNIQUE INDEX uq_sync_job_idempotency_key
    ON sync_job (idempotency_key);

-- Query: list jobs by sequence_plan_id (planner status check)
CREATE INDEX idx_sync_job_sequence_plan_id
    ON sync_job (sequence_plan_id);

-- Query: list pending/retrying jobs for retry scheduler
CREATE INDEX idx_sync_job_status_next_retry
    ON sync_job (status, next_retry_at)
    WHERE status IN ('queued', 'retrying');

-- Query: recent jobs by created_at for list endpoint cursor pagination
CREATE INDEX idx_sync_job_created_at
    ON sync_job (created_at DESC);

COMMENT ON TABLE sync_job IS
    'Tracks each VIN sequence publish attempt to Siemens OpCenter 10.2. '
    'Residency: Azure Germany West Central (primary), Germany North (DR). '
    'Classification: confidential. Retention: 13 months active, then archive.';

-- ------------------------------------------------------------
-- oee_snapshot: latest OEE values per weld line from OpCenter
-- ------------------------------------------------------------
CREATE TABLE oee_snapshot (
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
CREATE INDEX idx_oee_snapshot_line_measured
    ON oee_snapshot (line_id, measured_at DESC);

-- Query: freshness check — snapshots newer than N seconds
CREATE INDEX idx_oee_snapshot_received_at
    ON oee_snapshot (received_at DESC);

COMMENT ON TABLE oee_snapshot IS
    'OEE snapshots received from OpCenter GraphQL subscription. '
    'No operator-identifiable data — station-level aggregation only. '
    'Residency: Azure Germany West Central. Classification: internal. '
    'Retention: 90 days hot, then purge (raw data in TimescaleDB).';

-- ------------------------------------------------------------
-- andon_alert: Andon alerts received from OpCenter
-- ------------------------------------------------------------
CREATE TABLE andon_alert (
    alert_id                    UUID        NOT NULL DEFAULT gen_random_uuid(),
    opcenter_event_id           TEXT        NULL,
    station_id                  TEXT        NOT NULL,
    line_id                     TEXT        NOT NULL,
    alert_type                  TEXT        NOT NULL
        CHECK (alert_type IN ('predictive','reactive')),
    predicted_failure_type      TEXT        NOT NULL,
    minutes_to_predicted_failure INTEGER    NULL,
    recommended_action          TEXT        NOT NULL,
    status                      TEXT        NOT NULL
        CHECK (status IN ('open','acknowledged','resolved','false_positive')),
    detected_at                 TIMESTAMPTZ NOT NULL,
    received_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    acknowledged_at             TIMESTAMPTZ NULL,
    resolved_at                 TIMESTAMPTZ NULL,
    kafka_offset                BIGINT      NULL,
    correlation_id              UUID        NOT NULL DEFAULT gen_random_uuid(),
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT pk_andon_alert PRIMARY KEY (alert_id)
);

-- Query: open alerts sorted by minutes_to_predicted_failure (Command Center)
CREATE INDEX idx_andon_alert_status_minutes
    ON andon_alert (status, minutes_to_predicted_failure ASC NULLS LAST)
    WHERE status = 'open';

-- Query: alerts by line and station
CREATE INDEX idx_andon_alert_line_station
    ON andon_alert (line_id, station_id);

-- Query: recent alerts for list endpoint cursor pagination
CREATE INDEX idx_andon_alert_detected_at
    ON andon_alert (detected_at DESC);

-- Dedup: prevent duplicate ingestion of same OpCenter event
CREATE UNIQUE INDEX uq_andon_alert_opcenter_event_id
    ON andon_alert (opcenter_event_id)
    WHERE opcenter_event_id IS NOT NULL;

COMMENT ON TABLE andon_alert IS
    'Andon alerts received from OpCenter GraphQL subscription. '
    'station_id is station-level only — no operator_id stored (BetrVG §87 / GDPR). '
    'Residency: Azure Germany West Central. Classification: internal. '
    'Retention: 13 months.';

-- ------------------------------------------------------------
-- gate_result: quality gate results received from OpCenter
-- ------------------------------------------------------------
CREATE TABLE gate_result (
    gate_result_id          UUID        NOT NULL DEFAULT gen_random_uuid(),
    vin                     CHAR(17)    NOT NULL,
    gate_id                 TEXT        NOT NULL,
    gate_type               TEXT        NOT NULL
        CHECK (gate_type IN ('dimensional','leak','electrical','nvh','adas')),
    result                  TEXT        NOT NULL
        CHECK (result IN ('pass','conditional_pass','fail')),
    measurements            JSONB       NOT NULL DEFAULT '[]',
    measured_at             TIMESTAMPTZ NOT NULL,
    received_from_opcenter_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    opcenter_event_id       TEXT        NULL,
    kafka_offset            BIGINT      NULL,
    correlation_id          UUID        NOT NULL DEFAULT gen_random_uuid(),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT pk_gate_result PRIMARY KEY (gate_result_id)
);

-- Query: all gate results for a VIN (health score aggregation)
CREATE INDEX idx_gate_result_vin
    ON gate_result (vin);

-- Query: results by VIN + gate_type (dedup check)
CREATE INDEX idx_gate_result_vin_gate_type
    ON gate_result (vin, gate_type);

-- Query: recent results for list endpoint cursor pagination
CREATE INDEX idx_gate_result_received_at
    ON gate_result (received_from_opcenter_at DESC);

-- Query: conditional_pass results for anomaly highlighting
CREATE INDEX idx_gate_result_conditional_pass
    ON gate_result (vin, gate_type)
    WHERE result = 'conditional_pass';

-- Dedup: prevent duplicate ingestion of same OpCenter event
CREATE UNIQUE INDEX uq_gate_result_opcenter_event_id
    ON gate_result (opcenter_event_id)
    WHERE opcenter_event_id IS NOT NULL;

COMMENT ON TABLE gate_result IS
    'Quality gate results received from OpCenter GraphQL subscription. '
    'measurements JSONB contains array of GateMeasurement objects. '
    'Residency: Azure Germany West Central. Classification: confidential. '
    'Retention: 7 years (regulatory quality record).';

-- ------------------------------------------------------------
-- opcenter_api_key_rotation: tracks API key rotation schedule
-- (90-day max per EA-SEC-002)
-- ------------------------------------------------------------
CREATE TABLE opcenter_api_key_rotation (
    rotation_id         UUID        NOT NULL DEFAULT gen_random_uuid(),
    key_name            TEXT        NOT NULL,
    vault_path          TEXT        NOT NULL,
    issued_at           TIMESTAMPTZ NOT NULL,
    expires_at          TIMESTAMPTZ NOT NULL,
    rotated_at          TIMESTAMPTZ NULL,
    status              TEXT        NOT NULL
        CHECK (status IN ('active','rotated','expired')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT pk_opcenter_api_key_rotation PRIMARY KEY (rotation_id)
);

-- Query: active key and upcoming expiry
CREATE INDEX idx_opcenter_api_key_rotation_status_expires
    ON opcenter_api_key_rotation (status, expires_at)
    WHERE status = 'active';

COMMENT ON TABLE opcenter_api_key_rotation IS
    'Tracks OpCenter API key rotation schedule. '
    'Actual key stored in HashiCorp Vault — only metadata here. '
    'Residency: Azure Germany West Central. Classification: confidential.';
