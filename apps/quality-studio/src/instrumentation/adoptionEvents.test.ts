import { describe, it, expect, beforeEach, vi } from "vitest";
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

// Mock the fetch API
globalThis.fetch = vi.fn();

describe("adoptionEvents — event emission", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetAdoptionSession();
  });

  it("emitProcessReplayLaunch emits event with correct shape", async () => {
    emitProcessReplayLaunch("WBSDE4C07NCK19847");

    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/api/v1/ingest/events",
      expect.objectContaining({
        method: "POST",
        headers: { "Content-Type": "application/json" },
      }),
    );

    const callArgs = (globalThis.fetch as any).mock.calls[0];
    const body = JSON.parse(callArgs[1].body);
    expect(body).toMatchObject({
      action: "PROCESS_REPLAY_LAUNCH",
      vin: "WBSDE4C07NCK19847",
    });
    expect(body.sessionId).toBeTruthy();
    expect(body.timestamp).toBeTruthy();
  });

  it("emitDefectGenomeQuery emits event with correct shape", async () => {
    emitDefectGenomeQuery({
      userId: "kb-entra-001",
      role: "QUALITY_GATE_VALIDATOR",
      defectClass: "Crater",
    });

    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/api/v1/ingest/events",
      expect.objectContaining({
        method: "POST",
        headers: { "Content-Type": "application/json" },
      }),
    );

    const callArgs = (globalThis.fetch as any).mock.calls[0];
    const body = JSON.parse(callArgs[1].body);
    expect(body).toMatchObject({
      action: "DEFECT_GENOME_QUERY",
      userId: "kb-entra-001",
      role: "QUALITY_GATE_VALIDATOR",
      defectClass: "Crater",
    });
    expect(body.sessionId).toBeTruthy();
    expect(body.timestamp).toBeTruthy();
  });

  it("emitRootCauseAssigned emits event with correct shape", async () => {
    emitRootCauseAssigned({
      userId: "kb-entra-001",
      role: "QUALITY_GATE_VALIDATOR",
      clusterId: "DG-0042",
    });

    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/api/v1/ingest/events",
      expect.objectContaining({
        method: "POST",
        headers: { "Content-Type": "application/json" },
      }),
    );

    const callArgs = (globalThis.fetch as any).mock.calls[0];
    const body = JSON.parse(callArgs[1].body);
    expect(body).toMatchObject({
      action: "ROOT_CAUSE_ASSIGNED",
      userId: "kb-entra-001",
      role: "QUALITY_GATE_VALIDATOR",
      clusterId: "DG-0042",
    });
    expect(body.timestamp).toBeTruthy();
  });

  it("emitCertificateStudioOpen emits event with correct shape", async () => {
    emitCertificateStudioOpen({
      userId: "kb-entra-001",
      role: "QUALITY_GATE_VALIDATOR",
      vin: "WBSDE4C07NCK19847",
    });

    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/api/v1/ingest/events",
      expect.objectContaining({
        method: "POST",
        headers: { "Content-Type": "application/json" },
      }),
    );

    const callArgs = (globalThis.fetch as any).mock.calls[0];
    const body = JSON.parse(callArgs[1].body);
    expect(body).toMatchObject({
      action: "CERTIFICATE_STUDIO_OPEN",
      userId: "kb-entra-001",
      role: "QUALITY_GATE_VALIDATOR",
      vin: "WBSDE4C07NCK19847",
    });
    expect(body.sessionId).toBeTruthy();
    expect(body.timestamp).toBeTruthy();
  });

  it("emitCertify emits event with fabricTxHash", async () => {
    emitCertify({
      userId: "kb-entra-001",
      role: "QUALITY_GATE_VALIDATOR",
      vin: "WBSDE4C07NCK19847",
      fabricTxHash: "a3f9c2d1e8b047bf2e1d5f8a3c9d6b2e",
    });

    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/api/v1/ingest/events",
      expect.objectContaining({
        method: "POST",
        headers: { "Content-Type": "application/json" },
      }),
    );

    const callArgs = (globalThis.fetch as any).mock.calls[0];
    const body = JSON.parse(callArgs[1].body);
    expect(body).toMatchObject({
      action: "CERTIFY",
      userId: "kb-entra-001",
      role: "QUALITY_GATE_VALIDATOR",
      vin: "WBSDE4C07NCK19847",
      fabricTxHash: "a3f9c2d1e8b047bf2e1d5f8a3c9d6b2e",
    });
    expect(body.sessionId).toBeTruthy();
    expect(body.timestamp).toBeTruthy();
  });

  it("emitLogin emits event with LOGIN action type", async () => {
    emitLogin({
      userId: "kb-entra-001",
      role: "QUALITY_GATE_VALIDATOR",
    });

    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/api/v1/ingest/events",
      expect.objectContaining({
        method: "POST",
        headers: { "Content-Type": "application/json" },
      }),
    );

    const callArgs = (globalThis.fetch as any).mock.calls[0];
    const body = JSON.parse(callArgs[1].body);
    expect(body).toMatchObject({
      action: "LOGIN",
      userId: "kb-entra-001",
      role: "QUALITY_GATE_VALIDATOR",
    });
    expect(body.sessionId).toBeTruthy();
    expect(body.timestamp).toBeTruthy();
  });
});

describe("adoptionEvents — session tracking", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetAdoptionSession();
  });

  it("all events in a session share the same sessionId", () => {
    const sessionId1 = getSessionId();
    emitProcessReplayLaunch("VIN1");
    const sessionId2 = getSessionId();
    emitDefectGenomeQuery({
      userId: "user-1",
      role: "QUALITY_GATE_VALIDATOR",
      defectClass: "Crater",
    });
    const sessionId3 = getSessionId();

    expect(sessionId1).toBe(sessionId2);
    expect(sessionId2).toBe(sessionId3);

    // Verify both events used the same sessionId
    const calls = (globalThis.fetch as any).mock.calls;
    const body1 = JSON.parse(calls[0][1].body);
    const body2 = JSON.parse(calls[1][1].body);
    expect(body1.sessionId).toBe(body2.sessionId);
  });

  it("resetAdoptionSession generates a new session ID", () => {
    const oldSessionId = getSessionId();
    resetAdoptionSession();
    const newSessionId = getSessionId();

    expect(oldSessionId).not.toBe(newSessionId);
    expect(newSessionId).toBeTruthy();
  });
});

describe("adoptionEvents — login-only session exclusion", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetAdoptionSession();
  });

  it("LOGIN event has action type that does not match meaningful action types", () => {
    const meaningfulActions = [
      "PROCESS_REPLAY_LAUNCH",
      "DEFECT_GENOME_QUERY",
      "ROOT_CAUSE_ASSIGNED",
      "CERTIFICATE_STUDIO_OPEN",
      "CERTIFY",
    ];

    emitLogin({
      userId: "kb-entra-001",
      role: "QUALITY_GATE_VALIDATOR",
    });

    const callArgs = (globalThis.fetch as any).mock.calls[0];
    const body = JSON.parse(callArgs[1].body);

    expect(meaningfulActions).not.toContain(body.action);
    expect(body.action).toBe("LOGIN");
  });
});
