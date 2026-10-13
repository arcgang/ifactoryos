import { renderHook, act, waitFor } from "@testing-library/react";
import { describe, expect, it, beforeEach, vi } from "vitest";
import { useHandoverSubmission, type AlertDisposition } from "./useHandoverSubmission";
import { apiRequest } from "../services/api";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("../services/api", () => ({
  apiRequest: vi.fn(),
}));

const mockApiRequest = vi.mocked(apiRequest);

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const baseRequest = {
  shiftId: "SH-20261012-2",
  lineId: "WL-A",
  shiftStartTs: "2026-10-12T14:00:00Z",
  shiftEndTs: "2026-10-12T22:00:00Z",
  alertDispositions: [] as AlertDisposition[],
  oeeDeltaPct: -1.8,
};

const successResponse = {
  handoverId: "HOV-8831",
  submittedAt: "2026-10-12T22:03:14Z",
  submittedByUserId: "SL-4471",
};

// ---------------------------------------------------------------------------
// Setup / teardown
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("useHandoverSubmission", () => {
  it("initializes in idle state", () => {
    const { result } = renderHook(() => useHandoverSubmission());
    expect(result.current.state.kind).toBe("idle");
  });

  it("calls POST /handover with required fields on submit", async () => {
    mockApiRequest.mockResolvedValue(successResponse);

    const { result } = renderHook(() => useHandoverSubmission());

    await act(async () => {
      await result.current.submit(baseRequest);
    });

    const [path, options] = mockApiRequest.mock.calls[0];
    expect(path).toBe("/api/v1/handover");
    expect(options?.method).toBe("POST");

    const body = JSON.parse(options?.body);
    expect(body.shiftId).toBe("SH-20261012-2");
    expect(body.lineId).toBe("WL-A");
    expect(body.shiftStartTs).toBe("2026-10-12T14:00:00Z");
    expect(body.shiftEndTs).toBe("2026-10-12T22:00:00Z");
    expect(body.oeeDeltaPct).toBe(-1.8);
    expect(body.idempotencyKey).toBeTruthy();
  });

  it("generates and includes idempotencyKey in request", async () => {
    mockApiRequest.mockResolvedValue(successResponse);

    const { result } = renderHook(() => useHandoverSubmission());

    await act(async () => {
      await result.current.submit(baseRequest);
    });

    const [, options] = mockApiRequest.mock.calls[0];
    const body = JSON.parse(options?.body);

    // Idempotency key should be a UUID
    expect(body.idempotencyKey).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
    );
  });

  it("uses stable idempotency key across multiple submit attempts", async () => {
    mockApiRequest.mockResolvedValue(successResponse);

    const { result } = renderHook(() => useHandoverSubmission());

    const capturedKeys: string[] = [];

    await act(async () => {
      await result.current.submit(baseRequest);
    });

    let [, options] = mockApiRequest.mock.calls[mockApiRequest.mock.calls.length - 1];
    let body = JSON.parse(options?.body);
    capturedKeys.push(body.idempotencyKey);

    // Second submit attempt
    await act(async () => {
      await result.current.submit(baseRequest);
    });

    [, options] = mockApiRequest.mock.calls[mockApiRequest.mock.calls.length - 1];
    body = JSON.parse(options?.body);
    capturedKeys.push(body.idempotencyKey);

    // Both keys should be identical
    expect(capturedKeys[0]).toBe(capturedKeys[1]);
  });

  it("transitions to success state on 201 response", async () => {
    mockApiRequest.mockResolvedValue(successResponse);

    const { result } = renderHook(() => useHandoverSubmission());

    await act(async () => {
      await result.current.submit(baseRequest);
    });

    expect(result.current.state.kind).toBe("success");
    if (result.current.state.kind === "success") {
      expect(result.current.state.handoverId).toBe("HOV-8831");
      expect(result.current.state.submittedAt).toBe("2026-10-12T22:03:14Z");
    }
  });

  it("transitions to error state with code 409 on duplicate shift", async () => {
    mockApiRequest.mockRejectedValue(new Error("Request failed: 409 Conflict"));

    const { result } = renderHook(() => useHandoverSubmission());

    await act(async () => {
      await result.current.submit(baseRequest);
    });

    expect(result.current.state.kind).toBe("error");
    if (result.current.state.kind === "error") {
      expect(result.current.state.code).toBe("409");
      expect(result.current.state.message).toContain("already submitted");
    }
  });

  it("transitions to error state with code 403 for non-shift-lead", async () => {
    mockApiRequest.mockRejectedValue(new Error("Request failed: 403 Forbidden"));

    const { result } = renderHook(() => useHandoverSubmission());

    await act(async () => {
      await result.current.submit(baseRequest);
    });

    expect(result.current.state.kind).toBe("error");
    if (result.current.state.kind === "error") {
      expect(result.current.state.code).toBe("403");
      expect(result.current.state.message).toContain("Shift Lead");
    }
  });

  it("transitions to error state with code 422 for invalid payload", async () => {
    mockApiRequest.mockRejectedValue(new Error("Request failed: 422 Unprocessable Entity"));

    const { result } = renderHook(() => useHandoverSubmission());

    await act(async () => {
      await result.current.submit(baseRequest);
    });

    expect(result.current.state.kind).toBe("error");
    if (result.current.state.kind === "error") {
      expect(result.current.state.code).toBe("422");
    }
  });

  it("passes through optional fields (heldVins, notes) when provided", async () => {
    mockApiRequest.mockResolvedValue(successResponse);

    const { result } = renderHook(() => useHandoverSubmission());

    const requestWithOptionals = {
      ...baseRequest,
      heldVins: ["WBS11000X0CHX8812", "WBS11000X0CHX8819"],
      notes: "Electrode wear addressed. OEE below target.",
    };

    await act(async () => {
      await result.current.submit(requestWithOptionals);
    });

    const [, options] = mockApiRequest.mock.calls[0];
    const body = JSON.parse(options?.body);

    expect(body.heldVins).toEqual([
      "WBS11000X0CHX8812",
      "WBS11000X0CHX8819",
    ]);
    expect(body.notes).toBe("Electrode wear addressed. OEE below target.");
  });

  it("reset() transitions state back to idle", async () => {
    mockApiRequest.mockResolvedValue(successResponse);

    const { result } = renderHook(() => useHandoverSubmission());

    await act(async () => {
      await result.current.submit(baseRequest);
    });

    expect(result.current.state.kind).toBe("success");

    act(() => {
      result.current.reset();
    });

    expect(result.current.state.kind).toBe("idle");
  });
});
