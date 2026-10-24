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
}
export interface DefectGenomeQueryEvent {
  action: "DEFECT_GENOME_QUERY";
  userId: string;
  role: string;
  defectClass: string;
  sessionId: string;
  timestamp: string;
}

export interface ClusterAlertAcknowledgedEvent {
  action: "CLUSTER_ALERT_ACKNOWLEDGED";
  alertId: string;
  clusterId: string;
  sessionId: string;
}
export interface RootCauseAssignedEvent {
  action: "ROOT_CAUSE_ASSIGNED";
  userId: string;
  role: string;
  clusterId: string;
  timestamp: string;
}

export interface CertificateStudioOpenEvent {
  action: "CERTIFICATE_STUDIO_OPEN";
  userId: string;
  role: string;
  vin: string;
  sessionId: string;
  timestamp: string;
}

export interface CertifyEvent {
  action: "CERTIFY";
  userId: string;
  role: string;
  vin: string;
  fabricTxHash: string;
  sessionId: string;
  timestamp: string;
}

export interface LoginEvent {
  action: "LOGIN";
  userId: string;
  role: string;
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

function emit(event: ProcessReplayLaunchEvent | DefectGenomeQueryEvent | RootCauseAssignedEvent | ClusterAlertViewedEvent | ClusterAlertAcknowledgedEvent | CertificateStudioOpenEvent | CertifyEvent | LoginEvent): void {
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
  });
}

export function emitDefectGenomeQuery(params: {
  userId: string;
  role: string;
  defectClass: string;
}): void {
  emit({
    action: "DEFECT_GENOME_QUERY",
    userId: params.userId,
    role: params.role,
    defectClass: params.defectClass,
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
  });
}

export function emitRootCauseAssigned(params: {
  userId: string;
  role: string;
  clusterId: string;
}): void {
  emit({
    action: "ROOT_CAUSE_ASSIGNED",
    userId: params.userId,
    role: params.role,
    clusterId: params.clusterId,
    timestamp: new Date().toISOString(),
  });
}

export function emitCertificateStudioOpen(params: {
  userId: string;
  role: string;
  vin: string;
}): void {
  emit({
    action: "CERTIFICATE_STUDIO_OPEN",
    userId: params.userId,
    role: params.role,
    vin: params.vin,
    sessionId: _sessionId,
    timestamp: new Date().toISOString(),
  });
}

export function emitCertify(params: {
  userId: string;
  role: string;
  vin: string;
  fabricTxHash: string;
}): void {
  emit({
    action: "CERTIFY",
    userId: params.userId,
    role: params.role,
    vin: params.vin,
    fabricTxHash: params.fabricTxHash,
    sessionId: _sessionId,
    timestamp: new Date().toISOString(),
  });
}

export function emitLogin(params: {
  userId: string;
  role: string;
}): void {
  emit({
    action: "LOGIN",
    userId: params.userId,
    role: params.role,
    sessionId: _sessionId,
    timestamp: new Date().toISOString(),
  });
}
