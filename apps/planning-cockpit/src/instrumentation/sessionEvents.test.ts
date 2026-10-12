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

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function collectEvents(): { events: SessionEvent[]; cleanup: () => void } {
  const events: SessionEvent[] = [];
  const handler = (e: Event) => {
    events.push((e as CustomEvent<SessionEvent>).detail);
  };
  window.addEventListener("ifos:adoption", handler);
  return {
    events,
    cleanup: () => window.removeEventListener("ifos:adoption", handler),
  };
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

beforeEach(() => {
  resetSession();
  vi.spyOn(globalThis, "fetch").mockResolvedValue({
    ok: true,
    status: 200,
    json: () => Promise.resolve({}),
    text: () => Promise.resolve(""),
  } as Response);
});

// ---------------------------------------------------------------------------
// login
// ---------------------------------------------------------------------------

describe("emitLoginEvent", () => {
  it("emits a login event with userId, actionType, sessionId, and timestamp", () => {
    const { events, cleanup } = collectEvents();

    emitLoginEvent("planner_km_munich");

    const loginEvents = events.filter((e) => e.actionType === "login");
    expect(loginEvents.length).toBe(1);
    const ev = loginEvents[0];
    expect(ev.userId).toBe("planner_km_munich");
    expect(ev.actionType).toBe("login");
    expect(ev.sessionId).toBeTruthy();
    expect(typeof ev.sessionId).toBe("string");
    expect(ev.timestamp).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/);

    cleanup();
  });

  it("also emits a session_start event carrying sessionStartedAt", () => {
    const { events, cleanup } = collectEvents();

    emitLoginEvent("planner_km_munich");

    const startEvents = events.filter((e) => e.actionType === "session_start");
    expect(startEvents.length).toBe(1);
    expect(startEvents[0].sessionStartedAt).toBeTruthy();
    expect(startEvents[0].sessionStartedAt).toMatch(/^\d{4}-\d{2}-\d{2}T/);

    cleanup();
  });

  it("sets userId from the argument supplied to emitLoginEvent", () => {
    const { events, cleanup } = collectEvents();

    emitLoginEvent("user_abc");

    const loginEvent = events.find((e) => e.actionType === "login");
    expect(loginEvent?.userId).toBe("user_abc");

    cleanup();
  });
});

// ---------------------------------------------------------------------------
// optimizer_run
// ---------------------------------------------------------------------------

describe("emitOptimizerRunEvent", () => {
  it("emits optimizer_run with userId, actionType, sessionId, and timestamp", () => {
    initSession("planner_tw_berlin");
    const { events, cleanup } = collectEvents();

    emitOptimizerRunEvent();

    expect(events.length).toBe(1);
    const ev = events[0];
    expect(ev.userId).toBe("planner_tw_berlin");
    expect(ev.actionType).toBe("optimizer_run");
    expect(ev.sessionId).toBe(getSessionId());
    expect(ev.timestamp).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/);

    cleanup();
  });
});

// ---------------------------------------------------------------------------
// override_applied
// ---------------------------------------------------------------------------

describe("emitOverrideAppliedEvent", () => {
  it("emits override_applied with userId, actionType, sessionId, and timestamp", () => {
    initSession("planner_sr_munich");
    const { events, cleanup } = collectEvents();

    emitOverrideAppliedEvent();

    expect(events.length).toBe(1);
    const ev = events[0];
    expect(ev.userId).toBe("planner_sr_munich");
    expect(ev.actionType).toBe("override_applied");
    expect(ev.sessionId).toBe(getSessionId());
    expect(ev.timestamp).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/);

    cleanup();
  });
});

// ---------------------------------------------------------------------------
// publish_confirmed
// ---------------------------------------------------------------------------

describe("emitPublishConfirmedEvent", () => {
  it("emits publish_confirmed with userId, actionType, sessionId, and timestamp", () => {
    initSession("planner_km_munich");
    const { events, cleanup } = collectEvents();

    emitPublishConfirmedEvent();

    expect(events.length).toBe(1);
    const ev = events[0];
    expect(ev.userId).toBe("planner_km_munich");
    expect(ev.actionType).toBe("publish_confirmed");
    expect(ev.sessionId).toBe(getSessionId());
    expect(ev.timestamp).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/);

    cleanup();
  });
});

// ---------------------------------------------------------------------------
// session_end
// ---------------------------------------------------------------------------

describe("emitSessionEndEvent", () => {
  it("emits session_end carrying sessionDurationSeconds", () => {
    initSession("planner_km_munich");
    const { events, cleanup } = collectEvents();

    emitSessionEndEvent();

    const endEvents = events.filter((e) => e.actionType === "session_end");
    expect(endEvents.length).toBe(1);
    expect(typeof endEvents[0].sessionDurationSeconds).toBe("number");

    cleanup();
  });
});

// ---------------------------------------------------------------------------
// session isolation — each resetSession() starts a fresh session
// ---------------------------------------------------------------------------

describe("session isolation", () => {
  it("sessionId changes after resetSession", () => {
    const first = getSessionId();
    resetSession();
    const second = getSessionId();
    expect(first).not.toBe(second);
  });

  it("userId resets to anonymous after resetSession", () => {
    initSession("planner_km");
    resetSession();
    const { events, cleanup } = collectEvents();

    emitOptimizerRunEvent();
    expect(events[0].userId).toBe("anonymous");

    cleanup();
  });
});

// ---------------------------------------------------------------------------
// OTLP HTTP dispatch
// ---------------------------------------------------------------------------

describe("OTLP HTTP dispatch", () => {
  it("calls fetch with the event payload for each emitted event", () => {
    const { cleanup } = collectEvents();
    emitOptimizerRunEvent();

    expect(vi.mocked(fetch)).toHaveBeenCalledOnce();
    const [, opts] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(opts.body as string) as SessionEvent;
    expect(body.actionType).toBe("optimizer_run");
    expect(body.userId).toBeTruthy();
    expect(body.sessionId).toBeTruthy();
    expect(body.timestamp).toBeTruthy();

    cleanup();
  });

  it("does not throw when fetch rejects (fire-and-forget)", async () => {
    vi.mocked(fetch).mockRejectedValueOnce(new Error("Network error"));
    const { cleanup } = collectEvents();

    // Should not throw
    expect(() => emitOptimizerRunEvent()).not.toThrow();

    // Allow the rejected promise to settle
    await Promise.resolve();

    cleanup();
  });
});
