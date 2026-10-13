export interface ProcessReplayLaunchEvent {
  action: "PROCESS_REPLAY_LAUNCH";
  vin: string;
  sessionId: string;
  timestamp: string;
}

export interface ClusterAlertViewedEvent {
  action: "CLUSTER_ALERT_VIEWED";
  alertId: string;
  clusterId: string;
  sessionId: string;
  timestamp: string;
}

export interface ClusterAlertAcknowledgedEvent {
  action: "CLUSTER_ALERT_ACKNOWLEDGED";
  alertId: string;
  clusterId: string;
  sessionId: string;
  timestamp: string;
}

function generateSessionId(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

// One session ID per page load — not persisted across reloads.
let _sessionId: string = generateSessionId();

export function getSessionId(): string {
  return _sessionId;
}

/** Reset session — used in tests to verify instance isolation. */
export function resetAdoptionSession(): void {
  _sessionId = generateSessionId();
}

const COLLECTOR_URL =
  (import.meta as unknown as { env?: Record<string, string> }).env?.VITE_OTLP_ENDPOINT ??
  "/api/v1/ingest/events";

function emit(event: ProcessReplayLaunchEvent | ClusterAlertViewedEvent | ClusterAlertAcknowledgedEvent): void {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("ifos:adoption", { detail: event }));
  }

  if (typeof fetch !== "undefined") {
    void fetch(COLLECTOR_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(event),
      keepalive: true,
    }).catch(() => {
      // Fire-and-forget — instrumentation errors must never surface to engineers.
    });
  }
}

export function emitProcessReplayLaunch(vin: string): void {
  emit({
    action: "PROCESS_REPLAY_LAUNCH",
    vin,
    sessionId: _sessionId,
    timestamp: new Date().toISOString(),
  });
}

export function emitClusterAlertViewed(alertId: string, clusterId: string): void {
  emit({
    action: "CLUSTER_ALERT_VIEWED",
    alertId,
    clusterId,
    sessionId: _sessionId,
    timestamp: new Date().toISOString(),
  });
}

export function emitClusterAlertAcknowledged(alertId: string, clusterId: string): void {
  emit({
    action: "CLUSTER_ALERT_ACKNOWLEDGED",
    alertId,
    clusterId,
    sessionId: _sessionId,
    timestamp: new Date().toISOString(),
  });
}
