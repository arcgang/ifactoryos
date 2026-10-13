export interface VinSequenceItem {
  vin: string;
  position: number;
  optionCodes: string[];
}

export interface SequencePublishRequest {
  sequencePlanId: string;
  vinSequence: VinSequenceItem[];
  source: "planning-cockpit";
  lockedAt: string;
  plannerUserId?: string;
  idempotencyKey?: string;
}

export interface SequencePublishResponse {
  syncJobId: string;
  opcenterSequenceId: string | null;
  status: "published" | "queued" | "failed";
  source: "planning-cockpit";
  publishedAt: string | null;
  auditEventId?: string | null;
  retryScheduledAt?: string | null;
}

export type SyncJobStatus =
  | "published"
  | "queued"
  | "retrying"
  | "failed"
  | "rejected_by_opcenter";

export interface SyncJob {
  syncJobId: string;
  sequencePlanId: string;
  status: SyncJobStatus;
  createdAt: string;
  updatedAt: string;
  opcenterSequenceId?: string | null;
  opcenterError?: string | null;
  nextRetryAt?: string | null;
  lastAttemptAt?: string | null;
  attemptCount?: number;
  plannerUserId?: string | null;
  source?: "planning-cockpit";
}

export interface CursorPage {
  next_cursor: string | null;
  limit: number;
}

export interface SyncJobListResponse {
  data: SyncJob[];
  page: CursorPage;
}
