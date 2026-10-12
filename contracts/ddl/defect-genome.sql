-- ============================================================
-- defect-genome owned tables
-- All tables reside in Azure Germany West Central (EU data residency)
-- AES-256 encryption at rest enforced at storage layer
-- ============================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Enum types
CREATE TYPE defect_class AS ENUM ('dust', 'orange_peel', 'run', 'crater', 'pinhole');
CREATE TYPE cluster_status AS ENUM ('open', 'in_progress', 'resolved');
CREATE TYPE alert_status AS ENUM ('new', 'acknowledged', 'in_progress', 'resolved');
CREATE TYPE factor_category AS ENUM ('prep_stage', 'booth_parameter', 'climate_condition');

-- ============================================================
-- defect_cluster
-- Stores DBSCAN-detected clusters of paint defects
-- ============================================================
CREATE TABLE defect_cluster (
    cluster_id              UUID            NOT NULL DEFAULT gen_random_uuid(),
    defect_class            defect_class    NOT NULL,
    status                  cluster_status  NOT NULL DEFAULT 'open',
    root_cause_signature    TEXT            NOT NULL,
    confidence_score        NUMERIC(5,4)    NOT NULL CHECK (confidence_score >= 0 AND confidence_score <= 1),
    affected_vin_count      INTEGER         NOT NULL DEFAULT 0 CHECK (affected_vin_count >= 0),
    most_recent_vin         TEXT            NOT NULL,
    process_replay_url      TEXT,
    corrective_action_jira_id TEXT,
    model_version           TEXT            NOT NULL,
    low_confidence          BOOLEAN         NOT NULL DEFAULT FALSE,
    window_start_at         TIMESTAMPTZ     NOT NULL,
    window_end_at           TIMESTAMPTZ     NOT NULL,
    detected_at             TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    -- EU data residency marker
    data_region             TEXT            NOT NULL DEFAULT 'eu-de-west-central',
    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    CONSTRAINT pk_defect_cluster PRIMARY KEY (cluster_id),
    CONSTRAINT chk_defect_cluster_window CHECK (window_end_at > window_start_at)
);

CREATE INDEX idx_defect_cluster_defect_class ON defect_cluster (defect_class);
CREATE INDEX idx_defect_cluster_status ON defect_cluster (status);
CREATE INDEX idx_defect_cluster_detected_at ON defect_cluster (detected_at DESC);
CREATE INDEX idx_defect_cluster_confidence ON defect_cluster (confidence_score DESC);
CREATE INDEX idx_defect_cluster_class_status ON defect_cluster (defect_class, status);

-- ============================================================
-- cluster_vin_member
-- Junction table: which VINs belong to which cluster
-- ============================================================
CREATE TABLE cluster_vin_member (
    id                  UUID        NOT NULL DEFAULT gen_random_uuid(),
    cluster_id          UUID        NOT NULL,
    vin                 TEXT        NOT NULL,
    classified_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    station_id          TEXT        NOT NULL,
    prep_stage          INTEGER     CHECK (prep_stage >= 1 AND prep_stage <= 14),
    severity_score      NUMERIC(5,4),
    -- EU data residency marker
    data_region         TEXT        NOT NULL DEFAULT 'eu-de-west-central',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT pk_cluster_vin_member PRIMARY KEY (id),
    CONSTRAINT fk_cluster_vin_member_cluster FOREIGN KEY (cluster_id)
        REFERENCES defect_cluster (cluster_id) ON DELETE CASCADE,
    CONSTRAINT uq_cluster_vin UNIQUE (cluster_id, vin)
);

CREATE INDEX idx_cluster_vin_member_cluster_id ON cluster_vin_member (cluster_id);
CREATE INDEX idx_cluster_vin_member_vin ON cluster_vin_member (vin);
CREATE INDEX idx_cluster_vin_member_classified_at ON cluster_vin_member (classified_at DESC);

