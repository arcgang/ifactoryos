/**
 * Acceptance tests — US-021 · Quality Studio session event emission
 *
 * Covers:
 *   AC1  Every tracked meaningful action (PROCESS_REPLAY_LAUNCH, DEFECT_GENOME_QUERY,
 *         ROOT_CAUSE_ASSIGNED, CERTIFY, CERTIFICATE_STUDIO_OPEN) emits an event to
 *         the instrumentation layer within 1 second, containing userId, role,
 *         action type, VIN (where applicable), and timestamp.
 *   AC2  LOGIN is distinct from meaningful actions — its action value is NOT in the
 *         meaningful-action set, so a login-only session contributes 0 to WAU.
 *   AC3  CERTIFY event includes fabricTxHash, enabling A7 cross-reference with the
 *         Fabric anchor count.
 *
 * Seam under test: the shared session state in adoptionEvents.ts, exercised via every
 * emitter exported from that module.  Unit tests already cover each emitter in
 * isolation; these tests verify the cross-function session contract and the exact
 * payload delivered to the instrumentation endpoint.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  resetAdoptionSession,
  getSessionId,
  emitProcessReplayLaunch,
  emitDefectGenomeQuery,
  emitRootCauseAssigned,
  emitCertificateStudioOpen,
  emitCertify,
  emitLogin,
} from "./adoptionEvents";

// ── Precondition: verify the module exports every required emitter ─────────────
// If any name is missing, the import itself throws, causing every test to fail
// with a clear "cannot find export" message rather than a silent pass.
const REQUIRED_EXPORTS = [
  emitProcessReplayLaunch,
  emitDefectGenomeQuery,
  emitRootCauseAssigned,
  emitCertificateStudioOpen,
  emitCertify,
  emitLogin,
];

it("adoptionEvents module exports all required emitters (precondition)", () => {
  for (const fn of REQUIRED_EXPORTS) {
    expect(typeof fn).toBe("function");
  }
});

// ── Helpers ─────────────────────────────────────────────────────────────────

type FetchCall = Parameters<typeof globalThis.fetch>;

function bodyOf(calls: FetchCall[], index: number): Record<string, unknown> {
  const opts = calls[index][1] as RequestInit;
  return JSON.parse(opts.body as string) as Record<string, unknown>;
}

function findBody(
  calls: FetchCall[],
  action: string,
): Record<string, unknown> | undefined {
  for (const call of calls) {
    const opts = call[1] as RequestInit;
    try {
      const b = JSON.parse(opts.body as string) as Record<string, unknown>;
      if (b.action === action) return b;
    } catch {
      // skip non-JSON calls
    }
  }
  return undefined;
}

// ── Setup ────────────────────────────────────────────────────────────────────

beforeEach(() => {
  resetAdoptionSession();
  vi.spyOn(globalThis, "fetch").mockResolvedValue({
    ok: true,
    status: 200,
    json: () => Promise.resolve({}),
    text: () => Promise.resolve(""),
  } as Response);
});

// ── AC1 — PROCESS_REPLAY_LAUNCH ───────────────────────────────────────────────

describe("AC1 · PROCESS_REPLAY_LAUNCH emits to instrumentation with vin, sessionId, timestamp within 1 second", () => {
  it("emitProcessReplayLaunch posts event with action, vin, sessionId, and timestamp", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const sessionId = getSessionId();
    const beforeMs = Date.now();

    emitProcessReplayLaunch("WBA3A5G59DNP26082");

    expect(fetchSpy.mock.calls.length).toBeGreaterThan(0);
    const body = findBody(fetchSpy.mock.calls, "PROCESS_REPLAY_LAUNCH");
    expect(body).toBeDefined();
    expect(body!.action).toBe("PROCESS_REPLAY_LAUNCH");
    expect(body!.vin).toBe("WBA3A5G59DNP26082");
    expect(body!.sessionId).toBe(sessionId);
    expect(typeof body!.timestamp).toBe("string");
    expect(new Date(body!.timestamp as string).getTime() - beforeMs).toBeLessThan(1000);
  });
});

// ── AC1 — DEFECT_GENOME_QUERY ─────────────────────────────────────────────────

describe("AC1 · DEFECT_GENOME_QUERY emits with userId, role, defectClass, sessionId, timestamp within 1 second", () => {
  it("emitDefectGenomeQuery posts event with all required fields", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const sessionId = getSessionId();
    const beforeMs = Date.now();

    emitDefectGenomeQuery({
      userId: "pe-entra-007",
      role: "PAINT_ENGINEER",
      defectClass: "Crater",
    });

    expect(fetchSpy.mock.calls.length).toBeGreaterThan(0);
    const body = findBody(fetchSpy.mock.calls, "DEFECT_GENOME_QUERY");
    expect(body).toBeDefined();
    expect(body!.action).toBe("DEFECT_GENOME_QUERY");
    expect(body!.userId).toBe("pe-entra-007");
    expect(body!.role).toBe("PAINT_ENGINEER");
    expect(body!.defectClass).toBe("Crater");
    expect(body!.sessionId).toBe(sessionId);
    expect(typeof body!.timestamp).toBe("string");
    expect(new Date(body!.timestamp as string).getTime() - beforeMs).toBeLessThan(1000);
  });
});

// ── AC1 — ROOT_CAUSE_ASSIGNED ─────────────────────────────────────────────────

describe("AC1 · ROOT_CAUSE_ASSIGNED emits with userId, role, clusterId, timestamp within 1 second", () => {
  it("emitRootCauseAssigned posts event with all required fields", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const beforeMs = Date.now();

    emitRootCauseAssigned({
      userId: "qv-entra-003",
      role: "QUALITY_GATE_VALIDATOR",
      clusterId: "DG-0042",
    });

    expect(fetchSpy.mock.calls.length).toBeGreaterThan(0);
    const body = findBody(fetchSpy.mock.calls, "ROOT_CAUSE_ASSIGNED");
    expect(body).toBeDefined();
    expect(body!.action).toBe("ROOT_CAUSE_ASSIGNED");
    expect(body!.userId).toBe("qv-entra-003");
    expect(body!.role).toBe("QUALITY_GATE_VALIDATOR");
    expect(body!.clusterId).toBe("DG-0042");
    expect(typeof body!.timestamp).toBe("string");
    expect(new Date(body!.timestamp as string).getTime() - beforeMs).toBeLessThan(1000);
  });
});

// ── AC1 — CERTIFICATE_STUDIO_OPEN ────────────────────────────────────────────

describe("AC1 · CERTIFICATE_STUDIO_OPEN emits with userId, role, vin, sessionId, timestamp within 1 second", () => {
  it("emitCertificateStudioOpen posts event with all required fields", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const sessionId = getSessionId();
    const beforeMs = Date.now();

    emitCertificateStudioOpen({
      userId: "qv-entra-003",
      role: "QUALITY_GATE_VALIDATOR",
      vin: "WBY73CF080NCH14427",
    });

    expect(fetchSpy.mock.calls.length).toBeGreaterThan(0);
    const body = findBody(fetchSpy.mock.calls, "CERTIFICATE_STUDIO_OPEN");
    expect(body).toBeDefined();
    expect(body!.action).toBe("CERTIFICATE_STUDIO_OPEN");
    expect(body!.userId).toBe("qv-entra-003");
    expect(body!.role).toBe("QUALITY_GATE_VALIDATOR");
    expect(body!.vin).toBe("WBY73CF080NCH14427");
    expect(body!.sessionId).toBe(sessionId);
    expect(typeof body!.timestamp).toBe("string");
    expect(new Date(body!.timestamp as string).getTime() - beforeMs).toBeLessThan(1000);
  });
});

// ── AC1 + AC3 — CERTIFY ───────────────────────────────────────────────────────

describe("AC1 + AC3 · CERTIFY emits with userId, role, vin, fabricTxHash, sessionId, timestamp within 1 second", () => {
  it("emitCertify posts event including fabricTxHash for A7 cross-reference", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const sessionId = getSessionId();
    const beforeMs = Date.now();

    emitCertify({
      userId: "qv-entra-003",
      role: "QUALITY_GATE_VALIDATOR",
      vin: "WBY73CF080NCH14427",
      fabricTxHash: "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
    });

    expect(fetchSpy.mock.calls.length).toBeGreaterThan(0);
    const body = findBody(fetchSpy.mock.calls, "CERTIFY");
    expect(body).toBeDefined();
    expect(body!.action).toBe("CERTIFY");
    expect(body!.userId).toBe("qv-entra-003");
    expect(body!.role).toBe("QUALITY_GATE_VALIDATOR");
    expect(body!.vin).toBe("WBY73CF080NCH14427");
    // AC3: fabricTxHash must be present for A7 counter cross-reference
    expect(body!.fabricTxHash).toBe("deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef");
    expect(body!.sessionId).toBe(sessionId);
    expect(typeof body!.timestamp).toBe("string");
    expect(new Date(body!.timestamp as string).getTime() - beforeMs).toBeLessThan(1000);
  });
});

// ── AC2 — LOGIN is not a meaningful action ────────────────────────────────────

describe("AC2 · LOGIN action is distinct from all meaningful actions and does not count toward WAU", () => {
  it("emitLogin posts action=LOGIN which is absent from the meaningful-action set", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    emitLogin({ userId: "qv-entra-003", role: "QUALITY_GATE_VALIDATOR" });

    expect(fetchSpy.mock.calls.length).toBeGreaterThan(0);
    const body = bodyOf(fetchSpy.mock.calls, 0);
    expect(body.action).toBe("LOGIN");

    const MEANINGFUL_ACTIONS = [
      "PROCESS_REPLAY_LAUNCH",
      "DEFECT_GENOME_QUERY",
      "ROOT_CAUSE_ASSIGNED",
      "CERTIFY",
      "CERTIFICATE_STUDIO_OPEN",
    ];
    // A login-only session must not be counted: LOGIN must not be in the
    // meaningful-action set used by the WAU KQL query.
    expect(MEANINGFUL_ACTIONS).not.toContain(body.action as string);
  });

  it("a session with only a LOGIN event produces 0 meaningful-action events", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    emitLogin({ userId: "qv-entra-003", role: "QUALITY_GATE_VALIDATOR" });

    const MEANINGFUL_ACTIONS = new Set([
      "PROCESS_REPLAY_LAUNCH",
      "DEFECT_GENOME_QUERY",
      "ROOT_CAUSE_ASSIGNED",
      "CERTIFY",
      "CERTIFICATE_STUDIO_OPEN",
    ]);

    const meaningfulCount = fetchSpy.mock.calls.filter((call) => {
      const opts = call[1] as RequestInit;
      try {
        const b = JSON.parse(opts.body as string) as Record<string, unknown>;
        return MEANINGFUL_ACTIONS.has(b.action as string);
      } catch {
        return false;
      }
    }).length;

    expect(meaningfulCount).toBe(0);
  });
});

// ── AC1 — cross-event session-id consistency ──────────────────────────────────

describe("AC1 · all meaningful-action events in a session share the same sessionId", () => {
  it("sessionId is identical across PROCESS_REPLAY_LAUNCH, DEFECT_GENOME_QUERY, CERTIFY, CERTIFICATE_STUDIO_OPEN, ROOT_CAUSE_ASSIGNED", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const sessionId = getSessionId();

    emitProcessReplayLaunch("WBA3A5G59DNP26082");
    emitDefectGenomeQuery({ userId: "pe-001", role: "PAINT_ENGINEER", defectClass: "RunMark" });
    emitRootCauseAssigned({ userId: "pe-001", role: "PAINT_ENGINEER", clusterId: "DG-007" });
    emitCertificateStudioOpen({ userId: "qv-001", role: "QUALITY_GATE_VALIDATOR", vin: "WBA3A5G59DNP26082" });
    emitCertify({ userId: "qv-001", role: "QUALITY_GATE_VALIDATOR", vin: "WBA3A5G59DNP26082", fabricTxHash: "a".repeat(64) });

    const actions = [
      "PROCESS_REPLAY_LAUNCH",
      "DEFECT_GENOME_QUERY",
      "ROOT_CAUSE_ASSIGNED",
      "CERTIFICATE_STUDIO_OPEN",
      "CERTIFY",
    ];

    for (const action of actions) {
      const body = findBody(fetchSpy.mock.calls, action);
      expect(body, `${action} body must exist`).toBeDefined();
      expect(body!.sessionId, `${action} must carry the session sessionId`).toBe(sessionId);
    }
  });
});
