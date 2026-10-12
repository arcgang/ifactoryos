export type FactorName =
  | "paint_batch_conflict"
  | "changeover_penalty"
  | "maintenance_window"
  | "kitting_gap"
  | "vip_priority"
  | "regulatory_hold";

export interface ConstraintFactor {
  factorName: FactorName;
  weightedScore: number;
  dataSource: string;
  rawValue?: number | null;
}

export interface VinExplanation {
  runId: string;
  vin: string;
  position: number;
  topFactors: ConstraintFactor[];
}

export interface OverrideRecord {
  overrideId: string;
  runId: string;
  vin: string;
  originalPosition: number;
  newPosition: number;
  justification: string;
  plannerId: string;
  createdAt: string;
}

export interface OverrideRequest {
  runId: string;
  vin: string;
  originalPosition: number;
  newPosition: number;
  justification: string;
}

export interface CursorPage {
  next_cursor: string | null;
  limit: number;
}

export interface OverrideListResponse {
  data: OverrideRecord[];
  page: CursorPage;
}
