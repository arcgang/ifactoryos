-- Migration: create ar_instruction_cache
-- Caches resolved VIN instruction payloads for fast AR overlay serving.
-- TTL-expired rows are cleaned by a background job (DELETE WHERE expires_at < NOW()).

BEGIN;

CREATE TABLE IF NOT EXISTS ar_instruction_cache (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    vin                 TEXT        NOT NULL,
    station_id          TEXT        NOT NULL,
    option_codes        TEXT[]      NOT NULL DEFAULT '{}',
    variant_code        TEXT        NOT NULL,
    is_rare_variant     BOOLEAN     NOT NULL DEFAULT FALSE,
    instruction_version TEXT        NOT NULL,
    payload_jsonb       JSONB       NOT NULL,
    fallback_pdf_qr_url TEXT        NOT NULL,
    fetched_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at          TIMESTAMPTZ NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE ar_instruction_cache IS 'data_residency=eu-germany-west-central classification=internal';

CREATE INDEX IF NOT EXISTS idx_ar_instruction_cache_vin
    ON ar_instruction_cache (vin);

CREATE INDEX IF NOT EXISTS idx_ar_instruction_cache_vin_station
    ON ar_instruction_cache (vin, station_id);

CREATE INDEX IF NOT EXISTS idx_ar_instruction_cache_expires_at
    ON ar_instruction_cache (expires_at);

COMMIT;
