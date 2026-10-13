-- 001_identity_pseudonymisation_map.sql
-- GDPR Article 17 pseudonymisation mapping table.
-- data_residency=eu-germany-west-central classification=confidential
--
-- employee_id and name are the GDPR-sensitive columns; they are NULLed by the
-- daily scrubber CronJob within 30 days of tombstoned_at being set.
-- pseudonymous_token is never NULLed — it is the stable reference used by
-- torque_log and all immutable audit entries.

CREATE TABLE IF NOT EXISTS identity_pseudonymisation_map (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    employee_id         VARCHAR(8)  UNIQUE,
    name                VARCHAR,
    pseudonymous_token  UUID        NOT NULL DEFAULT gen_random_uuid(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    tombstoned_at       TIMESTAMPTZ
);

COMMENT ON TABLE identity_pseudonymisation_map IS
    'data_residency=eu-germany-west-central classification=confidential';

COMMENT ON COLUMN identity_pseudonymisation_map.employee_id IS
    'Nulled by scrubber within 30 days of tombstoned_at (GDPR Article 17)';

COMMENT ON COLUMN identity_pseudonymisation_map.name IS
    'Nulled by scrubber within 30 days of tombstoned_at (GDPR Article 17)';

COMMENT ON COLUMN identity_pseudonymisation_map.pseudonymous_token IS
    'Stable UUID referenced by torque_log and audit entries — never nulled';

-- Partial index on tombstoned rows — scrubber queries this set only
CREATE INDEX IF NOT EXISTS idx_identity_map_tombstoned_at
    ON identity_pseudonymisation_map (tombstoned_at)
    WHERE tombstoned_at IS NOT NULL;

-- Restrict access: only identity_service ROLE may read or write this table.
-- Executed as superuser; identity_service ROLE is created by Terraform before this migration.
REVOKE ALL ON identity_pseudonymisation_map FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE ON identity_pseudonymisation_map TO identity_service;
