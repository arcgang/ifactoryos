-- ============================================================
-- vin-digital-thread-api OWNED TABLES
-- PostgreSQL / Citus — EU data residency: Azure Germany West Central
-- AES-256 at rest enforced at storage layer (Azure Disk Encryption)
-- All timestamps in UTC (TIMESTAMPTZ)
-- ============================================================

-- ------------------------------------------------------------
-- thread_event_ingest_log
-- Relational write-ahead log for every event ingested via the
-- POST /vin/{vin}/thread/events endpoint before it is written
-- to Neo4j. Provides idempotency, audit trail, and replay
-- capability if Neo4j is temporarily unavailable.
-- ------------------------------------------------------------
CREATE TABLE thread_event_ingest_log (
    event_id            UUID            NOT NULL DEFAULT gen_random_uuid(),
    idempotency_key     UUID            NOT NULL,
    vin                 CHAR(17)        NOT NULL,
    event_type          TEXT            NOT NULL,
    shop                TEXT            NOT NULL,
    station             TEXT            NOT NULL,  -- station-level only, no operator PII
    stage_number        SMALLINT        NULL,       -- 1-14 for paint prep stages, NULL otherwise
    occurred_at         TIMESTAMPTZ     NOT NULL,
    data_source         TEXT            NOT NULL,
    parameters          JSONB           NOT NULL DEFAULT '{}',
    tunnel_position_mm  NUMERIC(10,2)   NULL,
    neo4j_linked        BOOLEAN         NOT NULL DEFAULT FALSE,
    neo4j_linked_at     TIMESTAMPTZ     NULL,
    ingest_status       TEXT            NOT NULL DEFAULT 'PENDING',  -- PENDING | LINKED | FAILED
    retry_count         SMALLINT        NOT NULL DEFAULT 0,
    last_error          TEXT            NULL,
    correlation_id      UUID            NOT NULL DEFAULT gen_random_uuid(),
    -- data residency: EU only
    data_region         TEXT            NOT NULL DEFAULT 'eu-germany-west-central',
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    CONSTRAINT thread_event_ingest_log_pkey PRIMARY KEY (event_id),
    CONSTRAINT thread_event_ingest_log_idempotency_key_uq UNIQUE (idempotency_key),
    CONSTRAINT thread_event_ingest_log_stage_number_chk CHECK (stage_number IS NULL OR (stage_number >= 1 AND stage_number <= 14)),
    CONSTRAINT thread_event_ingest_log_ingest_status_chk CHECK (ingest_status IN ('PENDING', 'LINKED', 'FAILED')),
    CONSTRAINT thread_event_ingest_log_vin_chk CHECK (vin ~ '^[A-HJ-NPR-Z0-9]{17}$')
);

COMMENT ON TABLE thread_event_ingest_log IS 'Write-ahead log for all manufacturing events ingested by vin-digital-thread-api before Neo4j write. EU data residency: Azure Germany West Central. Classification: confidential.';

-- Indexes for thread_event_ingest_log
CREATE INDEX idx_teil_vin ON thread_event_ingest_log (vin);
CREATE INDEX idx_teil_vin_occurred_at ON thread_event_ingest_log (vin, occurred_at DESC);
CREATE INDEX idx_teil_ingest_status ON thread_event_ingest_log (ingest_status) WHERE ingest_status IN ('PENDING', 'FAILED');
CREATE INDEX idx_teil_neo4j_linked ON thread_event_ingest_log (neo4j_linked) WHERE neo4j_linked = FALSE;
CREATE INDEX idx_teil_event_type ON thread_event_ingest_log (event_type);
CREATE INDEX idx_teil_shop_station ON thread_event_ingest_log (shop, station);
CREATE INDEX idx_teil_occurred_at ON thread_event_ingest_log (occurred_at DESC);
CREATE INDEX idx_teil_stage_number ON thread_event_ingest_log (stage_number) WHERE stage_number IS NOT NULL;

