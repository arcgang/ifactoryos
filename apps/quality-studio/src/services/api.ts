import type {
  ProcessReplayResponse,
  ThreadCompletenessResponse,
  ThreadSnapshotResponse,
  VinThreadResponse,
} from "../types/processReplay";
import type {
  AcknowledgeAlertRequest,
  AlertListResponse,
  ClusterAlert,
  CorrectiveActionRequest,
  CorrectiveActionResult,
  ClusterListResponse,
  ClusterQueryRequest,
  ClusterQueryResult,
  ConfidenceThresholdConfig,
  DefectClass,
  DefectCluster,
  ClusterStatus,
  ModelFeedbackRequest,
  ModelFeedbackAck,
} from "../types/defectGenome";

const BASE_URL =
  (import.meta as unknown as { env?: Record<string, string> }).env?.VITE_API_URL ?? "";
const TOKEN_KEY = "qs-auth-token";

// ---------------------------------------------------------------------------
// Gate result types (matches opcenter-sync-service LLD contract)
// ---------------------------------------------------------------------------

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

export type GateType = "dimensional" | "leak" | "electrical" | "nvh" | "adas";
export type GateResult = "pass" | "conditional_pass" | "fail";

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

export interface ListGateResultsParams {
  cursor?: string;
  limit?: number;
  vin?: string;
  gateType?: GateType;
  result?: GateResult;
  receivedAfter?: string;
}

// ---------------------------------------------------------------------------
// Certificate / Certify types (hyperledger-fabric-api LLD contract)
// ---------------------------------------------------------------------------

export type PerGateResult = Record<GateType, GateResult>;

export interface HealthScore {
  overall: GateResult;
  perGate: PerGateResult;
}

/** GateResultSnapshot station result per LLD contract */
export type StationCode = "DIM" | "LEAK" | "ELEC" | "NVH" | "ADAS";
export type StationResult = "PASS" | "CONDITIONAL" | "FAIL";

export interface StationResultItem {
  stationCode: StationCode;
  result: StationResult;
  details?: string;
}

export interface GateResultSnapshot {
  gateId: string;
  stationResults: StationResultItem[];
}

/** POST /certificate/issue request (LLD contract) */
export interface CertificateIssueRequest {
  certificateId: string;
  vin: string;
  gateResultSnapshot: GateResultSnapshot;
  healthScore: number;
  validatorId: string;
  issuedAt: string;
  certificateHash: string;
  shiftId?: string;
  sourceSystem?: "quality-studio" | "legacy";
}

/** POST /certificate/issue response (LLD contract) */
export interface CertificateIssueResponse {
  certificateId: string;
  vin: string;
  fabricTransactionId: string;
  fabricBlockNumber: number;
  anchoredAt: string;
  certificateHash: string;
  idempotent?: boolean;
}

/** GET /certificate/{certificateId} response (LLD contract) */
export interface CertificateRecord {
  certificateId: string;
  vin: string;
  gateResultSnapshot: GateResultSnapshot;
  healthScore: number;
  validatorId: string;
  issuedAt: string;
  anchoredAt: string;
  certificateHash: string;
  fabricTransactionId: string;
  fabricBlockNumber: number;
  sourceSystem: "quality-studio" | "legacy";
  shiftId?: string;
}

/** GET /certificates list response (LLD contract) */
export interface CertificateListResponse {
  data: CertificateRecord[];
  page: CursorPage;
}

/** GET /certificate/{certificateId}/verify response (LLD contract) */
export interface CertificateVerifyResponse {
  certificateId: string;
  verified: boolean;
  storedHash: string;
  fabricTransactionId: string;
  fabricHash?: string;
  verifiedAt?: string;
  mismatchReason?: string;
}

/** Legacy shape kept for backward-compat with existing CertificateStudioPanel */
export interface CertifyResponse {
  certificateId: string;
  fabricTxHash: string;
  vin: string;
  issuedAt: string;
  validatorId: string;
  healthScore: HealthScore;
  gateResultsSnapshot: GateResultRecord[];
}

