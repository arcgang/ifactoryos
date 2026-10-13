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
  CorrectiveActionRequest,
  CorrectiveActionResult,
  DefectClass,
  DefectCluster,
  ClusterStatus,
  ModelFeedbackRequest,
  ModelFeedbackAck,
} from "../types/defectGenome";

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

export async function getDefectGenomeAlerts(params: {
  status?: string;
  cursor?: string;
  limit?: number;
} = {}): Promise<AlertListResponse> {
  const q = new URLSearchParams();
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
  return request<AlertListResponse>(`/defect-genome/alerts${qs}`);
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
  return request<ClusterListResponse>(`/defect/clusters${qs}`);
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
