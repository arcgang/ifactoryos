-- ============================================================
-- sequence-optimizer owned tables
-- All tables reside in Azure Germany West Central (EU data residency hard constraint)
-- AES-256 encryption at rest enforced at storage layer
-- ============================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ------------------------------------------------------------
-- optimizer_run
-- Stores each Monte Carlo or heuristic optimisation run
-- ------------------------------------------------------------
CREATE TABLE optimizer_run (
    run_id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    week_iso            TEXT            NOT NULL,                          -- e.g. '2025-W30'
    result_type         TEXT            NOT NULL CHECK (result_type IN ('monte_carlo','heuristic_fallback')),
    status              TEXT            NOT NULL DEFAULT 'pending'
                                            CHECK (status IN ('pending','running','completed','failed','timed_out')),
    confidence_score    NUMERIC(5,4),
    duration_ms         INTEGER,
    monte_carlo_runs    INTEGER         NOT NULL DEFAULT 50,
    my26_config_id      UUID,                                              -- FK to my26_config
    constraint_snapshot JSONB           NOT NULL,                          -- serialised ConstraintSnapshot
    idempotency_key     UUID            UNIQUE,
    initiated_by        TEXT            NOT NULL,                          -- Entra ID subject
    -- data residency: EU only
    data_region         TEXT            NOT NULL DEFAULT 'eu-de-west-central',
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT now()
);

COMMENT ON TABLE optimizer_run IS 'data_residency: Azure Germany West Central; classification: confidential';

CREATE INDEX idx_optimizer_run_week_iso        ON optimizer_run (week_iso);
CREATE INDEX idx_optimizer_run_status          ON optimizer_run (status);
CREATE INDEX idx_optimizer_run_my26_config_id  ON optimizer_run (my26_config_id);
CREATE INDEX idx_optimizer_run_created_at      ON optimizer_run (created_at DESC);

-- ------------------------------------------------------------
-- optimizer_run_vin
-- Per-VIN ranked output for each optimisation run
-- ------------------------------------------------------------
CREATE TABLE optimizer_run_vin (
    id                  UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id              UUID            NOT NULL REFERENCES optimizer_run (run_id) ON DELETE CASCADE,
    vin                 TEXT            NOT NULL CHECK (vin ~ '^[A-HJ-NPR-Z0-9]{17}$'),
    position            INTEGER         NOT NULL CHECK (position >= 1),
    constraint_score    NUMERIC(12,6)   NOT NULL,
    vip_priority        BOOLEAN         NOT NULL DEFAULT FALSE,
    regulatory_hold     BOOLEAN         NOT NULL DEFAULT FALSE,
    model_year          TEXT            CHECK (model_year IN ('MY25','MY26')),
    option_codes        JSONB,
    paint_code          TEXT,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT now(),
    UNIQUE (run_id, vin),
    UNIQUE (run_id, position)
);

COMMENT ON TABLE optimizer_run_vin IS 'data_residency: Azure Germany West Central; classification: confidential';

CREATE INDEX idx_run_vin_run_id   ON optimizer_run_vin (run_id);
CREATE INDEX idx_run_vin_vin      ON optimizer_run_vin (vin);
CREATE INDEX idx_run_vin_position ON optimizer_run_vin (run_id, position);

-- ------------------------------------------------------------
-- vin_constraint_explanation
-- Top-3 constraint factors per VIN per run (explainability)
-- ------------------------------------------------------------
CREATE TABLE vin_constraint_explanation (
    id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          UUID            NOT NULL REFERENCES optimizer_run (run_id) ON DELETE CASCADE,
    vin             TEXT            NOT NULL CHECK (vin ~ '^[A-HJ-NPR-Z0-9]{17}$'),
    factor_rank     INTEGER         NOT NULL CHECK (factor_rank BETWEEN 1 AND 3),
    factor_name     TEXT            NOT NULL CHECK (factor_name IN (
                        'paint_batch_conflict','changeover_penalty',
                        'maintenance_window','kitting_gap',
                        'vip_priority','regulatory_hold')),
    weighted_score  NUMERIC(12,6)   NOT NULL,
    raw_value       NUMERIC(18,6),
    data_source     TEXT            NOT NULL,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    UNIQUE (run_id, vin, factor_rank)
);

COMMENT ON TABLE vin_constraint_explanation IS 'data_residency: Azure Germany West Central; classification: confidential';

CREATE INDEX idx_vce_run_id  ON vin_constraint_explanation (run_id);
CREATE INDEX idx_vce_vin     ON vin_constraint_explanation (vin);
CREATE INDEX idx_vce_run_vin ON vin_constraint_explanation (run_id, vin);

-- ------------------------------------------------------------
-- sequence_override
-- Manual planner overrides with justification (Override Log)
-- ------------------------------------------------------------
CREATE TABLE sequence_override (
    override_id         UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id              UUID            NOT NULL REFERENCES optimizer_run (run_id),
    vin                 TEXT            NOT NULL CHECK (vin ~ '^[A-HJ-NPR-Z0-9]{17}$'),
    original_position   INTEGER         NOT NULL CHECK (original_position >= 1),
    new_position        INTEGER         NOT NULL CHECK (new_position >= 1),
    justification       TEXT            NOT NULL CHECK (char_length(justification) >= 10),
    planner_id          TEXT            NOT NULL,                          -- Entra ID subject; no PII stored
    week_iso            TEXT            NOT NULL,
    -- data residency: EU only
    data_region         TEXT            NOT NULL DEFAULT 'eu-de-west-central',
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT now()
);