// ---------------------------------------------------------------------------
// Override request types (manual override flow)
// ---------------------------------------------------------------------------

export interface OverrideRequest {
  vin: string;
  justification: string;
  validatorId: string;
}

export interface OverrideStatus {
  vin: string;
  status: "pending" | "approved" | "rejected";
  approvedBy?: string;
  approvedAt?: string;
}

// ---------------------------------------------------------------------------
// Error type
// ---------------------------------------------------------------------------

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

// ---------------------------------------------------------------------------
// Core fetch helpers
// ---------------------------------------------------------------------------

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token =
    typeof localStorage !== "undefined" ? localStorage.getItem(TOKEN_KEY) : null;
  const headers = new Headers(options.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(`${BASE_URL}/api/v1${path}`, { ...options, headers });

  if (!res.ok) {
    throw new ApiError(res.status, (await res.text()) || `HTTP ${res.status}`);
  }

  if (res.status === 204) return null as T;
  return res.json() as Promise<T>;
}

// Public apiRequest — full path, no /api/v1 prefix added (used by gate/certify functions)
export async function apiRequest<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const token =
    typeof localStorage !== "undefined" ? localStorage.getItem(TOKEN_KEY) : null;
  const headers = new Headers(options.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const res = await fetch(`${BASE_URL}${path}`, { ...options, headers });
  if (!res.ok) {
    throw new ApiError(res.status, (await res.text()) || `HTTP ${res.status}`);
  }
  if (res.status === 204) return null as T;
  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Gate results API
// ---------------------------------------------------------------------------

export function listGateResults(
  params: ListGateResultsParams = {},
): Promise<GateResultListResponse> {
  const qs = new URLSearchParams();
  if (params.cursor) qs.set("cursor", params.cursor);
  if (params.limit !== undefined) qs.set("limit", String(params.limit));
  if (params.vin) qs.set("vin", params.vin);
  if (params.gateType) qs.set("gateType", params.gateType);
  if (params.result) qs.set("result", params.result);
  if (params.receivedAfter) qs.set("receivedAfter", params.receivedAfter);
  const query = qs.toString();
  return apiRequest<GateResultListResponse>(
    `/api/v1/gate-results${query ? `?${query}` : ""}`,
  );
}

export function getGateResult(gateResultId: string): Promise<GateResultRecord> {
  return apiRequest<GateResultRecord>(
    `/api/v1/gate-results/${encodeURIComponent(gateResultId)}`,
  );
}

// ---------------------------------------------------------------------------
// Certificate API (hyperledger-fabric-api LLD contract)
// ---------------------------------------------------------------------------

export async function issueCertificate(
  body: CertificateIssueRequest,
): Promise<{ response: CertificateIssueResponse; status: 200 | 201 }> {
  const token =
    typeof localStorage !== "undefined" ? localStorage.getItem(TOKEN_KEY) : null;
  const headers = new Headers();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  headers.set("Content-Type", "application/json");

  const res = await fetch(`${BASE_URL}/api/v1/certificate/issue`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });

  if (res.status === 401) {
    throw new ApiError(401, "Unauthorized");
  }
  if (!res.ok) {
    const text = (await res.text()) || `HTTP ${res.status}`;
    throw new ApiError(res.status, text);
  }
  const response = (await res.json()) as CertificateIssueResponse;
  return { response, status: res.status as 200 | 201 };
}

export function getCertificate(certificateId: string): Promise<CertificateRecord> {
  return request<CertificateRecord>(
    `/certificate/${encodeURIComponent(certificateId)}`,
  );
}

