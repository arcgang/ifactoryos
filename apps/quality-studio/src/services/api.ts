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

export class ApiError extends Error {
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
