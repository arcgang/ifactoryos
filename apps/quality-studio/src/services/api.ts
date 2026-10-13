/** Central API client — base URL, auth header, 401/error handling. */

const API_BASE = import.meta.env.VITE_API_URL ?? "";
const TOKEN_KEY = "qs_access_token";

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
// Certificate / Certify types (hyperledger-fabric-api — stub endpoint)
// ---------------------------------------------------------------------------

export type PerGateResult = Record<GateType, GateResult>;

export interface HealthScore {
  overall: GateResult;
  perGate: PerGateResult;
}

export interface CertifyRequest {
  vin: string;
  gateResultsSnapshot: GateResultRecord[];
  healthScore: HealthScore;
  validatorId: string;
}

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
// Core fetch helper
// ---------------------------------------------------------------------------

function redirectToLogin(): void {
  window.location.href = "/login";
}

export async function apiRequest<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const token = localStorage.getItem(TOKEN_KEY);
  const headers = new Headers(options.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });

  if (res.status === 401) {
    localStorage.removeItem(TOKEN_KEY);
    redirectToLogin();
    throw new Error("Unauthorized");
  }

  if (!res.ok) {
    throw new Error((await res.text()) || `Request failed: ${res.status}`);
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
// Certify API (stub — real endpoint from hyperledger-fabric-api story)
// ---------------------------------------------------------------------------

export function certifyVehicle(body: CertifyRequest): Promise<CertifyResponse> {
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