export async function listCertificates(params: {
  cursor?: string;
  limit?: number;
  vin?: string;
  shiftId?: string;
  issuedAfter?: string;
  issuedBefore?: string;
}): Promise<CertificateListResponse> {
  const qs = new URLSearchParams();
  if (params.cursor) qs.set("cursor", params.cursor);
  if (params.limit !== undefined) qs.set("limit", String(params.limit));
  if (params.vin) qs.set("vin", params.vin);
  if (params.shiftId) qs.set("shiftId", params.shiftId);
  if (params.issuedAfter) qs.set("issuedAfter", params.issuedAfter);
  if (params.issuedBefore) qs.set("issuedBefore", params.issuedBefore);
  const query = qs.toString();
  return request<CertificateListResponse>(`/certificates${query ? `?${query}` : ""}`);
}

export function verifyCertificate(
  certificateId: string,
): Promise<CertificateVerifyResponse> {
  return request<CertificateVerifyResponse>(
    `/certificate/${encodeURIComponent(certificateId)}/verify`,
  );
}

// ---------------------------------------------------------------------------
// Certify API (legacy stub — kept for backward-compat with existing tests)
// ---------------------------------------------------------------------------

export function certifyVehicle(body: {
  vin: string;
  gateResultsSnapshot: GateResultRecord[];
  healthScore: HealthScore;
  validatorId: string;
}): Promise<CertifyResponse> {
  return apiRequest<CertifyResponse>("/api/v1/certificate/issue", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// ---------------------------------------------------------------------------
// Override request API (manual override flow)
// ---------------------------------------------------------------------------

export function submitOverrideRequest(
  body: OverrideRequest,
): Promise<OverrideStatus> {
  return apiRequest<OverrideStatus>("/api/v1/gate-results/override-requests", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function getOverrideStatus(vin: string): Promise<OverrideStatus> {
  return apiRequest<OverrideStatus>(
    `/api/v1/gate-results/override-requests/${encodeURIComponent(vin)}/status`,
  );
}

// ---------------------------------------------------------------------------
// VIN Digital Thread endpoints
// ---------------------------------------------------------------------------

export async function getVinThread(
  vin: string,
  params: { cursor?: string; limit?: number; shopFilter?: string; fromTs?: string; toTs?: string } = {},
): Promise<VinThreadResponse> {
  const q = new URLSearchParams();
  if (params.cursor) q.set("cursor", params.cursor);
  if (params.limit != null) q.set("limit", String(params.limit));
  if (params.shopFilter) q.set("shopFilter", params.shopFilter);
  if (params.fromTs) q.set("fromTs", params.fromTs);
  if (params.toTs) q.set("toTs", params.toTs);
  const qs = q.toString() ? `?${q}` : "";
  return request<VinThreadResponse>(`/vin/${encodeURIComponent(vin)}/thread${qs}`);
}

export async function getVinThreadPage(
  vin: string,
  options: {
    cursor?: string | null;
    limit?: number;
    shopFilter?: string;
    fromTs?: string;
    toTs?: string;
    signal?: AbortSignal;
  } = {},
): Promise<VinThreadResponse> {
  const params = new URLSearchParams();
  if (options.cursor) params.set("cursor", options.cursor);
  if (options.limit != null) params.set("limit", String(options.limit));
  if (options.shopFilter) params.set("shopFilter", options.shopFilter);
  if (options.fromTs) params.set("fromTs", options.fromTs);
  if (options.toTs) params.set("toTs", options.toTs);

  const qs = params.toString();
  const path = `/vin/${encodeURIComponent(vin)}/thread${qs ? `?${qs}` : ""}`;
  return request<VinThreadResponse>(path, { signal: options.signal });
}

export async function getAllVinThreadEvents(
  vin: string,
  shopFilter?: string,
  signal?: AbortSignal,
): Promise<VinThreadResponse> {
  let cursor: string | null = null;
  let allData: VinThreadResponse["data"] = [];
  let lastPage: VinThreadResponse | null = null;

  do {
    const page = await getVinThreadPage(vin, {
      cursor,
      limit: 200,
      shopFilter,
      signal,
    });
    allData = allData.concat(page.data);
    cursor = page.page.next_cursor;
    lastPage = page;
  } while (cursor !== null);

  return {
    vin: lastPage!.vin,
    data: allData,
    page: { next_cursor: null, limit: 200 },
    vinStatus: lastPage!.vinStatus,
  };
}

export async function getVinProcessReplay(
  vin: string,
  params: { cursor?: string; limit?: number } = {},
): Promise<ProcessReplayResponse> {
  const q = new URLSearchParams();
  if (params.cursor) q.set("cursor", params.cursor);
  if (params.limit != null) q.set("limit", String(params.limit));
  const qs = q.toString() ? `?${q}` : "";
  return request<ProcessReplayResponse>(`/vin/${encodeURIComponent(vin)}/thread/replay${qs}`);
}

export async function getVinThreadSnapshot(
  vin: string,
  atTs: string,
): Promise<ThreadSnapshotResponse> {
  const q = new URLSearchParams({ atTs });
  return request<ThreadSnapshotResponse>(
    `/vin/${encodeURIComponent(vin)}/thread/snapshot?${q}`,
  );
}

export async function getVinThreadCompleteness(
  vin: string,
  signal?: AbortSignal,
): Promise<ThreadCompletenessResponse> {
  return request<ThreadCompletenessResponse>(
    `/vin/${encodeURIComponent(vin)}/thread/completeness`,
    { signal },
  );
}

// ---------------------------------------------------------------------------
// DefectGenome / Cluster endpoints
// ---------------------------------------------------------------------------

export async function getDefectGenomeAlerts(params: {
  status?: string;
  cursor?: string;
  limit?: number;
} = {}): Promise<AlertListResponse> {
  const q = new URLSearchParams();
  if (params.status) q.set("status", params.status);
  if (params.cursor) q.set("cursor", params.cursor);
  if (params.limit != null) q.set("limit", String(params.limit));
  const qs = q.toString() ? `?${q}` : "";
  return request<AlertListResponse>(`/defect-genome/alerts${qs}`);
}

export async function runClusterQuery(
  body: ClusterQueryRequest,
): Promise<ClusterQueryResult> {
  return request<ClusterQueryResult>("/defect/cluster-query", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function listClusters(params: {
  defectClass?: DefectClass;
  status?: ClusterStatus;
  cursor?: string;
  limit?: number;
} = {}): Promise<ClusterListResponse> {
  const q = new URLSearchParams();
  if (params.defectClass) q.set("defectClass", params.defectClass);
  if (params.status) q.set("status", params.status);
  if (params.cursor) q.set("cursor", params.cursor);
  if (params.limit != null) q.set("limit", String(params.limit));
  const qs = q.toString() ? `?${q}` : "";
  return request<ClusterListResponse>(`/defect/clusters${qs}`);
}

export async function getClusterAlert(alertId: string): Promise<ClusterAlert> {
  return request<ClusterAlert>(`/defect-genome/alerts/${encodeURIComponent(alertId)}`);
}

export async function acknowledgeClusterAlert(
  alertId: string,
  body: AcknowledgeAlertRequest,
): Promise<ClusterAlert> {
  return request<ClusterAlert>(
    `/defect-genome/alerts/${encodeURIComponent(alertId)}/acknowledge`,
    { method: "POST", body: JSON.stringify(body) },
  );
}

export async function getCluster(clusterId: string): Promise<DefectCluster> {
  return request<DefectCluster>(`/defect/clusters/${encodeURIComponent(clusterId)}`);
}

export async function getConfidenceThreshold(): Promise<ConfidenceThresholdConfig> {
  return request<ConfidenceThresholdConfig>("/models/confidence-threshold");
}

export async function createCorrectiveAction(
  clusterId: string,
  body: CorrectiveActionRequest,
): Promise<CorrectiveActionResult> {
  return request<CorrectiveActionResult>(
    `/defect/clusters/${encodeURIComponent(clusterId)}/corrective-action`,
    { method: "POST", body: JSON.stringify(body) },
  );
}

export async function submitModelFeedback(
  body: ModelFeedbackRequest,
): Promise<ModelFeedbackAck> {
  return request<ModelFeedbackAck>("/models/feedback", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
