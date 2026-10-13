-- Migration: create defect-genome core tables
-- Up

BEGIN;

-- Enums
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'defect_class_type') THEN
        CREATE TYPE defect_class_type AS ENUM (
            'dust',
            'orange_peel',
            'run',
            'crater',
            'pinhole'
        );
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'cluster_status_type') THEN
        CREATE TYPE cluster_status_type AS ENUM (
            'open',
            'in_progress',
            'resolved'
        );
    END IF;
END$$;

-- ---------------------------------------------------------------------------
-- confidence_threshold_config — singleton row, configurable via API
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS confidence_threshold_config (
    singleton_guard              TEXT            PRIMARY KEY DEFAULT 'singleton'
                                                     CHECK (singleton_guard = 'singleton'),
    low_confidence_threshold     NUMERIC(5,4)    NOT NULL DEFAULT 0.6000
                                                     CHECK (low_confidence_threshold >= 0 AND low_confidence_threshold <= 1),
    cluster_alert_threshold      NUMERIC(5,4)    NOT NULL DEFAULT 0.8000
                                                     CHECK (cluster_alert_threshold >= 0 AND cluster_alert_threshold <= 1),
    cluster_alert_min_vins       INTEGER         NOT NULL DEFAULT 3 CHECK (cluster_alert_min_vins >= 1),
    cluster_alert_window_hours   INTEGER         NOT NULL DEFAULT 4 CHECK (cluster_alert_window_hours >= 1),
    updated_at                   TIMESTAMPTZ     NOT NULL DEFAULT now(),
    data_region                  TEXT            NOT NULL DEFAULT 'eu-de-west-central'
);

COMMENT ON TABLE confidence_threshold_config IS 'data_residency: eu-de-west-central; classification: internal; singleton enforced by PRIMARY KEY';

-- Seed singleton row
INSERT INTO confidence_threshold_config (
    singleton_guard, low_confidence_threshold, cluster_alert_threshold,
    cluster_alert_min_vins, cluster_alert_window_hours
) VALUES (
    'singleton', 0.6000, 0.8000, 3, 4
) ON CONFLICT (singleton_guard) DO NOTHING;

-- ---------------------------------------------------------------------------
-- defect_cluster — one row per detected cluster
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS defect_cluster (
    cluster_id                   UUID                PRIMARY KEY DEFAULT gen_random_uuid(),
    defect_class                 defect_class_type   NOT NULL,
    status                       cluster_status_type NOT NULL DEFAULT 'open',
    root_cause_signature         TEXT                NOT NULL,
    confidence_score             NUMERIC(5,4)        NOT NULL CHECK (confidence_score >= 0 AND confidence_score <= 1),
    affected_vin_count           INTEGER             NOT NULL DEFAULT 0 CHECK (affected_vin_count >= 0),
    most_recent_vin              TEXT                NOT NULL,
    detected_at                  TIMESTAMPTZ         NOT NULL,
    process_replay_url           TEXT,
    top_factors                  JSONB,
    model_version                TEXT,
    corrective_action_jira_id    TEXT,
    created_at                   TIMESTAMPTZ         NOT NULL DEFAULT now(),
    updated_at                   TIMESTAMPTZ         NOT NULL DEFAULT now(),
    data_region                  TEXT                NOT NULL DEFAULT 'eu-de-west-central'
);

COMMENT ON TABLE defect_cluster IS 'data_residency: eu-de-west-central; classification: internal';

CREATE INDEX IF NOT EXISTS idx_defect_cluster_defect_class
    ON defect_cluster (defect_class);

CREATE INDEX IF NOT EXISTS idx_defect_cluster_status
    ON defect_cluster (status);

