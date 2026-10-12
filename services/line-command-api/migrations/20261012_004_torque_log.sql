-- Migration: create torque_log
-- Torque tightening records from Beckhoff PLC via edge gateway.
-- Idempotency enforced via idempotency_key unique constraint.
-- (AC-13685a24, AC-69c77dfd, AC-b734032a)

BEGIN;

CREATE TABLE IF NOT EXISTS torque_log (
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

CREATE INDEX IF NOT EXISTS idx_torque_log_vin
    ON torque_log (vin);

CREATE INDEX IF NOT EXISTS idx_torque_log_station_id
    ON torque_log (station_id);

CREATE INDEX IF NOT EXISTS idx_torque_log_vin_fastener
    ON torque_log (vin, fastener_point_id);

CREATE INDEX IF NOT EXISTS idx_torque_log_plc_timestamp
    ON torque_log (plc_timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_torque_log_uns_publish_status
    ON torque_log (uns_publish_status) WHERE uns_publish_status != 'published';

CREATE INDEX IF NOT EXISTS idx_torque_log_in_tolerance
    ON torque_log (in_tolerance) WHERE in_tolerance = FALSE;

COMMIT;
