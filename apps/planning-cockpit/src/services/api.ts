import type { ConstraintThreshold, ThresholdListResponse } from "../types/constraints";

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
