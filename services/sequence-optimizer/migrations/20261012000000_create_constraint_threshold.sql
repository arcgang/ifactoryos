-- Migration: create constraint_threshold table
-- Up

BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'constraint_type') THEN
        CREATE TYPE constraint_type AS ENUM (
            'paint_capacity',
            'body_wip_queue',
            'kitting_readiness',
            'maintenance_window',
            'agv_delay',
            'press_die_overrun'
        );
    END IF;
END$$;

CREATE TABLE IF NOT EXISTS constraint_threshold (
    threshold_id    UUID             PRIMARY KEY DEFAULT gen_random_uuid(),
    constraint_type constraint_type  NOT NULL,
    warning_level   DOUBLE PRECISION NOT NULL,
    critical_level  DOUBLE PRECISION NOT NULL,
    unit            TEXT             NOT NULL,
    updated_at      TIMESTAMPTZ      NOT NULL DEFAULT now(),
    updated_by      TEXT             NOT NULL,
    created_at      TIMESTAMPTZ      NOT NULL DEFAULT now(),
    CONSTRAINT constraint_threshold_unique_type UNIQUE (constraint_type)
);

CREATE INDEX IF NOT EXISTS idx_constraint_threshold_constraint_type
    ON constraint_threshold (constraint_type);

COMMIT;

-- Down
-- BEGIN;
-- DROP TABLE IF EXISTS constraint_threshold;
-- DROP TYPE IF EXISTS constraint_type;
-- COMMIT;
