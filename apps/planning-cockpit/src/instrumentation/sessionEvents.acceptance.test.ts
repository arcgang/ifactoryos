/**
 * Acceptance tests — US-019 · Session Event Emission
 *
 * Covers:
 *   AC-1  Every action type (login, optimizer_run, override_applied, publish_confirmed)
 *         emits an event to the instrumentation layer within 1 second, containing
 *         userId, actionType, sessionId, and timestamp.
 *   AC-3  The session_end event carries sessionDurationSeconds so the Gate 1 WAU
 *         query can exclude sessions < 10 min without a meaningful action.
 *
 * Seam under test: the module-level session state in sessionEvents.ts is shared
 * across App.tsx (calls initSession + emitLoginEvent), SequenceBuilderPage.tsx
 * (calls emitOptimizerRunEvent + emitOverrideAppliedEvent), and
 * ConstraintAlertPanel.tsx (calls emitPublishConfirmedEvent).  Each task's unit
 * tests cover the individual call-sites; these tests verify the cross-component
 * state contract and the end-to-end payload reaching the instrumentation layer.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  emitLoginEvent,
  emitOptimizerRunEvent,
  emitOverrideAppliedEvent,
  emitPublishConfirmedEvent,
  emitSessionEndEvent,
  getSessionId,
  initSession,
  resetSession,
} from "./sessionEvents";
import type { SessionEvent } from "./sessionEvents";

// ─── Helpers ────────────────────────────────────────────────────────────────

/** Decode the JSON body passed to the nth fetch call. */
function fetchBody(
  calls: Parameters<typeof globalThis.fetch>[],
  index: number,
): SessionEvent {
  const opts = calls[index][1] as RequestInit;
  return JSON.parse(opts.body as string) as SessionEvent;
}

/** Return the first fetch call whose body has the given actionType. */
function findFetchCall(
  calls: Parameters<typeof globalThis.fetch>[],
  actionType: string,
): SessionEvent | undefined {
  for (const call of calls) {
    const opts = call[1] as RequestInit;
    try {
      const body = JSON.parse(opts.body as string) as SessionEvent;
      if (body.actionType === actionType) return body;
    } catch {
      // ignore non-JSON calls
    }
  }
  return undefined;
}

// ─── Setup ──────────────────────────────────────────────────────────────────

beforeEach(() => {
  resetSession();
  vi.spyOn(globalThis, "fetch").mockResolvedValue({
    ok: true,
    status: 200,
    json: () => Promise.resolve({}),
    text: () => Promise.resolve(""),
  } as Response);
});

// ─── AC-1 — all four action types emit to the instrumentation layer ──────────

describe("AC-1 — login emits to the instrumentation layer with userId, actionType, sessionId, timestamp", () => {
  it("emitLoginEvent posts a 'login' event body to fetch synchronously (within 1 second)", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    initSession("planner_km_munich");
    const sessionId = getSessionId();

    const beforeMs = Date.now();
    emitLoginEvent("planner_km_munich");

    // fetch must be called synchronously — no setTimeout in the dispatch path
    expect(fetchSpy.mock.calls.length).toBeGreaterThan(0);

    const payload = findFetchCall(fetchSpy.mock.calls, "login");
    expect(payload).toBeDefined();
    expect(payload!.userId).toBe("planner_km_munich");
    expect(payload!.actionType).toBe("login");
    expect(payload!.sessionId).toBe(sessionId);
    expect(typeof payload!.timestamp).toBe("string");
    // Emission lag must be under 1 second
    expect(new Date(payload!.timestamp).getTime() - beforeMs).toBeLessThan(1000);
  });
});

describe("AC-1 — optimizer_run emits to the instrumentation layer with userId, actionType, sessionId, timestamp", () => {
  it("emitOptimizerRunEvent posts an 'optimizer_run' event body to fetch synchronously (within 1 second)", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    initSession("planner_km_munich");
    const sessionId = getSessionId();

    const beforeMs = Date.now();
    emitOptimizerRunEvent();

    expect(fetchSpy).toHaveBeenCalledOnce();
    const payload = fetchBody(fetchSpy.mock.calls, 0);
    expect(payload.userId).toBe("planner_km_munich");
    expect(payload.actionType).toBe("optimizer_run");
    expect(payload.sessionId).toBe(sessionId);
    expect(typeof payload.timestamp).toBe("string");
    expect(new Date(payload.timestamp).getTime() - beforeMs).toBeLessThan(1000);
  });
});

describe("AC-1 — override_applied emits to the instrumentation layer with userId, actionType, sessionId, timestamp", () => {
  it("emitOverrideAppliedEvent posts an 'override_applied' event body to fetch synchronously (within 1 second)", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    initSession("planner_km_munich");
    const sessionId = getSessionId();

    const beforeMs = Date.now();
    emitOverrideAppliedEvent();

    expect(fetchSpy).toHaveBeenCalledOnce();
    const payload = fetchBody(fetchSpy.mock.calls, 0);
    expect(payload.userId).toBe("planner_km_munich");
    expect(payload.actionType).toBe("override_applied");
    expect(payload.sessionId).toBe(sessionId);
    expect(typeof payload.timestamp).toBe("string");
    expect(new Date(payload.timestamp).getTime() - beforeMs).toBeLessThan(1000);
  });
});

