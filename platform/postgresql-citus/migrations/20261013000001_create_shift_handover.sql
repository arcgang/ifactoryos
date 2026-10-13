-- Migration: create shift_handover table (US-009, cmp:line-command-api)
-- Up

BEGIN;

CREATE TABLE IF NOT EXISTS shift_handover (
    id                      UUID            NOT NULL DEFAULT gen_random_uuid(),
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
    CONSTRAINT pk_shift_handover PRIMARY KEY (id),
    CONSTRAINT uq_shift_handover_idempotency UNIQUE (idempotency_key),
    CONSTRAINT uq_shift_handover_shift_line UNIQUE (shift_id, line_id)
);

COMMENT ON TABLE shift_handover IS 'data_residency=eu-germany-west-central classification=internal';

-- Query: fetch handovers by shift_id for the incoming Shift Lead
CREATE INDEX IF NOT EXISTS idx_shift_handover_shift_id ON shift_handover (shift_id);

-- Query: fetch handovers by line_id with cursor pagination
CREATE INDEX IF NOT EXISTS idx_shift_handover_line_id ON shift_handover (line_id);

-- Query: audit trail of which Shift Lead submitted the handover
CREATE INDEX IF NOT EXISTS idx_shift_handover_submitted_by ON shift_handover (submitted_by_user_id);

-- Query: fetch most recent handovers for a line (Command Center, historical view)
CREATE INDEX IF NOT EXISTS idx_shift_handover_shift_end_ts ON shift_handover (shift_end_ts DESC);

-- Citus: distribute on line_id so all handovers for a production line collocate on one shard.
-- select_distributed_table_colocate_with => 'none' because there is no parent
-- distributed table to colocate with in this migration.
SELECT create_distributed_table('shift_handover', 'line_id');

COMMIT;

-- Down
-- BEGIN;
-- DROP TABLE IF EXISTS shift_handover;
-- COMMIT;
