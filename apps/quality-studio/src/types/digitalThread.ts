export type EventType =
  | "PLC_TRACE"
  | "GATE_RESULT"
  | "TORQUE_LOG"
  | "AR_INSTRUCTION_EXECUTION"
  | "PAINT_PREP_STAGE"
  | "BOOTH_PARAMETER"
  | "CLIMATE_DATA"
  | "SHOP_BOUNDARY_TRANSITION"
  | "QUALITY_CERTIFICATION";

export type VinStatus = "IN_PROGRESS" | "FINAL_GATE_COMPLETE" | "UNKNOWN";

export interface ThreadEvent {
  eventId: string;
  vin: string;
  eventType: EventType;
  shop: string;
  station: string;
  occurredAt: string;
  dataSource: string;
  parameters: Record<string, number | string | boolean | null>;
  createdAt?: string | null;
  stageNumber?: number | null;
  tunnelPositionMm?: number | null;
}

export interface CursorPage {
  next_cursor: string | null;
  limit: number;
}

export interface VinThreadResponse {
  vin: string;
  data: ThreadEvent[];
  page: CursorPage;
  vinStatus?: VinStatus;
}

export interface StageCompletenessDetail {
  stageNumber: number;
  stageName: string;
  available: boolean;
  completenessPercent: number;
  expectedSampleCount: number;
  actualSampleCount: number;
  missingParameters?: string[] | null;
}

export interface ThreadCompletenessResponse {
  vin: string;
  overallCompletenessPercent: number;
  meetsThreshold?: boolean | null;
  stages: StageCompletenessDetail[];
}

export interface ApiError {
  code: string;
  message: string;
  correlation_id: string;
  details?: Record<string, unknown>[];
}