describe("AC-1 — publish_confirmed emits to the instrumentation layer with userId, actionType, sessionId, timestamp", () => {
  it("emitPublishConfirmedEvent posts a 'publish_confirmed' event body to fetch synchronously (within 1 second)", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    initSession("planner_km_munich");
    const sessionId = getSessionId();

    const beforeMs = Date.now();
    emitPublishConfirmedEvent();

    expect(fetchSpy).toHaveBeenCalledOnce();
    const payload = fetchBody(fetchSpy.mock.calls, 0);
    expect(payload.userId).toBe("planner_km_munich");
    expect(payload.actionType).toBe("publish_confirmed");
    expect(payload.sessionId).toBe(sessionId);
    expect(typeof payload.timestamp).toBe("string");
    expect(new Date(payload.timestamp).getTime() - beforeMs).toBeLessThan(1000);
  });
});

describe("AC-1 — userId initialised by App.tsx (initSession) propagates through SequenceBuilderPage and ConstraintAlertPanel", () => {
  it("all events emitted in a session share the userId set by initSession and the same sessionId", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    // App.tsx mounts: initialises session and emits login
    initSession("planner_km_munich");
    emitLoginEvent("planner_km_munich");
    fetchSpy.mockClear();

    // SequenceBuilderPage: optimizer run
    emitOptimizerRunEvent();
    const runPayload = fetchBody(fetchSpy.mock.calls, 0);
    expect(runPayload.userId).toBe("planner_km_munich");
    fetchSpy.mockClear();

    // SequenceBuilderPage: override applied
    emitOverrideAppliedEvent();
    const overridePayload = fetchBody(fetchSpy.mock.calls, 0);
    expect(overridePayload.userId).toBe("planner_km_munich");
    fetchSpy.mockClear();

    // ConstraintAlertPanel: publish confirmed
    emitPublishConfirmedEvent();
    const publishPayload = fetchBody(fetchSpy.mock.calls, 0);
    expect(publishPayload.userId).toBe("planner_km_munich");

    // All three must share the session ID established at login
    expect(runPayload.sessionId).toBe(overridePayload.sessionId);
    expect(overridePayload.sessionId).toBe(publishPayload.sessionId);
  });
});

describe("AC-1 — events are dispatched to an adoption/ingest endpoint, not silently dropped", () => {
  it("fetch URL contains 'ingest' or 'event' confirming the instrumentation endpoint is reached", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    initSession("planner_km_munich");
    emitOptimizerRunEvent();

    const [url] = fetchSpy.mock.calls[0];
    expect(String(url)).toMatch(/ingest|event/i);
  });
});

describe("AC-1 + AC-3 — event payload field names match Properties_d keys consumed by Gate 1 KQL queries", () => {
  it("action event payload contains actionType, userId, sessionId, timestamp at the top level (matching KQL Properties_d keys)", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    initSession("planner_km_munich");
    emitOptimizerRunEvent();

    const payload = JSON.parse(
      (fetchSpy.mock.calls[0][1] as RequestInit).body as string,
    ) as Record<string, unknown>;
    // KQL reads: Properties_d["actionType"], ["userId"], ["sessionId"], ["timestamp"]
    expect("actionType" in payload).toBe(true);
    expect("userId" in payload).toBe(true);
    expect("sessionId" in payload).toBe(true);
    expect("timestamp" in payload).toBe(true);
  });

  it("session_end payload contains sessionDurationSeconds at the top level (matching KQL Properties_d['sessionDurationSeconds'])", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    initSession("planner_km_munich");
    emitSessionEndEvent();

    const payload = JSON.parse(
      (fetchSpy.mock.calls[0][1] as RequestInit).body as string,
    ) as Record<string, unknown>;
    // KQL reads: Properties_d["sessionDurationSeconds"] for WAU exclusion
    expect("sessionDurationSeconds" in payload).toBe(true);
  });
});

// ─── AC-3 — session_end carries sessionDurationSeconds for WAU exclusion ─────

describe("AC-3 — session_end event emits sessionDurationSeconds as a number", () => {
  it("emitSessionEndEvent posts a 'session_end' body with a numeric sessionDurationSeconds field", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    initSession("planner_km_munich");
    emitSessionEndEvent();

    expect(fetchSpy).toHaveBeenCalledOnce();
    const payload = fetchBody(fetchSpy.mock.calls, 0);
    expect(payload.actionType).toBe("session_end");
    expect(typeof payload.sessionDurationSeconds).toBe("number");
  });
});

describe("AC-3 — a session started and immediately ended has sessionDurationSeconds < 600 (< 10 min)", () => {
  it("sessionDurationSeconds is less than 600 when the session was started just now without meaningful actions", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    initSession("planner_km_munich");
    // End immediately — duration is well under 10 minutes
    emitSessionEndEvent();

    const payload = fetchBody(fetchSpy.mock.calls, 0);
    expect(payload.sessionDurationSeconds!).toBeLessThan(600);
  });
});

describe("AC-3 — session_start event carries sessionStartedAt for server-side duration calculation", () => {
  it("emitLoginEvent posts a 'session_start' body with a parseable ISO sessionStartedAt timestamp", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    initSession("planner_km_munich");
    emitLoginEvent("planner_km_munich");

    const payload = findFetchCall(fetchSpy.mock.calls, "session_start");
    expect(payload).toBeDefined();
    expect(typeof payload!.sessionStartedAt).toBe("string");
    expect(new Date(payload!.sessionStartedAt!).getTime()).toBeGreaterThan(0);
  });
});
