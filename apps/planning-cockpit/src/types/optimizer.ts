export interface MaintenanceWindow {
  windowId: string;
  stationId: string;
  startsAt: string;
  endsAt: string;
  description?: string | null;
}

export interface ConstraintSnapshot {
  snapshotTimestamp: string;
  paintCapacityUtilisation: number;
  bodyWipQueueDepth: number;
  kittingReadinessPct: number;
  unsLatencyMs?: number | null;
  maintenanceWindows?: MaintenanceWindow[] | null;
}

export interface VinInput {
  vin: string;
  modelYear?: "MY25" | "MY26" | null;
  paintCode?: string | null;
  optionCodes?: string[] | null;
  vipPriority?: boolean;
  regulatoryHold?: boolean;
}

export interface OptimizeRequest {
  weekIso: string;
  vins: VinInput[];
  constraintSnapshot: ConstraintSnapshot;
  monteCarloRuns?: number;
  idempotencyKey?: string | null;
  my26ConfigVersion?: string | null;
}

export interface RankedVin {
  vin: string;
  position: number;
  constraintScore: number;
  vipPriority?: boolean | null;
  regulatoryHold?: boolean | null;
}

export type ResultType = "monte_carlo" | "heuristic_fallback";

export interface OptimizeResponse {
  runId: string;
  weekIso: string;
  resultType: ResultType;
  rankedSequence: RankedVin[];
  completedAt: string;
  durationMs: number;
  confidenceScore: number;
  my26ConfigVersion: string | null;
  constraintSnapshotTimestamp?: string | null;
}
