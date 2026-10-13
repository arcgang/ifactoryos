import { renderHook, act } from "@testing-library/react";
import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";
import { usePublishSequence } from "./usePublishSequence";
import type { SequencePublishResponse, SyncJob } from "../types/sequence";
import * as api from "../services/api";

vi.mock("../services/api", () => ({
  publishSequence: vi.fn(),
  getSyncJob: vi.fn(),
  listSyncJobs: vi.fn(),
  listConstraintThresholds: vi.fn(),
  upsertConstraintThreshold: vi.fn(),
  getConstraintStreamUrl: vi.fn(() => ""),
}));

const PLAN_ID = "00000000-0000-0000-0000-000000000001";

const STUB_REQUEST = {
  sequencePlanId: PLAN_ID,
  vinSequence: [
    { vin: "WBY11CF050N123401", position: 1, optionCodes: [] },
  ],
  source: "planning-cockpit" as const,
  lockedAt: "2026-10-13T06:17:04Z",
};

const PUBLISHED_RESPONSE: SequencePublishResponse = {
  syncJobId: "sync-job-1",
  opcenterSequenceId: "OPC-SEQ-2026-042-A-88142",
  status: "published",
  source: "planning-cockpit",
  publishedAt: "2026-10-13T06:17:38Z",
  auditEventId: "aud-001",
  retryScheduledAt: null,
};

const QUEUED_RESPONSE: SequencePublishResponse = {
  syncJobId: "sync-job-2",
  opcenterSequenceId: null,
  status: "queued",
  source: "planning-cockpit",
  publishedAt: null,
  auditEventId: null,
  retryScheduledAt: "2026-10-13T06:18:08Z",
};

const PUBLISHED_JOB: SyncJob = {
  syncJobId: "sync-job-2",
  sequencePlanId: PLAN_ID,
  status: "published",
  createdAt: "2026-10-13T06:17:38Z",
  updatedAt: "2026-10-13T06:18:08Z",
  opcenterSequenceId: "OPC-SEQ-2026-042-A-99999",
  lastAttemptAt: "2026-10-13T06:18:08Z",
  nextRetryAt: null,
};

beforeEach(() => {
  localStorage.clear();
  vi.useFakeTimers();
  // Default: no queued jobs to restore
  vi.mocked(api.listSyncJobs).mockResolvedValue({ data: [], page: { next_cursor: null, limit: 50 } });
});

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
  localStorage.clear();
});

