import type {
  ProcessReplayResponse,
  ThreadCompletenessResponse,
  ThreadSnapshotResponse,
  VinThreadResponse,
} from "../types/processReplay";

const BASE_URL =
  (import.meta as unknown as { env?: Record<string, string> }).env?.VITE_API_URL ?? "";
const TOKEN_KEY = "qs-auth-token";

class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

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

export { ApiError };

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
): Promise<ThreadCompletenessResponse> {
  return request<ThreadCompletenessResponse>(
    `/vin/${encodeURIComponent(vin)}/thread/completeness`,
  );
}
