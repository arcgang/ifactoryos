-- Migration: 20261012_002_vin_thread_completeness_cache
-- Materialised completeness metrics per VIN, refreshed after each event ingestion.
-- Drives the completeness alert publisher and the /completeness endpoint.
-- EU data residency: Azure Germany West Central / on-prem Munich.
-- Classification: confidential.

BEGIN;

CREATE TABLE IF NOT EXISTS vin_thread_completeness_cache (
    vin                         CHAR(17)        NOT NULL,
    overall_completeness_pct    NUMERIC(5,2)    NOT NULL DEFAULT 0.00,
    meets_threshold             BOOLEAN         NOT NULL DEFAULT FALSE,  -- >= 95%
    expected_sample_count       INTEGER         NOT NULL DEFAULT 0,
    actual_sample_count         INTEGER         NOT NULL DEFAULT 0,
    stage_completeness          JSONB           NOT NULL DEFAULT '[]',
    replay_window_start         TIMESTAMPTZ,
    replay_window_end           TIMESTAMPTZ,
    vin_status                  TEXT            NOT NULL DEFAULT 'IN_PROGRESS'
                                    CHECK (vin_status IN ('IN_PROGRESS', 'FINAL_GATE_COMPLETE', 'UNKNOWN')),
    alert_published             BOOLEAN         NOT NULL DEFAULT FALSE,
    alert_published_at          TIMESTAMPTZ,
    data_region                 TEXT            NOT NULL DEFAULT 'eu-germany-west-central',
    created_at                  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    CONSTRAINT vin_thread_completeness_cache_pkey PRIMARY KEY (vin),
    CONSTRAINT vin_thread_completeness_cache_vin_chk
        CHECK (vin ~ '^[A-HJ-NPR-Z0-9]{17}$'),
    CONSTRAINT vin_thread_completeness_cache_pct_chk
        CHECK (overall_completeness_pct >= 0 AND overall_completeness_pct <= 100),
    CONSTRAINT vin_thread_completeness_cache_status_chk
        CHECK (vin_status IN ('IN_PROGRESS', 'FINAL_GATE_COMPLETE', 'UNKNOWN'))
);

COMMENT ON TABLE vin_thread_completeness_cache IS
    'Materialised completeness metrics per VIN refreshed on each event ingestion. '
    'Drives /completeness endpoint and completeness alert publisher. '
    'EU data residency: Azure Germany West Central. Classification: confidential.';

CREATE INDEX IF NOT EXISTS idx_vtcc_meets_threshold
    ON vin_thread_completeness_cache (meets_threshold);
CREATE INDEX IF NOT EXISTS idx_vtcc_vin_status
    ON vin_thread_completeness_cache (vin_status);
CREATE INDEX IF NOT EXISTS idx_vtcc_alert_published
    ON vin_thread_completeness_cache (alert_published)
    WHERE alert_published = FALSE;
CREATE INDEX IF NOT EXISTS idx_vtcc_updated_at
    ON vin_thread_completeness_cache (updated_at DESC);

COMMIT;
