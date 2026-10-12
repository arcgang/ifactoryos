import type { ConstraintThreshold, ThresholdListResponse } from "../types/constraints";
import type { OptimizeRequest, OptimizeResponse } from "../types/optimizer";
import type {
  AlertListResponse,
  AuditEventRequest,
  ConstraintAlertRecord,
  ResequenceRequest,
  PublishSequenceRequest,
} from "../types/alerts";
import type { My26Config, My26ConfigDraft, My26ValidationResult } from "../types/my26";
import type {
  VinExplanation,
  OverrideRecord,
  OverrideRequest,
  OverrideListResponse,
} from "../types/overrides";

const TOKEN_KEY = "ifos_planning_cockpit_token";

const baseUrl =
  typeof import.meta !== "undefined" && import.meta.env
    ? (import.meta.env.VITE_API_URL ?? "")
    : "";

function redirectToLogin(): void {
  window.location.href = "/login";
}

async function requestRaw(path: string, options: RequestInit = {}): Promise<Response> {
  const token = localStorage.getItem(TOKEN_KEY);
  const headers = new Headers(options.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const res = await fetch(`${baseUrl}${path}`, { ...options, headers });
  if (res.status === 401) {
    localStorage.removeItem(TOKEN_KEY);
    redirectToLogin();
    throw new Error("Unauthorized");
  }
  return res;
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await requestRaw(path, options);
  if (!res.ok) {
    throw new Error((await res.text()) || "Request failed");
  }
  if (res.status === 204) return null as T;
  return res.json() as Promise<T>;
}

export async function listConstraintThresholds(
  cursor?: string,
  limit?: number,
): Promise<ThresholdListResponse> {
  const params = new URLSearchParams();
  if (cursor) params.set("cursor", cursor);
  if (limit !== undefined) params.set("limit", String(limit));
  const qs = params.toString();
  return request<ThresholdListResponse>(
    `/constraints/thresholds${qs ? `?${qs}` : ""}`,
  );
}

export async function upsertConstraintThreshold(
  threshold: ConstraintThreshold,
): Promise<ConstraintThreshold> {
  return request<ConstraintThreshold>("/constraints/thresholds", {
    method: "PUT",
    body: JSON.stringify(threshold),
  });
}

/**
 * Build the SSE URL for live constraint telemetry.
 * Derives protocol/host from window.location so it works on any deployment.
 */
export function getConstraintStreamUrl(): string {
  if (typeof window === "undefined") return "";
  return `${baseUrl}/constraints/stream`;
}

export async function postOptimize(body: OptimizeRequest): Promise<OptimizeResponse> {
  return request<OptimizeResponse>("/optimize", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function getOptimizeRun(runId: string): Promise<OptimizeResponse> {
  return request<OptimizeResponse>(`/optimize/${encodeURIComponent(runId)}`);
}

export async function listActiveAlerts(): Promise<AlertListResponse> {
  return request<AlertListResponse>("/alerts?status=active&limit=50");
}

export async function acknowledgeAlert(alertId: string): Promise<ConstraintAlertRecord> {
  return request<ConstraintAlertRecord>(`/alerts/${encodeURIComponent(alertId)}/acknowledge`, {
    method: "PATCH",
  });
}

export async function postResequence(body: ResequenceRequest): Promise<OptimizeResponse> {
  return request<OptimizeResponse>("/resequence", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function publishSequence(body: PublishSequenceRequest): Promise<void> {
  return request<void>("/sequence/publish", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function getMy26Config(): Promise<My26Config | null> {
  const res = await requestRaw("/my26/config");
  if (res.status === 404) return null;
  if (!res.ok) throw new Error((await res.text()) || "Request failed");
  return res.json() as Promise<My26Config>;
}

export async function validateMy26Config(
  draft: My26ConfigDraft,
): Promise<My26ValidationResult> {
  return request<My26ValidationResult>("/my26/config/validate", {
    method: "POST",
    body: JSON.stringify(draft),
  });
}

export async function saveMy26Config(draft: My26ConfigDraft): Promise<My26Config> {
  return request<My26Config>("/my26/config/save", {
    method: "POST",
    body: JSON.stringify(draft),
  });
}

export async function postAuditEvent(body: AuditEventRequest): Promise<void> {
  return request<void>("/api/v1/audit/event", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function explainVinPosition(
  runId: string,
  vin: string,
): Promise<VinExplanation> {
  return request<VinExplanation>(
    `/optimize/${encodeURIComponent(runId)}/explain/${encodeURIComponent(vin)}`,
  );
}

export async function listOverrides(params: {
  weekIso?: string;
  runId?: string;
  cursor?: string;
  limit?: number;
}): Promise<OverrideListResponse> {
  const p = new URLSearchParams();
  if (params.weekIso) p.set("weekIso", params.weekIso);
  if (params.runId) p.set("runId", params.runId);
  if (params.cursor) p.set("cursor", params.cursor);
  if (params.limit !== undefined) p.set("limit", String(params.limit));
  const qs = p.toString();
  return request<OverrideListResponse>(`/overrides${qs ? `?${qs}` : ""}`);
}

export async function createOverride(body: OverrideRequest): Promise<OverrideRecord> {
  return request<OverrideRecord>("/overrides", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function getOverride(overrideId: string): Promise<OverrideRecord> {
  return request<OverrideRecord>(`/overrides/${encodeURIComponent(overrideId)}`);
}

export async function exportOverridesPdf(weekIso: string): Promise<Blob> {
  const res = await requestRaw(
    `/overrides/export?weekIso=${encodeURIComponent(weekIso)}`,
  );
  if (!res.ok) {
    throw new Error((await res.text()) || "Export failed");
  }
  return res.blob();
}
