import type {
  ThreadCompletenessResponse,
  VinThreadResponse,
} from "../types/digitalThread";

const TOKEN_KEY = "qs-token";

const baseUrl = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
const API_BASE = "/api/v1";

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

  const res = await fetch(`${baseUrl}${API_BASE}${path}`, {
    ...options,
    headers,
    signal: options.signal,
  });

  if (res.status === 401) {
    localStorage.removeItem(TOKEN_KEY);
    redirectToLogin();
    throw new Error("Unauthorized");
  }

  if (!res.ok) {
    const text = await res.text();
    let message = text || "Request failed";
    try {
      const body = JSON.parse(text) as { message?: string; code?: string };
      if (body.message) message = body.message;
    } catch {
      // use raw text
    }
    const err = new Error(message);
    (err as Error & { status: number }).status = res.status;
    throw err;
  }

  if (res.status === 204) return null as T;
  return res.json() as Promise<T>;
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
