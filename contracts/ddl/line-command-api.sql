-- ============================================================
-- line-command-api owned tables
-- All tables reside in Azure Germany West Central (EU data residency)
-- AES-256 encryption at rest enforced at storage layer
-- ============================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ------------------------------------------------------------
-- ar_instruction_cache
-- Caches resolved VIN instruction payloads for fast AR overlay
-- serving. TTL-expired rows cleaned by background job.
-- ------------------------------------------------------------
CREATE TABLE ar_instruction_cache (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    vin                 TEXT        NOT NULL,
    station_id          TEXT        NOT NULL,
    option_codes        TEXT[]      NOT NULL DEFAULT '{}',
    variant_code        TEXT        NOT NULL,
    is_rare_variant     BOOLEAN     NOT NULL DEFAULT FALSE,
    instruction_version TEXT        NOT NULL,
    payload_jsonb       JSONB       NOT NULL,
    fallback_pdf_qr_url TEXT        NOT NULL,
    fetched_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at          TIMESTAMPTZ NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- data residency: EU (Azure Germany West Central)
COMMENT ON TABLE ar_instruction_cache IS 'data_residency=eu-germany-west-central classification=internal';

CREATE INDEX idx_ar_instruction_cache_vin ON ar_instruction_cache (vin);
CREATE INDEX idx_ar_instruction_cache_vin_station ON ar_instruction_cache (vin, station_id);
CREATE INDEX idx_ar_instruction_cache_expires_at ON ar_instruction_cache (expires_at);

-- ------------------------------------------------------------
-- ar_instruction_failure_log
-- Immutable log of AR overlay load failures for audit trail
-- (AC-775df01c). Append-only; no UPDATE/DELETE.
-- ------------------------------------------------------------
CREATE TABLE ar_instruction_failure_log (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    vin             TEXT        NOT NULL,
    station_id      TEXT        NOT NULL,
    device_type     TEXT        NOT NULL CHECK (device_type IN ('HoloLens2', 'iPad')),
    failure_reason  TEXT        NOT NULL,
    correlation_id  UUID        NOT NULL,
    failed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE ar_instruction_failure_log IS 'data_residency=eu-germany-west-central classification=internal append_only=true';

CREATE INDEX idx_ar_failure_log_vin ON ar_instruction_failure_log (vin);
CREATE INDEX idx_ar_failure_log_failed_at ON ar_instruction_failure_log (failed_at DESC);

-- ------------------------------------------------------------
-- ar_session
-- Records AR Workbench session starts for adoption instrumentation
-- (AC-9c6cd996, AC-fd1f00c6). No biometric or gaze data.
-- ------------------------------------------------------------
CREATE TABLE ar_session (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    vin             TEXT        NOT NULL,
    station_id      TEXT        NOT NULL,
    device_type     TEXT        NOT NULL CHECK (device_type IN ('HoloLens2', 'iPad')),
    user_id         TEXT        NOT NULL,
    idempotency_key UUID        NOT NULL,
    event_id        UUID        NOT NULL DEFAULT gen_random_uuid(),
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_ar_session_idempotency UNIQUE (idempotency_key)
);
COMMENT ON TABLE ar_session IS 'data_residency=eu-germany-west-central classification=internal no_biometric=true';

CREATE INDEX idx_ar_session_vin ON ar_session (vin);
CREATE INDEX idx_ar_session_user_id ON ar_session (user_id);
CREATE INDEX idx_ar_session_station_id ON ar_session (station_id);
CREATE INDEX idx_ar_session_started_at ON ar_session (started_at DESC);

-- ------------------------------------------------------------
-- expert_call
-- Records remote expert call sessions initiated from AR Workbench
-- (AC-7293efd6, AC-48bbc2a6, AC-e30375d9). No biometric/gaze data.
-- ------------------------------------------------------------
CREATE TABLE expert_call (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    vin                 TEXT        NOT NULL,
    station_id          TEXT        NOT NULL,
    ar_session_id       UUID        REFERENCES ar_session (id),
    call_mode           TEXT        NOT NULL CHECK (call_mode IN ('video', 'voice')),
    expert_user_id      TEXT,
    duration_seconds    INTEGER     CHECK (duration_seconds >= 0),
    status              TEXT        NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'ended')),
    idempotency_key     UUID        NOT NULL,
    audit_entry_id      UUID,
    initiated_by_user_id TEXT       NOT NULL,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at            TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_expert_call_idempotency UNIQUE (idempotency_key)
);
COMMENT ON TABLE expert_call IS 'data_residency=eu-germany-west-central classification=internal no_biometric=true no_gaze=true';

CREATE INDEX idx_expert_call_vin ON expert_call (vin);
CREATE INDEX idx_expert_call_ar_session_id ON expert_call (ar_session_id);
CREATE INDEX idx_expert_call_status ON expert_call (status);
CREATE INDEX idx_expert_call_started_at ON expert_call (started_at DESC);

