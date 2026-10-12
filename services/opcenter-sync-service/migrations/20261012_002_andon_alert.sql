-- Migration: create andon_alert
-- Andon alerts received from OpCenter GraphQL subscription.
-- station_id is station-level only — no operator_id stored (BetrVG §87 / GDPR).
-- Residency: Azure Germany West Central. Classification: internal.
-- Retention: 13 months.

BEGIN;

CREATE TABLE IF NOT EXISTS andon_alert (
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
CREATE INDEX IF NOT EXISTS idx_andon_alert_status_minutes
    ON andon_alert (status, minutes_to_predicted_failure ASC NULLS LAST)
    WHERE status = 'open';

-- Query: alerts by line and station
CREATE INDEX IF NOT EXISTS idx_andon_alert_line_station
    ON andon_alert (line_id, station_id);

-- Query: recent alerts for list endpoint cursor pagination
CREATE INDEX IF NOT EXISTS idx_andon_alert_detected_at
    ON andon_alert (detected_at DESC);

-- Dedup: prevent duplicate ingestion of same OpCenter event
CREATE UNIQUE INDEX IF NOT EXISTS uq_andon_alert_opcenter_event_id
    ON andon_alert (opcenter_event_id)
    WHERE opcenter_event_id IS NOT NULL;

COMMENT ON TABLE andon_alert IS
    'Andon alerts received from OpCenter GraphQL subscription. '
    'station_id is station-level only — no operator_id stored (BetrVG §87 / GDPR). '
    'Residency: Azure Germany West Central. Classification: internal. '
    'Retention: 13 months.';

COMMIT;