CREATE INDEX IF NOT EXISTS idx_defect_cluster_created_at
    ON defect_cluster (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_defect_cluster_defect_class_status
    ON defect_cluster (defect_class, status);

-- ---------------------------------------------------------------------------
-- cluster_query_result — one row per POST /defect/cluster-query call
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cluster_query_result (
    query_id                UUID                PRIMARY KEY DEFAULT gen_random_uuid(),
    cluster_id              UUID                NOT NULL REFERENCES defect_cluster (cluster_id) ON DELETE RESTRICT,
    defect_class            defect_class_type   NOT NULL,
    model_confidence        NUMERIC(5,4)        NOT NULL CHECK (model_confidence >= 0 AND model_confidence <= 1),
    low_confidence          BOOLEAN             NOT NULL DEFAULT FALSE,
    top_factors             JSONB               NOT NULL DEFAULT '[]',
    affected_vin_count      INTEGER             NOT NULL DEFAULT 0,
    affected_vins           JSONB               NOT NULL DEFAULT '[]',
    model_version           TEXT,
    advisor_v1_fallback     JSONB,
    idempotency_key         UUID                UNIQUE,
    caller_id               TEXT,
    queried_at              TIMESTAMPTZ         NOT NULL DEFAULT now(),
    data_region             TEXT                NOT NULL DEFAULT 'eu-de-west-central'
);

COMMENT ON TABLE cluster_query_result IS 'data_residency: eu-de-west-central; classification: internal';

CREATE INDEX IF NOT EXISTS idx_cluster_query_result_cluster_id
    ON cluster_query_result (cluster_id);

CREATE INDEX IF NOT EXISTS idx_cluster_query_result_idempotency_key
    ON cluster_query_result (idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_cluster_query_result_queried_at
    ON cluster_query_result (queried_at DESC);

-- ---------------------------------------------------------------------------
-- cluster_alert — one row per alert triggered by the streaming consumer
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cluster_alert (
    alert_id                     UUID                PRIMARY KEY DEFAULT gen_random_uuid(),
    cluster_id                   UUID                NOT NULL REFERENCES defect_cluster (cluster_id) ON DELETE RESTRICT,
    defect_class                 defect_class_type   NOT NULL,
    status                       TEXT                NOT NULL DEFAULT 'new'
                                                         CHECK (status IN ('new', 'acknowledged', 'in_progress', 'resolved')),
    confidence_score             NUMERIC(5,4)        NOT NULL CHECK (confidence_score >= 0 AND confidence_score <= 1),
    affected_vin_count           INTEGER             NOT NULL DEFAULT 0 CHECK (affected_vin_count >= 0),
    root_cause_signature         TEXT                NOT NULL,
    process_replay_url           TEXT,
    detected_at                  TIMESTAMPTZ         NOT NULL,
    acknowledged_by_user_id      TEXT,
    acknowledged_at              TIMESTAMPTZ,
    corrective_action_jira_id    TEXT,
    created_at                   TIMESTAMPTZ         NOT NULL DEFAULT now(),
    updated_at                   TIMESTAMPTZ         NOT NULL DEFAULT now(),
    data_region                  TEXT                NOT NULL DEFAULT 'eu-de-west-central'
);

COMMENT ON TABLE cluster_alert IS 'data_residency: eu-de-west-central; classification: internal';

CREATE INDEX IF NOT EXISTS idx_cluster_alert_cluster_id
    ON cluster_alert (cluster_id);

CREATE INDEX IF NOT EXISTS idx_cluster_alert_status
    ON cluster_alert (status);

CREATE INDEX IF NOT EXISTS idx_cluster_alert_detected_at
    ON cluster_alert (detected_at DESC);

-- ---------------------------------------------------------------------------
-- model_feedback — engineer feedback for retraining pipeline
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS model_feedback (
    feedback_id             UUID                PRIMARY KEY DEFAULT gen_random_uuid(),
    cluster_id              UUID                NOT NULL REFERENCES defect_cluster (cluster_id) ON DELETE RESTRICT,
    query_id                UUID                NOT NULL,
    engineer_user_id        TEXT                NOT NULL,
    accepted_factor_rank    INTEGER             NOT NULL CHECK (accepted_factor_rank >= 1 AND accepted_factor_rank <= 5),
    defect_class            defect_class_type   NOT NULL,
    notes                   TEXT,
    override_root_cause     TEXT,
    caller_id               TEXT,
    accepted_at             TIMESTAMPTZ         NOT NULL DEFAULT now(),
    data_region             TEXT                NOT NULL DEFAULT 'eu-de-west-central'
);

COMMENT ON TABLE model_feedback IS 'data_residency: eu-de-west-central; classification: internal';

CREATE INDEX IF NOT EXISTS idx_model_feedback_cluster_id
    ON model_feedback (cluster_id);

CREATE INDEX IF NOT EXISTS idx_model_feedback_defect_class
    ON model_feedback (defect_class);

CREATE INDEX IF NOT EXISTS idx_model_feedback_accepted_at
    ON model_feedback (accepted_at DESC);

COMMIT;

-- Down
-- BEGIN;
-- DROP TABLE IF EXISTS model_feedback;
-- DROP TABLE IF EXISTS cluster_alert;
-- DROP TABLE IF EXISTS cluster_query_result;
-- DROP TABLE IF EXISTS defect_cluster;
-- DROP TABLE IF EXISTS confidence_threshold_config;
-- DROP TYPE IF EXISTS cluster_status_type;
-- DROP TYPE IF EXISTS defect_class_type;
-- COMMIT;