-- ------------------------------------------------------------
-- vin_thread_completeness_cache
-- Materialised completeness metrics per VIN, refreshed after
-- each event ingestion. Drives the /completeness endpoint and
-- the completeness alert publisher without re-querying Neo4j.
-- ------------------------------------------------------------
CREATE TABLE vin_thread_completeness_cache (
    vin                         CHAR(17)        NOT NULL,
    overall_completeness_pct    NUMERIC(5,2)    NOT NULL DEFAULT 0.00,
    meets_threshold             BOOLEAN         NOT NULL DEFAULT FALSE,  -- >= 95%
    expected_sample_count       INTEGER         NOT NULL DEFAULT 0,
    actual_sample_count         INTEGER         NOT NULL DEFAULT 0,
    stage_completeness          JSONB           NOT NULL DEFAULT '[]',  -- array of StageCompletenessDetail
    replay_window_start         TIMESTAMPTZ     NULL,
    replay_window_end           TIMESTAMPTZ     NULL,
    vin_status                  TEXT            NOT NULL DEFAULT 'IN_PROGRESS',  -- IN_PROGRESS | FINAL_GATE_COMPLETE | UNKNOWN
    alert_published             BOOLEAN         NOT NULL DEFAULT FALSE,
    alert_published_at          TIMESTAMPTZ     NULL,
    -- data residency: EU only
    data_region                 TEXT            NOT NULL DEFAULT 'eu-germany-west-central',
    created_at                  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    CONSTRAINT vin_thread_completeness_cache_pkey PRIMARY KEY (vin),
    CONSTRAINT vin_thread_completeness_cache_vin_chk CHECK (vin ~ '^[A-HJ-NPR-Z0-9]{17}$'),
    CONSTRAINT vin_thread_completeness_cache_pct_chk CHECK (overall_completeness_pct >= 0 AND overall_completeness_pct <= 100),
    CONSTRAINT vin_thread_completeness_cache_status_chk CHECK (vin_status IN ('IN_PROGRESS', 'FINAL_GATE_COMPLETE', 'UNKNOWN'))
);

COMMENT ON TABLE vin_thread_completeness_cache IS 'Materialised completeness metrics per VIN refreshed on each event ingestion. Drives /completeness endpoint and completeness alert publisher. EU data residency: Azure Germany West Central. Classification: confidential.';

-- Indexes for vin_thread_completeness_cache
CREATE INDEX idx_vtcc_meets_threshold ON vin_thread_completeness_cache (meets_threshold);
CREATE INDEX idx_vtcc_vin_status ON vin_thread_completeness_cache (vin_status);
CREATE INDEX idx_vtcc_alert_published ON vin_thread_completeness_cache (alert_published) WHERE alert_published = FALSE;
CREATE INDEX idx_vtcc_updated_at ON vin_thread_completeness_cache (updated_at DESC);

-- ------------------------------------------------------------
-- thread_query_audit_log
-- Immutable record of every read query (GET /thread, /replay,
-- /snapshot, /completeness) for IBM Guardium monitoring and
-- GDPR Article 30 compliance. Append-only; no UPDATE/DELETE.
-- ------------------------------------------------------------
CREATE TABLE thread_query_audit_log (
    audit_id            UUID            NOT NULL DEFAULT gen_random_uuid(),
    vin                 CHAR(17)        NOT NULL,
    operation           TEXT            NOT NULL,  -- GET_THREAD | GET_REPLAY | GET_SNAPSHOT | GET_COMPLETENESS | INGEST_EVENT
    caller_subject      TEXT            NOT NULL,  -- JWT sub claim (user or service identity)
    caller_role         TEXT            NOT NULL,  -- JWT role claim
    correlation_id      UUID            NOT NULL,
    query_params        JSONB           NOT NULL DEFAULT '{}',
    response_status     SMALLINT        NOT NULL,
    response_event_count INTEGER        NULL,
    duration_ms         INTEGER         NULL,
    -- data residency: EU only
    data_region         TEXT            NOT NULL DEFAULT 'eu-germany-west-central',
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    CONSTRAINT thread_query_audit_log_pkey PRIMARY KEY (audit_id),
    CONSTRAINT thread_query_audit_log_operation_chk CHECK (operation IN ('GET_THREAD', 'GET_REPLAY', 'GET_SNAPSHOT', 'GET_COMPLETENESS', 'INGEST_EVENT'))
);

COMMENT ON TABLE thread_query_audit_log IS 'Append-only audit log for all read and write queries. IBM Guardium monitored. GDPR Article 30 compliance. EU data residency: Azure Germany West Central. Classification: confidential. Retention: 7 years.';

-- Indexes for thread_query_audit_log
CREATE INDEX idx_tqal_vin ON thread_query_audit_log (vin);
CREATE INDEX idx_tqal_caller_subject ON thread_query_audit_log (caller_subject);
CREATE INDEX idx_tqal_created_at ON thread_query_audit_log (created_at DESC);
CREATE INDEX idx_tqal_operation ON thread_query_audit_log (operation);
CREATE INDEX idx_tqal_correlation_id ON thread_query_audit_log (correlation_id);

-- ------------------------------------------------------------
-- updated_at trigger function (shared)
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_teil_updated_at
    BEFORE UPDATE ON thread_event_ingest_log
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_vtcc_updated_at
    BEFORE UPDATE ON vin_thread_completeness_cache
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
