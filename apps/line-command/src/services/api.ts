/** Central API client — base URL, auth header, 403 handling. */

const API_BASE = import.meta.env.VITE_API_URL ?? "";
const TOKEN_KEY = "lc_access_token";

// ---------------------------------------------------------------------------
// Andon alert types
// ---------------------------------------------------------------------------

export interface WeldTracePoint {
  timestamp: string;
  value: number;
}

export interface WeldTrace {
  weld_current_a: WeldTracePoint[];
  weld_voltage_v: WeldTracePoint[];
}

export type AlertStatus = "open" | "acknowledged" | "resolved" | "false_positive";

export interface AndonAlertResponse {
  alertId: string;
  stationId: string;
  lineId: string;
  alertType: "predictive" | "reactive";
  predictedFailureType: string;
  minutesToPredictedFailure: number | null;
  recommendedAction: string;
  status: AlertStatus;
  detectedAt: string;
  correlationId: string;
  weldTrace: WeldTrace;
  thresholdCrossingAt: string;
  thresholdValue: number | null;
}

export type DispatchDisposition = "resolved" | "escalated" | "monitoring";

export interface AcknowledgeRequest {
  disposition: DispatchDisposition;
  note?: string;
}

export interface AcknowledgeResponse {
  alertId: string;
  status: AlertStatus;
  acknowledgedAt: string;
  eventId: string;
}

export class ForbiddenError extends Error {
  constructor(
    public readonly resource: string,
    public readonly userId: string,
  ) {
    super(`403 Forbidden: ${resource}`);
    this.name = "ForbiddenError";
  }
}

async function emitUnauthorisedAuditEvent(
  resource: string,
  userId: string,
): Promise<void> {
  const token = localStorage.getItem(TOKEN_KEY);
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  try {
    await fetch(`${API_BASE}/api/v1/audit/event`, {
      method: "POST",
      headers,
      body: JSON.stringify({
        userId,
        action: "unauthorized_access_attempt",
        resource,
        console: "line-command",
        timestamp: new Date().toISOString(),
      }),
    });
  } catch {
    // best-effort — do not block the 403 surface
  }
}

export async function apiRequest<T>(
  path: string,
  options: RequestInit & { userId?: string } = {},
): Promise<T> {
  const token = localStorage.getItem(TOKEN_KEY);
  const headers = new Headers(options.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const { userId, ...fetchOptions } = options;
  const res = await fetch(`${API_BASE}${path}`, { ...fetchOptions, headers });

  if (res.status === 403) {
    const effectiveUserId = userId ?? "unknown";
    await emitUnauthorisedAuditEvent(path, effectiveUserId);
    throw new ForbiddenError(path, effectiveUserId);
  }

  if (!res.ok) {
    throw new Error(`Request failed: ${res.status} ${res.statusText}`);
  }

  if (res.status === 204) return null as T;
  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Andon alert API
// ---------------------------------------------------------------------------

export function getAndonAlert(alertId: string, userId?: string): Promise<AndonAlertResponse> {
  return apiRequest<AndonAlertResponse>(`/api/v1/andon/alerts/${encodeURIComponent(alertId)}`, { userId });
}

export function acknowledgeAndonAlert(
  alertId: string,
  body: AcknowledgeRequest,
  userId?: string,
): Promise<AcknowledgeResponse> {
  return apiRequest<AcknowledgeResponse>(
    `/api/v1/andon/alerts/${encodeURIComponent(alertId)}/acknowledge`,
    {
      method: "POST",
      body: JSON.stringify(body),
      userId,
    },
  );
}
