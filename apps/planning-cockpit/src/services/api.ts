import type { ConstraintThreshold, ThresholdListResponse } from "../types/constraints";
import type {
  SequencePublishRequest,
  SequencePublishResponse,
  SyncJob,
  SyncJobListResponse,
  SyncJobStatus,
} from "../types/sequence";

const TOKEN_KEY = "ifos_planning_cockpit_token";

const baseUrl =
  typeof import.meta !== "undefined" && import.meta.env
    ? (import.meta.env.VITE_API_URL ?? "")
    : "";

function redirectToLogin(): void {
  window.location.href = "/login";
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
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

export async function publishSequence(
  req: SequencePublishRequest,
): Promise<SequencePublishResponse> {
  const res = await fetch(`${baseUrl}/sequence/publish`, {
    method: "POST",
    headers: buildAuthHeaders(),
    body: JSON.stringify(req),
  });
  if (res.status === 401) {
    localStorage.removeItem(TOKEN_KEY);
    redirectToLogin();
    throw new Error("Unauthorized");
  }
  if (!res.ok) {
    const body = await res.text();
    throw new Error(body || `Request failed with status ${res.status}`);
  }
  return res.json() as Promise<SequencePublishResponse>;
}

export async function getSyncJob(syncJobId: string): Promise<SyncJob> {
  return request<SyncJob>(`/sequence/jobs/${encodeURIComponent(syncJobId)}`);
}

export async function listSyncJobs(params?: {
  status?: SyncJobStatus;
  cursor?: string;
  limit?: number;
}): Promise<SyncJobListResponse> {
  const qs = new URLSearchParams();
  if (params?.status) qs.set("status", params.status);
  if (params?.cursor) qs.set("cursor", params.cursor);
  if (params?.limit !== undefined) qs.set("limit", String(params.limit));
  const q = qs.toString();
  return request<SyncJobListResponse>(`/sequence/jobs${q ? `?${q}` : ""}`);
}

function buildAuthHeaders(): Headers {
  const headers = new Headers();
  const token = localStorage.getItem(TOKEN_KEY);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  headers.set("Content-Type", "application/json");
  return headers;
}
