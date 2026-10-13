export interface CursorPage {
  next_cursor: string | null;
  limit: number;
}

export interface PrepStageSample {
  ts: string;
  parameters: Record<string, number | string | boolean | null>;
  tunnelPositionMm?: number | null;
}

export interface PrepStageTimeSeries {
  stageNumber: number;
  stageName: string;
  available: boolean;
  samples: PrepStageSample[];
}

export interface ClimateSample {
  ts: string;
  parameters: Record<string, number | string | boolean | null>;
}

export interface ClimateTimeSeries {
  available: boolean;
  samples: ClimateSample[];
}

export interface BoothSample {
  ts: string;
  parameters: Record<string, number | string | boolean | null>;
}

export interface BoothParameterTimeSeries {
  boothId: string;
  available: boolean;
  samples: BoothSample[];
}

export interface ProcessReplayResponse {
  vin: string;
  overallCompletenessPercent: number;
  prepStages: PrepStageTimeSeries[];
  boothParameters: BoothParameterTimeSeries[];
  climateData: ClimateTimeSeries;
  page: CursorPage;
  replayWindowStart: string | null;
  replayWindowEnd: string | null;
}

export interface ThreadSnapshotResponse {
  vin: string;
  atTs: string;
  stageName: string | null;
  stageNumber: number | null;
  tunnelPositionMm: number | null;
  parameters: Record<string, number | string | boolean | null>;
  dataAvailable?: boolean;
}

export interface StageCompletenessDetail {
  stageNumber: number;
  stageName: string;
  available: boolean;
  completenessPercent: number;
  expectedSampleCount: number;
  actualSampleCount: number;
  missingParameters?: string[];
}

export interface ThreadCompletenessResponse {
  vin: string;
  overallCompletenessPercent: number;
  meetsThreshold?: boolean;
  stages: StageCompletenessDetail[];
}

export interface ThreadEvent {
  eventId: string;
  vin: string;
  eventType: string;
  shop: string;
  station: string;
  occurredAt: string;
  dataSource: string;
  parameters: Record<string, number | string | boolean | null>;
  createdAt?: string | null;
  stageNumber?: number | null;
  tunnelPositionMm?: number | null;
}

export interface VinThreadResponse {
  vin: string;
  data: ThreadEvent[];
  page: CursorPage;
  vinStatus?: "IN_PROGRESS" | "FINAL_GATE_COMPLETE" | "UNKNOWN" | null;
}
