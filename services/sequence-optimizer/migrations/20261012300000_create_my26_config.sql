-- Migration: create my26_config table
-- Up

BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'my26_config_status') THEN
        CREATE TYPE my26_config_status AS ENUM (
            'draft',
            'validated',
            'active',
            'superseded'
        );
    END IF;
END$$;

CREATE TABLE IF NOT EXISTS my26_config (
    config_id       UUID               PRIMARY KEY DEFAULT gen_random_uuid(),
    version         TEXT               NOT NULL,
    status          my26_config_status NOT NULL DEFAULT 'draft',
    option_codes    JSONB              NOT NULL DEFAULT '[]',
    paint_palette   JSONB              NOT NULL DEFAULT '[]',
    kitting_rules   JSONB              NOT NULL DEFAULT '[]',
    created_at      TIMESTAMPTZ        NOT NULL DEFAULT now(),
    created_by      TEXT               NOT NULL,
    activated_at    TIMESTAMPTZ,
    data_region     TEXT               NOT NULL DEFAULT 'eu-de-west-central'
);

COMMENT ON TABLE my26_config IS 'data_residency: eu-de-west-central';

-- Enforce at-most-one active config at a time
CREATE UNIQUE INDEX IF NOT EXISTS idx_my26_config_single_active
    ON my26_config (status)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_my26_config_status
    ON my26_config (status);

CREATE INDEX IF NOT EXISTS idx_my26_config_created_at
    ON my26_config (created_at DESC);

COMMIT;

-- Down
-- BEGIN;
-- DROP TABLE IF EXISTS my26_config;
-- DROP TYPE IF EXISTS my26_config_status;
-- COMMIT;