describe("usePublishSequence", () => {
  it("starts in idle state", () => {
    const { result } = renderHook(() => usePublishSequence(PLAN_ID));
    expect(result.current.publishState.phase).toBe("idle");
  });

  it("transitions idle → publishing → published on happy path (status=published)", async () => {
    vi.mocked(api.publishSequence).mockResolvedValue(PUBLISHED_RESPONSE);

    const { result } = renderHook(() => usePublishSequence(PLAN_ID));

    await act(async () => {
      await result.current.publish(STUB_REQUEST);
    });

    expect(result.current.publishState.phase).toBe("published");
    const state = result.current.publishState;
    if (state.phase === "published") {
      expect(state.response.opcenterSequenceId).toBe("OPC-SEQ-2026-042-A-88142");
      expect(state.response.publishedAt).toBe("2026-10-13T06:17:38Z");
      expect(state.response.source).toBe("planning-cockpit");
    }
  });

  it("transitions idle → publishing → queued when OpCenter unavailable (status=queued)", async () => {
    vi.mocked(api.publishSequence).mockResolvedValue(QUEUED_RESPONSE);

    const { result } = renderHook(() => usePublishSequence(PLAN_ID));

    await act(async () => {
      await result.current.publish(STUB_REQUEST);
    });

    expect(result.current.publishState.phase).toBe("queued");
    const state = result.current.publishState;
    if (state.phase === "queued") {
      expect(state.syncJobId).toBe("sync-job-2");
      expect(state.retryScheduledAt).toBe("2026-10-13T06:18:08Z");
    }
  });

  it("persists pending job to localStorage on queued transition", async () => {
    vi.mocked(api.publishSequence).mockResolvedValue(QUEUED_RESPONSE);

    const { result } = renderHook(() => usePublishSequence(PLAN_ID));

    await act(async () => {
      await result.current.publish(STUB_REQUEST);
    });

    const stored = localStorage.getItem("ifos_pending_publish");
    expect(stored).not.toBeNull();
    const record = JSON.parse(stored!) as { syncJobId: string; sequencePlanId: string };
    expect(record.syncJobId).toBe("sync-job-2");
    expect(record.sequencePlanId).toBe(PLAN_ID);
  });

  it("starts polling every 30 s when queued and stops when published", async () => {
    vi.mocked(api.publishSequence).mockResolvedValue(QUEUED_RESPONSE);
    vi.mocked(api.getSyncJob).mockResolvedValue(PUBLISHED_JOB);

    const { result } = renderHook(() => usePublishSequence(PLAN_ID));

    await act(async () => {
      await result.current.publish(STUB_REQUEST);
    });

    expect(result.current.publishState.phase).toBe("queued");

    // Advance one poll interval
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });

    expect(vi.mocked(api.getSyncJob)).toHaveBeenCalledWith("sync-job-2");
    expect(result.current.publishState.phase).toBe("published");
    const state = result.current.publishState;
    if (state.phase === "published") {
      expect(state.response.opcenterSequenceId).toBe("OPC-SEQ-2026-042-A-99999");
    }
  });

  it("clears localStorage when polling resolves to published", async () => {
    vi.mocked(api.publishSequence).mockResolvedValue(QUEUED_RESPONSE);
    vi.mocked(api.getSyncJob).mockResolvedValue(PUBLISHED_JOB);

    const { result } = renderHook(() => usePublishSequence(PLAN_ID));

    await act(async () => {
      await result.current.publish(STUB_REQUEST);
    });

    expect(localStorage.getItem("ifos_pending_publish")).not.toBeNull();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });

    expect(localStorage.getItem("ifos_pending_publish")).toBeNull();
  });

  it("transitions to error state when publishSequence throws", async () => {
    vi.mocked(api.publishSequence).mockRejectedValue(new Error("Network error"));

    const { result } = renderHook(() => usePublishSequence(PLAN_ID));

    await act(async () => {
      await result.current.publish(STUB_REQUEST);
    });

    expect(result.current.publishState.phase).toBe("error");
    const state = result.current.publishState;
    if (state.phase === "error") {
      expect(state.message).toBe("Network error");
      expect(state.isRejected).toBe(false);
    }
  });

  it("transitions to error state and marks isRejected=true when job rejected_by_opcenter", async () => {
    vi.mocked(api.publishSequence).mockResolvedValue(QUEUED_RESPONSE);
    vi.mocked(api.getSyncJob).mockResolvedValue({
      ...PUBLISHED_JOB,
      status: "rejected_by_opcenter",
      opcenterError: "Invalid VIN checksum at position 3",
    });

    const { result } = renderHook(() => usePublishSequence(PLAN_ID));

    await act(async () => {
      await result.current.publish(STUB_REQUEST);
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });

    expect(result.current.publishState.phase).toBe("error");
    const state = result.current.publishState;
    if (state.phase === "error") {
      expect(state.isRejected).toBe(true);
      expect(state.message).toBe("Invalid VIN checksum at position 3");
    }
  });

  it("dismiss() resets state to idle", async () => {
    vi.mocked(api.publishSequence).mockResolvedValue(PUBLISHED_RESPONSE);

    const { result } = renderHook(() => usePublishSequence(PLAN_ID));

    await act(async () => {
      await result.current.publish(STUB_REQUEST);
    });

    expect(result.current.publishState.phase).toBe("published");

    act(() => {
      result.current.dismiss();
    });

    expect(result.current.publishState.phase).toBe("idle");
  });

  it("restores queued state from localStorage on mount when plan IDs match", async () => {
    localStorage.setItem(
      "ifos_pending_publish",
      JSON.stringify({
        syncJobId: "sync-job-restored",
        sequencePlanId: PLAN_ID,
        retryScheduledAt: "2026-10-13T07:00:00Z",
      }),
    );

    const { result } = renderHook(() => usePublishSequence(PLAN_ID));

    // Allow mount effects to settle
    await act(async () => {
      await Promise.resolve();
    });

    expect(result.current.publishState.phase).toBe("queued");
    const state = result.current.publishState;
    if (state.phase === "queued") {
      expect(state.syncJobId).toBe("sync-job-restored");
    }
  });

  it("does not restore localStorage entry when plan ID does not match", async () => {
    localStorage.setItem(
      "ifos_pending_publish",
      JSON.stringify({
        syncJobId: "sync-job-other",
        sequencePlanId: "other-plan-id",
        retryScheduledAt: null,
      }),
    );

    const { result } = renderHook(() => usePublishSequence(PLAN_ID));

    await act(async () => {
      await Promise.resolve();
    });

    expect(result.current.publishState.phase).toBe("idle");
  });
});
