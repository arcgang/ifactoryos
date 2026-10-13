-- Migration: create gate_result table (US-017, cmp:opcenter-sync-service)
-- Up

BEGIN;

CREATE TABLE IF NOT EXISTS gate_result (
    gate_result_id            UUID        NOT NULL DEFAULT gen_random_uuid(),
    vin                       CHAR(17)    NOT NULL,
    gate_id                   TEXT        NOT NULL,
    gate_type                 TEXT        NOT NULL
        CHECK (gate_type IN ('dimensional','leak','electrical','nvh','adas')),
    result                    TEXT        NOT NULL
        CHECK (result IN ('pass','conditional_pass','fail')),
    measurements              JSONB       NOT NULL DEFAULT '[]',
    measured_at               TIMESTAMPTZ NOT NULL,
    received_from_opcenter_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    opcenter_event_id         TEXT        NULL,
    kafka_offset              BIGINT      NULL,
    correlation_id            UUID        NOT NULL DEFAULT gen_random_uuid(),
    created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT pk_gate_result PRIMARY KEY (gate_result_id)
);

COMMENT ON TABLE gate_result IS
    'Quality gate results received from OpCenter GraphQL subscription. '
    'measurements JSONB contains array of GateMeasurement objects. '
    'Residency: Azure Germany West Central. Classification: confidential. '
    'Retention: 7 years (regulatory quality record).';

-- Query: all gate results for a VIN (health score aggregation)
CREATE INDEX IF NOT EXISTS idx_gate_result_vin
    ON gate_result (vin);

-- Query: results by VIN + gate_type (dedup check)
CREATE INDEX IF NOT EXISTS idx_gate_result_vin_gate_type
    ON gate_result (vin, gate_type);

-- Query: recent results for list endpoint cursor pagination
CREATE INDEX IF NOT EXISTS idx_gate_result_received_at
    ON gate_result (received_from_opcenter_at DESC);

-- Query: conditional_pass results for anomaly highlighting
CREATE INDEX IF NOT EXISTS idx_gate_result_conditional_pass
    ON gate_result (vin, gate_type)
    WHERE result = 'conditional_pass';

-- Dedup: prevent duplicate ingestion of same OpCenter event
CREATE UNIQUE INDEX IF NOT EXISTS uq_gate_result_opcenter_event_id
    ON gate_result (opcenter_event_id)
    WHERE opcenter_event_id IS NOT NULL;

-- Citus: distribute on vin so all gate results for a vehicle collocate on one shard.
-- select_distributed_table_colocate_with => 'none' because there is no parent
-- distributed table to colocate with in this migration.
SELECT create_distributed_table('gate_result', 'vin');

COMMIT;

-- Down
-- BEGIN;
-- DROP TABLE IF EXISTS gate_result;
-- COMMIT;