-- ============================================================
-- root_cause_factor
-- Top-5 contributing factors per cluster query result
-- ============================================================
CREATE TABLE root_cause_factor (
    factor_id           UUID            NOT NULL DEFAULT gen_random_uuid(),
    cluster_id          UUID            NOT NULL,
    query_id            UUID            NOT NULL,
    rank                INTEGER         NOT NULL CHECK (rank >= 1 AND rank <= 5),
    factor_name         TEXT            NOT NULL,
    factor_category     factor_category NOT NULL,
    probability_score   NUMERIC(5,4)    NOT NULL CHECK (probability_score >= 0 AND probability_score <= 1),
    supporting_vin_count INTEGER        NOT NULL DEFAULT 0,
    -- EU data residency marker
    data_region         TEXT            NOT NULL DEFAULT 'eu-de-west-central',
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    CONSTRAINT pk_root_cause_factor PRIMARY KEY (factor_id),
    CONSTRAINT fk_root_cause_factor_cluster FOREIGN KEY (cluster_id)
        REFERENCES defect_cluster (cluster_id) ON DELETE CASCADE,
    CONSTRAINT uq_root_cause_factor_rank UNIQUE (query_id, rank)
);

CREATE INDEX idx_root_cause_factor_cluster_id ON root_cause_factor (cluster_id);
CREATE INDEX idx_root_cause_factor_query_id ON root_cause_factor (query_id);
CREATE INDEX idx_root_cause_factor_probability ON root_cause_factor (probability_score DESC);

-- ============================================================
-- cluster_query_log
-- Audit log of every cluster query invocation
-- ============================================================
CREATE TABLE cluster_query_log (
    query_id                UUID            NOT NULL DEFAULT gen_random_uuid(),
    defect_class            defect_class    NOT NULL,
    cluster_id              UUID,
    requested_by_user_id    TEXT            NOT NULL,
    model_confidence        NUMERIC(5,4)    CHECK (model_confidence >= 0 AND model_confidence <= 1),
    low_confidence          BOOLEAN         NOT NULL DEFAULT FALSE,
    affected_vin_count      INTEGER         NOT NULL DEFAULT 0,
    model_version           TEXT            NOT NULL,
    idempotency_key         UUID,
    time_window_hours       INTEGER         NOT NULL DEFAULT 4,
    vin_filter              JSONB,
    advisor_v1_used         BOOLEAN         NOT NULL DEFAULT FALSE,
    queried_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    -- EU data residency marker
    data_region             TEXT            NOT NULL DEFAULT 'eu-de-west-central',
    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    CONSTRAINT pk_cluster_query_log PRIMARY KEY (query_id),
    CONSTRAINT fk_cluster_query_log_cluster FOREIGN KEY (cluster_id)
        REFERENCES defect_cluster (cluster_id) ON DELETE SET NULL
);

CREATE INDEX idx_cluster_query_log_cluster_id ON cluster_query_log (cluster_id);
CREATE INDEX idx_cluster_query_log_user ON cluster_query_log (requested_by_user_id);
CREATE INDEX idx_cluster_query_log_queried_at ON cluster_query_log (queried_at DESC);
CREATE UNIQUE INDEX idx_cluster_query_log_idempotency ON cluster_query_log (idempotency_key)
    WHERE idempotency_key IS NOT NULL;

-- ============================================================
-- cluster_alert
-- Real-time alerts fired when cluster threshold is crossed
-- ============================================================
CREATE TABLE cluster_alert (
    alert_id                    UUID            NOT NULL DEFAULT gen_random_uuid(),
    cluster_id                  UUID            NOT NULL,
    defect_class                defect_class    NOT NULL,
    status                      alert_status    NOT NULL DEFAULT 'new',
    affected_vin_count          INTEGER         NOT NULL CHECK (affected_vin_count >= 3),
    root_cause_signature        TEXT            NOT NULL,
    confidence_score            NUMERIC(5,4)    NOT NULL CHECK (confidence_score >= 0 AND confidence_score <= 1),
    most_recent_vin             TEXT            NOT NULL,
    process_replay_url          TEXT,
    corrective_action_jira_id   TEXT,
    acknowledged_by_user_id     TEXT,
    acknowledged_at             TIMESTAMPTZ,
    triggered_at                TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    -- EU data residency marker
    data_region                 TEXT            NOT NULL DEFAULT 'eu-de-west-central',
    created_at                  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    CONSTRAINT pk_cluster_alert PRIMARY KEY (alert_id),
    CONSTRAINT fk_cluster_alert_cluster FOREIGN KEY (cluster_id)
        REFERENCES defect_cluster (cluster_id) ON DELETE CASCADE
);

CREATE INDEX idx_cluster_alert_cluster_id ON cluster_alert (cluster_id);
CREATE INDEX idx_cluster_alert_status ON cluster_alert (status);
CREATE INDEX idx_cluster_alert_triggered_at ON cluster_alert (triggered_at DESC);
CREATE INDEX idx_cluster_alert_defect_class ON cluster_alert (defect_class);