COMMENT ON TABLE sequence_override IS 'data_residency: Azure Germany West Central; classification: confidential; GDPR: planner_id is Entra subject (pseudonymous, not direct PII)';

CREATE INDEX idx_override_run_id   ON sequence_override (run_id);
CREATE INDEX idx_override_vin      ON sequence_override (vin);
CREATE INDEX idx_override_week_iso ON sequence_override (week_iso);
CREATE INDEX idx_override_planner  ON sequence_override (planner_id);
CREATE INDEX idx_override_created  ON sequence_override (created_at DESC);

-- ------------------------------------------------------------
-- constraint_threshold
-- Configurable warning/critical thresholds per constraint type
-- ------------------------------------------------------------
CREATE TABLE constraint_threshold (
    threshold_id        UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    constraint_type     TEXT            NOT NULL UNIQUE CHECK (constraint_type IN (
                            'paint_capacity','body_wip_queue','kitting_readiness',
                            'maintenance_window','agv_delay','press_die_overrun')),
    warning_level       NUMERIC(18,6)   NOT NULL,
    critical_level      NUMERIC(18,6)   NOT NULL,
    unit                TEXT            NOT NULL,
    updated_by          TEXT            NOT NULL,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT now()
);

COMMENT ON TABLE constraint_threshold IS 'data_residency: Azure Germany West Central; classification: internal';

CREATE INDEX idx_threshold_type ON constraint_threshold (constraint_type);

-- ------------------------------------------------------------
-- constraint_alert
-- Threshold breach alerts with affected VINs
-- ------------------------------------------------------------
CREATE TABLE constraint_alert (
    alert_id            UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    constraint_type     TEXT            NOT NULL CHECK (constraint_type IN (
                            'paint_capacity','body_wip_queue','kitting_readiness',
                            'maintenance_window','agv_delay','press_die_overrun')),
    current_value       NUMERIC(18,6)   NOT NULL,
    threshold_value     NUMERIC(18,6)   NOT NULL,
    affected_vins       JSONB           NOT NULL DEFAULT '[]',
    status              TEXT            NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active','acknowledged','resolved')),
    detected_at         TIMESTAMPTZ     NOT NULL DEFAULT now(),
    acknowledged_at     TIMESTAMPTZ,
    acknowledged_by     TEXT,
    resolved_at         TIMESTAMPTZ,
    -- data residency: EU only
    data_region         TEXT            NOT NULL DEFAULT 'eu-de-west-central',
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT now()
);

COMMENT ON TABLE constraint_alert IS 'data_residency: Azure Germany West Central; classification: internal';

CREATE INDEX idx_alert_status       ON constraint_alert (status);
CREATE INDEX idx_alert_detected_at  ON constraint_alert (detected_at DESC);
CREATE INDEX idx_alert_type         ON constraint_alert (constraint_type);

-- ------------------------------------------------------------
-- my26_config
-- MY26 model-year constraint configuration versions
-- ------------------------------------------------------------
CREATE TABLE my26_config (
    config_id       UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    version         TEXT            NOT NULL UNIQUE,                       -- semver e.g. '1.0.0'
    status          TEXT            NOT NULL DEFAULT 'draft'
                        CHECK (status IN ('draft','validated','active','superseded')),
    option_codes    JSONB           NOT NULL DEFAULT '[]',
    paint_palette   JSONB           NOT NULL DEFAULT '[]',
    kitting_rules   JSONB           NOT NULL DEFAULT '[]',
    created_by      TEXT            NOT NULL,
    activated_at    TIMESTAMPTZ,
    activated_by    TEXT,
    -- data residency: EU only
    data_region     TEXT            NOT NULL DEFAULT 'eu-de-west-central',
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT now()
);

COMMENT ON TABLE my26_config IS 'data_residency: Azure Germany West Central; classification: confidential';

CREATE INDEX idx_my26_config_status     ON my26_config (status);
CREATE INDEX idx_my26_config_version    ON my26_config (version);
CREATE INDEX idx_my26_config_created_at ON my26_config (created_at DESC);

-- FK from optimizer_run to my26_config (deferred to avoid circular dependency at creation time)
ALTER TABLE optimizer_run
    ADD CONSTRAINT fk_optimizer_run_my26_config
    FOREIGN KEY (my26_config_id) REFERENCES my26_config (config_id);

-- ------------------------------------------------------------
-- model_governance_entry
-- watsonx.governance model inventory record per deployed model version
-- ------------------------------------------------------------
CREATE TABLE model_governance_entry (
    entry_id            UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    model_name          TEXT            NOT NULL,                          -- e.g. 'SequenceOptimizer'
    model_version       TEXT            NOT NULL,
    kserve_endpoint     TEXT            NOT NULL,
    mlflow_run_id       TEXT,
    watsonx_gov_id      TEXT,
    is_primary          BOOLEAN         NOT NULL DEFAULT FALSE,
    is_shadow           BOOLEAN         NOT NULL DEFAULT FALSE,
    deployed_at         TIMESTAMPTZ     NOT NULL DEFAULT now(),
    retired_at          TIMESTAMPTZ,
    deployed_by         TEXT            NOT NULL,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT now()
);

COMMENT ON TABLE model_governance_entry IS 'data_residency: Azure Germany West Central; classification: internal; watsonx.governance inventory';

CREATE INDEX idx_mge_model_name    ON model_governance_entry (model_name);
CREATE INDEX idx_mge_is_primary    ON model_governance_entry (is_primary) WHERE is_primary = TRUE;
CREATE INDEX idx_mge_deployed_at   ON model_governance_entry (deployed_at DESC);
