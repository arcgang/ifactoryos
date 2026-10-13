-- Migration: create sync_jobs
-- Tracks VIN sequence publish attempts to Siemens OpCenter.
-- Supports idempotency, retry queue, and Pending Publish status display.
-- Residency: Azure Germany West Central. Classification: internal.
-- Retention: permanent (audit trail).

BEGIN;

CREATE TABLE IF NOT EXISTS sync_jobs (
    sync_job_id         UUID            NOT NULL DEFAULT gen_random_uuid(),
    sequence_plan_id    UUID            NOT NULL,
    planner_user_id     TEXT            NULL,
    status              TEXT            NOT NULL CHECK (status IN (
                            'published', 'queued', 'retrying', 'failed', 'rejected_by_opcenter'
                        )),
    source              TEXT            NOT NULL DEFAULT 'planning-cockpit',
    opcenter_sequence_id TEXT           NULL,
    idempotency_key     UUID            NULL,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    last_attempt_at     TIMESTAMPTZ     NULL,
    next_retry_at       TIMESTAMPTZ     NULL,
    attempt_count       INT             NOT NULL DEFAULT 0,
    opcenter_error      TEXT            NULL,
    published_at        TIMESTAMPTZ     NULL,
    audit_event_id      UUID            NULL,
    CONSTRAINT pk_sync_jobs PRIMARY KEY (sync_job_id)
);

-- Idempotency: one row per idempotency_key (when provided)
CREATE UNIQUE INDEX IF NOT EXISTS uq_sync_jobs_idempotency_key
    ON sync_jobs (idempotency_key)
    WHERE idempotency_key IS NOT NULL;

-- Query: list by sequence_plan and status (planners monitoring Pending Publish)
CREATE INDEX IF NOT EXISTS idx_sync_jobs_sequence_plan_status
    ON sync_jobs (sequence_plan_id, status);

-- Retry worker: find jobs due for retry
CREATE INDEX IF NOT EXISTS idx_sync_jobs_next_retry_at
    ON sync_jobs (next_retry_at)
    WHERE status IN ('queued', 'retrying');

-- Query: list all jobs sorted by creation (keyset pagination)
CREATE INDEX IF NOT EXISTS idx_sync_jobs_created_at
    ON sync_jobs (created_at DESC, sync_job_id DESC);

COMMENT ON TABLE sync_jobs IS
    'VIN sequence publish jobs sent to Siemens OpCenter 10.2. '
    'Tracks idempotency, retry queue, and audit trail for US-004. '
    'Residency: Azure Germany West Central. Classification: internal.';

COMMIT;
