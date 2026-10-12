import type { ConstraintType } from "./constraints";

export type AlertStatus = "active" | "acknowledged" | "resolved";

export interface ConstraintAlertRecord {
  alertId: string;
  constraintType: ConstraintType;
  currentValue: number;
  thresholdValue: number;
  affectedVins: string[];
  status: AlertStatus;
  detectedAt: string;
  acknowledgedAt?: string | null;
  acknowledgedBy?: string | null;
}

export interface AlertListResponse {
  data: ConstraintAlertRecord[];
  page: {
    next_cursor: string | null;
    limit: number;
  };
}

export interface ResequenceRequest {
  alertId: string;
  affectedVins: Array<{ vin: string }>;
  constraintSnapshot: {
    snapshotTimestamp: string;
    paintCapacityUtilisation: number;
    bodyWipQueueDepth: number;
    kittingReadinessPct: number;
  };
  idempotencyKey?: string | null;
}

export interface PublishSequenceRequest {
  sequenceId: string;
  isResequence: boolean;
  my26ConfigVersion?: string | null;
}

export interface AuditEventRequest {
  userId: string;
  action: string;
  configVersion?: string | null;
  my26ConfigVersion?: string | null;
  timestamp: string;
}