-- ============================================================
-- model_feedback
-- Engineer feedback on root-cause assignments for model retraining
-- ============================================================
CREATE TABLE model_feedback (
    feedback_id                 UUID            NOT NULL DEFAULT gen_random_uuid(),
    cluster_id                  UUID            NOT NULL,
    query_id                    UUID            NOT NULL,
    engineer_user_id            TEXT            NOT NULL,
    defect_class                defect_class    NOT NULL,
    accepted_factor_rank        INTEGER         CHECK (accepted_factor_rank >= 1 AND accepted_factor_rank <= 5),
    override_root_cause         TEXT,
    notes                       TEXT,
    mlflow_run_id               TEXT,
    -- EU data residency marker
    data_region                 TEXT            NOT NULL DEFAULT 'eu-de-west-central',
    created_at                  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    CONSTRAINT pk_model_feedback PRIMARY KEY (feedback_id),
    CONSTRAINT fk_model_feedback_cluster FOREIGN KEY (cluster_id)
        REFERENCES defect_cluster (cluster_id) ON DELETE CASCADE,
    CONSTRAINT fk_model_feedback_query FOREIGN KEY (query_id)
        REFERENCES cluster_query_log (query_id) ON DELETE CASCADE
);

CREATE INDEX idx_model_feedback_cluster_id ON model_feedback (cluster_id);
CREATE INDEX idx_model_feedback_query_id ON model_feedback (query_id);
CREATE INDEX idx_model_feedback_engineer ON model_feedback (engineer_user_id);
CREATE INDEX idx_model_feedback_created_at ON model_feedback (created_at DESC);

-- ============================================================
-- confidence_threshold_config
-- Configurable thresholds for low-confidence badge and cluster alerts
-- Single-row config table (enforced by constraint)
-- ============================================================
CREATE TABLE confidence_threshold_config (
    config_id                   UUID            NOT NULL DEFAULT gen_random_uuid(),
    singleton_guard             BOOLEAN         NOT NULL DEFAULT TRUE,
    low_confidence_threshold    NUMERIC(5,4)    NOT NULL DEFAULT 0.6000
        CHECK (low_confidence_threshold >= 0 AND low_confidence_threshold <= 1),
    cluster_alert_threshold     NUMERIC(5,4)    NOT NULL DEFAULT 0.8000
        CHECK (cluster_alert_threshold >= 0 AND cluster_alert_threshold <= 1),
    cluster_alert_min_vins      INTEGER         NOT NULL DEFAULT 3 CHECK (cluster_alert_min_vins >= 1),
    cluster_alert_window_hours  INTEGER         NOT NULL DEFAULT 4 CHECK (cluster_alert_window_hours >= 1),
    updated_by_user_id          TEXT,
    -- EU data residency marker
    data_region                 TEXT            NOT NULL DEFAULT 'eu-de-west-central',
    created_at                  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    CONSTRAINT pk_confidence_threshold_config PRIMARY KEY (config_id),
    CONSTRAINT uq_confidence_threshold_singleton UNIQUE (singleton_guard)
);

-- Seed default config row
INSERT INTO confidence_threshold_config
    (low_confidence_threshold, cluster_alert_threshold, cluster_alert_min_vins, cluster_alert_window_hours)
VALUES
    (0.6000, 0.8000, 3, 4);

-- ============================================================
-- advisor_v1_rule
-- Rule-Based Defect Advisor v1 deterministic decision tree rules
-- ============================================================
CREATE TABLE advisor_v1_rule (
    rule_id             UUID            NOT NULL DEFAULT gen_random_uuid(),
    defect_class        defect_class    NOT NULL,
    rule_description    TEXT            NOT NULL,
    conditions          JSONB           NOT NULL,
    recommended_action  TEXT            NOT NULL,
    priority            INTEGER         NOT NULL DEFAULT 100,
    is_active           BOOLEAN         NOT NULL DEFAULT TRUE,
    -- EU data residency marker
    data_region         TEXT            NOT NULL DEFAULT 'eu-de-west-central',
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    CONSTRAINT pk_advisor_v1_rule PRIMARY KEY (rule_id)
);

CREATE INDEX idx_advisor_v1_rule_defect_class ON advisor_v1_rule (defect_class);
CREATE INDEX idx_advisor_v1_rule_active ON advisor_v1_rule (is_active) WHERE is_active = TRUE;
CREATE INDEX idx_advisor_v1_rule_priority ON advisor_v1_rule (defect_class, priority);
