/**
 * Adoption instrumentation — emits session events to the observability stack
 * via OTLP/HTTP (proxied through the API gateway) and a window CustomEvent
 * for same-page listeners. All emissions are fire-and-forget: failures are
 * logged but never surface to the planner workflow.
 *
 * Lifecycle events:
 *   session_start  — on login, carries sessionStartedAt
 *   login          — on login, carries userId / sessionId / timestamp
 *   optimizer_run  — after POST /optimize returns
 *   override_applied — after POST /overrides returns 201
 *   publish_confirmed — after POST /sequence/publish returns 200/202
 *   session_end    — on beforeunload / visibilitychange=hidden
 */

export type ActionType =
  | "login"
  | "optimizer_run"
  | "override_applied"
  | "publish_confirmed"
  | "session_start"
  | "session_end";

export interface SessionEvent {
  userId: string;
  actionType: ActionType;
  sessionId: string;
  timestamp: string;
  /** Present on session_start */
  sessionStartedAt?: string;
  /** Present on session_end */
  sessionDurationSeconds?: number;
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

// Session state — generated once per page load, never persisted.
let _sessionId: string = generateSessionId();
let _userId: string = "anonymous";
let _sessionStartedAt: string | null = null;

/**
 * Initialise the session with the authenticated user's ID.
 * Call once after the OIDC token is received.
 */
export function initSession(userId: string): void {
  _userId = userId;
  _sessionStartedAt = new Date().toISOString();
}

/** Exposed for tests to verify that sequential calls produce independent sessions. */
export function resetSession(): void {
  _sessionId = generateSessionId();
  _userId = "anonymous";
  _sessionStartedAt = null;
}

export function getSessionId(): string {
  return _sessionId;
}

const COLLECTOR_URL =
  typeof import.meta !== "undefined" && import.meta.env
    ? (import.meta.env.VITE_OTLP_ENDPOINT ?? "/api/v1/ingest/events")
    : "/api/v1/ingest/events";

function dispatch(event: SessionEvent): void {
  // Same-page CustomEvent for UI components that show the live event log.
  if (typeof window !== "undefined") {
    window.dispatchEvent(
      new CustomEvent("ifos:adoption", { detail: event }),
    );
  }

  // OTLP/HTTP POST to the observability-stack collector (via API gateway proxy).
  // Fire-and-forget: never block the planner flow.
  if (typeof fetch !== "undefined") {
    void fetch(COLLECTOR_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(event),
      // keepalive so the request survives page unload (session_end path)
      keepalive: true,
    }).catch(() => {
      // Silently swallow — instrumentation errors must never surface to planners.
    });
  }
}

function buildEvent(
  actionType: ActionType,
  extra: Partial<SessionEvent> = {},
): SessionEvent {
  return {
    userId: _userId,
    actionType,
    sessionId: _sessionId,
    timestamp: new Date().toISOString(),
    ...extra,
  };
}

export function emitLoginEvent(userId?: string): void {
  if (userId) _userId = userId;
  dispatch(buildEvent("session_start", { sessionStartedAt: new Date().toISOString() }));
  dispatch(buildEvent("login"));
}

export function emitOptimizerRunEvent(): void {
  dispatch(buildEvent("optimizer_run"));
}

export function emitOverrideAppliedEvent(): void {
  dispatch(buildEvent("override_applied"));
}

export function emitPublishConfirmedEvent(): void {
  dispatch(buildEvent("publish_confirmed"));
}

export function emitSessionEndEvent(): void {
  const durationSeconds =
    _sessionStartedAt != null
      ? Math.round((Date.now() - new Date(_sessionStartedAt).getTime()) / 1000)
      : 0;
  dispatch(buildEvent("session_end", { sessionDurationSeconds: durationSeconds }));
}
