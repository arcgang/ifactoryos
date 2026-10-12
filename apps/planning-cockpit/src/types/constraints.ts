export type ConstraintType =
  | "paint_capacity"
  | "body_wip_queue"
  | "kitting_readiness"
  | "maintenance_window"
  | "agv_delay"
  | "press_die_overrun";

export interface ConstraintThreshold {
  thresholdId: string;
  constraintType: ConstraintType;
  warningLevel: number;
  criticalLevel: number;
  unit: string;
  updatedAt?: string | null;
  updatedBy?: string | null;
}

export interface CursorPage {
  next_cursor: string | null;
  limit: number;
}

export interface ThresholdListResponse {
  data: ConstraintThreshold[];
  page: CursorPage;
}

/** Inbound message forwarded from the Node.js SSE bridge (mirrors LiveConstraintTelemetry Avro schema). */
export interface LiveConstraintTelemetry {
  constraintType: ConstraintType;
  currentValue: number;
  unit: string;
  /** ISO-8601 UTC timestamp of the UNS measurement */
  measuredAt: string;
  /** ISO-8601 UTC timestamp when the Node.js bridge received the Kafka message */
  receivedAt: string;
}

export interface ConstraintTileState {
  constraintType: ConstraintType;
  currentValue: number | null;
  unit: string;
  measuredAt: string | null;
  isAmber: boolean;
  isStale: boolean;
  lastKnownGoodAt: string | null;
}
