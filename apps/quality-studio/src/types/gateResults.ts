/** Types derived from the opcenter-sync-service LLD contract. */

export type GateType = "dimensional" | "leak" | "electrical" | "nvh" | "adas";
export type GateResult = "pass" | "conditional_pass" | "fail";

export const ALL_GATE_TYPES: GateType[] = [
  "dimensional",
  "leak",
  "electrical",
  "nvh",
  "adas",
];

export interface GateMeasurement {
  measurementId: string;
  name: string;
  value: number;
  unit: string;
  toleranceLow: number;
  toleranceHigh: number;
  withinTolerance: boolean;
  reworkThresholdLow: number | null;
  reworkThresholdHigh: number | null;
}

export interface GateResultRecord {
  gateResultId: string;
  vin: string;
  gateId: string;
  gateType: GateType;
  result: GateResult;
  measurements: GateMeasurement[];
  measuredAt: string;
  receivedFromOpcenterAt: string;
  opcenterEventId: string | null;
}

export interface CursorPage {
  next_cursor: string | null;
  limit: number;
}

export interface GateResultListResponse {
  data: GateResultRecord[];
  page: CursorPage;
}