-- ------------------------------------------------------------
-- torque_log
-- Torque tightening records from Beckhoff PLC via edge gateway.
-- Idempotency enforced via idempotency_key unique constraint.
-- (AC-13685a24, AC-69c77dfd, AC-b734032a)
-- ------------------------------------------------------------
CREATE TABLE torque_log (
    id                  UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    vin                 TEXT            NOT NULL,
    station_id          TEXT            NOT NULL,
    fastener_point_id   TEXT            NOT NULL,
    torque_nm           NUMERIC(10,4)   NOT NULL,
    angles_deg          NUMERIC(10,4)   NOT NULL,
    spec_nm             NUMERIC(10,4),
    tolerance_nm        NUMERIC(10,4),
    in_tolerance        BOOLEAN,
    trace_data          JSONB,
    plc_timestamp       TIMESTAMPTZ     NOT NULL,
    uns_publish_status  TEXT            NOT NULL DEFAULT 'queued' CHECK (uns_publish_status IN ('published', 'queued', 'failed')),
    uns_published_at    TIMESTAMPTZ,
    edge_buffered       BOOLEAN         NOT NULL DEFAULT FALSE,
    idempotency_key     UUID            NOT NULL,
    alert_id            UUID,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_torque_log_idempotency UNIQUE (idempotency_key)
);
COMMENT ON TABLE torque_log IS 'data_residency=eu-germany-west-central classification=confidential';

CREATE INDEX idx_torque_log_vin ON torque_log (vin);
CREATE INDEX idx_torque_log_station_id ON torque_log (station_id);
CREATE INDEX idx_torque_log_vin_fastener ON torque_log (vin, fastener_point_id);
CREATE INDEX idx_torque_log_plc_timestamp ON torque_log (plc_timestamp DESC);
CREATE INDEX idx_torque_log_uns_publish_status ON torque_log (uns_publish_status) WHERE uns_publish_status != 'published';
CREATE INDEX idx_torque_log_in_tolerance ON torque_log (in_tolerance) WHERE in_tolerance = FALSE;

-- ------------------------------------------------------------
-- andon_alert
-- Predictive Andon alerts consumed from UNS / opcenter-sync-service.
-- Shift Leads acknowledge and disposition alerts here.
-- (AC-e37bcf34, AC-fd1f00c6)
-- ------------------------------------------------------------
CREATE TABLE andon_alert (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    external_alert_id       UUID        NOT NULL,
    shift_id                UUID,
    line_id                 TEXT        NOT NULL,
    station_id              TEXT        NOT NULL,
    alert_type              TEXT        NOT NULL,
    status                  TEXT        NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved', 'escalated', 'monitoring')),
    predicted_failure_ts    TIMESTAMPTZ NOT NULL,
    disposition             TEXT        CHECK (disposition IN ('resolved', 'escalated', 'monitoring')),
    disposition_note        TEXT,
    acknowledged_by_user_id TEXT,
    acknowledged_at         TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_andon_alert_external_id UNIQUE (external_alert_id)
);
COMMENT ON TABLE andon_alert IS 'data_residency=eu-germany-west-central classification=internal';

CREATE INDEX idx_andon_alert_shift_id ON andon_alert (shift_id);
CREATE INDEX idx_andon_alert_line_id ON andon_alert (line_id);
CREATE INDEX idx_andon_alert_status ON andon_alert (status);
CREATE INDEX idx_andon_alert_predicted_failure_ts ON andon_alert (predicted_failure_ts DESC);
CREATE INDEX idx_andon_alert_created_at ON andon_alert (created_at DESC);

-- ------------------------------------------------------------
-- shift_handover
-- Structured digital shift handover records.
-- (AC-e37bcf34, AC-5f4b73cb, AC-04cd3711)
-- Stored in postgresql-citus shifthandover table as well;
-- this table is the line-command-api write-side owner.
-- ------------------------------------------------------------
CREATE TABLE shift_handover (
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

CREATE INDEX idx_shift_handover_shift_id ON shift_handover (shift_id);
CREATE INDEX idx_shift_handover_line_id ON shift_handover (line_id);
CREATE INDEX idx_shift_handover_submitted_by ON shift_handover (submitted_by_user_id);
CREATE INDEX idx_shift_handover_shift_end_ts ON shift_handover (shift_end_ts DESC);

-- ------------------------------------------------------------
-- works_council_config
-- Stores Works Council approval flags (e.g. video streaming
-- approval). Single-row config per plant. Drives call_mode
-- selection in expert_call. (AC-7293efd6, AC-48bbc2a6)
-- ------------------------------------------------------------
CREATE TABLE works_council_config (
    id                          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    plant_id                    TEXT        NOT NULL UNIQUE,
    video_streaming_approved    BOOLEAN     NOT NULL DEFAULT FALSE,
    approved_by_user_id         TEXT,
    approved_at                 TIMESTAMPTZ,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE works_council_config IS 'data_residency=eu-germany-west-central classification=internal';

CREATE INDEX idx_works_council_config_plant_id ON works_council_config (plant_id);
