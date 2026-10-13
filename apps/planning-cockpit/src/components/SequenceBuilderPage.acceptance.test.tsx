/**
 * Acceptance tests for US-004 — Publish the locked VIN sequence to Siemens OpCenter
 *
 * Exercises the seam between the opcenter-sync-service publish flow (publish endpoint,
 * retry queue, rejection audit) and the planning-cockpit UI (Publish button,
 * confirmation banner, pending-publish status).
 *
 * AC1 — confirmation banner shows OpCenter sequence ID, publishedAt, and source='planning-cockpit'
 * AC2 — OpCenter rejection is surfaced as isRejected error with Contact Integration Lead link
 * AC3 — unavailable OpCenter: Pending Publish badge, localStorage queuing, 30s auto-retry
 */

import "../i18n";
import { render, screen, act, waitFor, fireEvent } from "@testing-library/react";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { SequenceBuilderPage } from "./SequenceBuilderPage";
import type { SequencePublishResponse, SyncJob } from "../types/sequence";
import * as api from "../services/api";

vi.mock("../services/api", () => ({
  publishSequence: vi.fn(),
  getSyncJob: vi.fn(),
  listSyncJobs: vi.fn(),
  listConstraintThresholds: vi.fn(),
  upsertConstraintThreshold: vi.fn(),
  getConstraintStreamUrl: vi.fn(() => ""),
  postOptimize: vi.fn(),
  getOptimizeRun: vi.fn(),
  createOverride: vi.fn(),
  listOverrides: vi.fn(),
  explainVinPosition: vi.fn(),
}));

// ── Fixtures ──────────────────────────────────────────────────────────────────

// The sequencePlanId hard-coded in SequenceBuilderPage (STUB_PLAN_ID)
const STUB_PLAN_ID = "SPL-2026-042-A";

const PUBLISHED_RESPONSE: SequencePublishResponse = {
  syncJobId: "sync-job-accept-1",
  opcenterSequenceId: "OPC-SEQ-2026-042-A-88142",
  status: "published",
  source: "planning-cockpit",
  publishedAt: "2026-10-13T06:17:38Z",
  auditEventId: "aud-accept-001",
  retryScheduledAt: null,
};

const QUEUED_RESPONSE: SequencePublishResponse = {
  syncJobId: "sync-job-accept-2",
  opcenterSequenceId: null,
  status: "queued",
  source: "planning-cockpit",
  publishedAt: null,
  retryScheduledAt: "2026-10-13T06:18:08Z",
};

const REJECTED_JOB: SyncJob = {
  syncJobId: "sync-job-accept-2",
  sequencePlanId: STUB_PLAN_ID,
  status: "rejected_by_opcenter",
  createdAt: "2026-10-13T06:17:38Z",
  updatedAt: "2026-10-13T06:18:08Z",
  opcenterSequenceId: null,
  opcenterError:
    "VIN WBY11CF050N123401 failed OpCenter validation: unsigned sequence",
  nextRetryAt: null,
  lastAttemptAt: "2026-10-13T06:18:08Z",
};

const PUBLISHED_JOB: SyncJob = {
  syncJobId: "sync-job-accept-2",
  sequencePlanId: STUB_PLAN_ID,
  status: "published",
  createdAt: "2026-10-13T06:17:38Z",
  updatedAt: "2026-10-13T06:18:38Z",
  opcenterSequenceId: "OPC-SEQ-2026-042-A-99999",
  lastAttemptAt: "2026-10-13T06:18:38Z",
  nextRetryAt: null,
};

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Advance fake timers by 30 s (the AC3-specified retry interval). */
async function advanceRetryInterval() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(30_000);
  });
}

// ── Global setup ──────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  vi.mocked(api.listSyncJobs).mockResolvedValue({
    data: [],
    page: { next_cursor: null, limit: 50 },
  });
});

// ── AC1 ───────────────────────────────────────────────────────────────────────

describe("AC1 — confirmation banner shows OpCenter sequence ID, publishedAt, and source='planning-cockpit'", () => {
  it("precondition: publishSequence is mocked so AC1 tests are not vacuous against a silent stub", () => {
    // If this fails, the vi.mock is not in scope and all AC1 assertions would
    // pass trivially against an empty DOM — the precondition itself must be green.
    expect(vi.isMockFunction(api.publishSequence)).toBe(true);
  });

  it("AC1: confirmation banner shows the OpCenter sequence ID returned by the sync service", async () => {
    vi.mocked(api.publishSequence).mockResolvedValue(PUBLISHED_RESPONSE);
    render(<SequenceBuilderPage />);

    fireEvent.click(screen.getByRole("button", { name: /publish to opcenter/i }));
    fireEvent.click(screen.getByRole("button", { name: /publish \d+ vins to opcenter/i }));

    await waitFor(() => {
      expect(screen.getByText("OPC-SEQ-2026-042-A-88142")).toBeTruthy();
    });
  });

  it("AC1: confirmation banner shows the publishedAt timestamp returned by the sync service", async () => {
    vi.mocked(api.publishSequence).mockResolvedValue(PUBLISHED_RESPONSE);
    render(<SequenceBuilderPage />);

    fireEvent.click(screen.getByRole("button", { name: /publish to opcenter/i }));
    fireEvent.click(screen.getByRole("button", { name: /publish \d+ vins to opcenter/i }));

    // PublishConfirmationBanner renders new Date(publishedAt).toISOString(),
    // which normalises "2026-10-13T06:17:38Z" → "2026-10-13T06:17:38.000Z".
    await waitFor(() => {
      expect(screen.getByText("2026-10-13T06:17:38.000Z")).toBeTruthy();
    });
  });

  it("AC1: confirmation banner shows source field value exactly 'planning-cockpit' — confirming the sole authoritative source", async () => {
    vi.mocked(api.publishSequence).mockResolvedValue(PUBLISHED_RESPONSE);
    render(<SequenceBuilderPage />);

    fireEvent.click(screen.getByRole("button", { name: /publish to opcenter/i }));
    fireEvent.click(screen.getByRole("button", { name: /publish \d+ vins to opcenter/i }));

    await waitFor(() => {
      // PublishConfirmationBanner renders response.source in a <dd> element.
      // 'planning-cockpit' (lowercase, hyphenated) must appear as the source value.
      expect(screen.getByText("planning-cockpit")).toBeTruthy();
    });
  });

  it("AC1: all three required fields (opcenterSequenceId, publishedAt, source='planning-cockpit') appear together in the success banner", async () => {
    vi.mocked(api.publishSequence).mockResolvedValue(PUBLISHED_RESPONSE);
    render(<SequenceBuilderPage />);

    fireEvent.click(screen.getByRole("button", { name: /publish to opcenter/i }));
    fireEvent.click(screen.getByRole("button", { name: /publish \d+ vins to opcenter/i }));

    // Wait for the success heading to appear
    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: /successfully published to siemens opcenter/i }),
      ).toBeTruthy();
    });

    // All three AC1 fields must be present simultaneously in the success banner.
    // Multiple role="status" elements exist (banner + empty-sequence-state), so
    // identify the banner by its class.
    const banners = screen.getAllByRole("status");
    const banner = banners.find((el) =>
      el.classList.contains("publish-banner--success"),
    );
    expect(banner, "publish-banner--success element must exist").toBeTruthy();
    expect(banner!.textContent).toContain("OPC-SEQ-2026-042-A-88142");
    // Banner normalises the timestamp via new Date(...).toISOString()
    expect(banner!.textContent).toContain("2026-10-13T06:17:38.000Z");
    expect(banner!.textContent).toContain("planning-cockpit");
  });
});

// ── AC2 ───────────────────────────────────────────────────────────────────────

describe("AC2 — rejection from OpCenter is logged and surfaced with audit trail escalation link", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("precondition: getSyncJob is mocked so AC2 polling assertions are not vacuous", () => {
    expect(vi.isMockFunction(api.getSyncJob)).toBe(true);
  });

  it("AC2: after 30s poll resolves to rejected_by_opcenter, publish-failed error banner appears", async () => {
    vi.mocked(api.publishSequence).mockResolvedValue(QUEUED_RESPONSE);
    vi.mocked(api.getSyncJob).mockResolvedValue(REJECTED_JOB);
    render(<SequenceBuilderPage />);

    // Click to open confirm panel, then confirm — flush the async publish Promise via act
    fireEvent.click(screen.getByRole("button", { name: /publish to opcenter/i }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /publish \d+ vins to opcenter/i }));
    });

    expect(screen.getByText(/pending publish/i)).toBeTruthy();

    // Advance 30 s — poll fires and returns rejected_by_opcenter
    await advanceRetryInterval();

    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.getByText(/publish failed/i)).toBeTruthy();
  });

  it("AC2: Contact Integration Lead link is visible when rejection originates from OpCenter (audit trail entry point)", async () => {
    vi.mocked(api.publishSequence).mockResolvedValue(QUEUED_RESPONSE);
    vi.mocked(api.getSyncJob).mockResolvedValue(REJECTED_JOB);
    render(<SequenceBuilderPage />);

    fireEvent.click(screen.getByRole("button", { name: /publish to opcenter/i }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /publish \d+ vins to opcenter/i }));
    });

    expect(screen.getByText(/pending publish/i)).toBeTruthy();
    await advanceRetryInterval();

    // isRejected=true causes the UI to show the escalation link used to log the rejection
    expect(screen.getByText(/contact integration lead/i)).toBeTruthy();
  });

  it("AC2: OpCenter rejection error message from the sync service is surfaced verbatim in the UI", async () => {
    vi.mocked(api.publishSequence).mockResolvedValue(QUEUED_RESPONSE);
    vi.mocked(api.getSyncJob).mockResolvedValue(REJECTED_JOB);
    render(<SequenceBuilderPage />);

    fireEvent.click(screen.getByRole("button", { name: /publish to opcenter/i }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /publish \d+ vins to opcenter/i }));
    });

    expect(screen.getByText(/pending publish/i)).toBeTruthy();
    await advanceRetryInterval();

    expect(
      screen.getByText(/VIN WBY11CF050N123401 failed OpCenter validation: unsigned sequence/i),
    ).toBeTruthy();
  });

  it("AC2: a direct throw from the sync service (non-rejection network error) does NOT show the Contact Integration Lead link", async () => {
    vi.mocked(api.publishSequence).mockRejectedValue(new Error("Network error"));
    render(<SequenceBuilderPage />);

    fireEvent.click(screen.getByRole("button", { name: /publish to opcenter/i }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /publish \d+ vins to opcenter/i }));
    });

    expect(screen.getByRole("alert")).toBeTruthy();

    // Contact Integration Lead only appears for isRejected=true (OpCenter-sourced rejections)
    expect(screen.queryByText(/contact integration lead/i)).toBeNull();
  });
});

// ── AC3 ───────────────────────────────────────────────────────────────────────

describe("AC3 — unavailable OpCenter: Pending Publish badge, localStorage queuing, automatic 30s retry", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("precondition: localStorage starts empty — no stale pending publish that would contaminate AC3 assertions", () => {
    expect(localStorage.getItem("ifos_pending_publish")).toBeNull();
  });

  it("precondition: listSyncJobs is mocked to return empty data so mount does not restore a prior queued state", () => {
    expect(vi.isMockFunction(api.listSyncJobs)).toBe(true);
  });

  it("AC3: Pending Publish badge is immediately visible when the sync service responds 'queued'", async () => {
    vi.mocked(api.publishSequence).mockResolvedValue(QUEUED_RESPONSE);
    render(<SequenceBuilderPage />);

    fireEvent.click(screen.getByRole("button", { name: /publish to opcenter/i }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /publish \d+ vins to opcenter/i }));
    });

    expect(screen.getByText(/pending publish/i)).toBeTruthy();
  });

  it("AC3: retry time is shown in the Pending Publish badge so the planner knows when the next retry fires", async () => {
    vi.mocked(api.publishSequence).mockResolvedValue(QUEUED_RESPONSE);
    render(<SequenceBuilderPage />);

    fireEvent.click(screen.getByRole("button", { name: /publish to opcenter/i }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /publish \d+ vins to opcenter/i }));
    });

    expect(screen.getByText(/next retry at/i)).toBeTruthy();
  });

  it("AC3: queued sequence is persisted to localStorage so the planner sees pending status after a page reload", async () => {
    vi.mocked(api.publishSequence).mockResolvedValue(QUEUED_RESPONSE);
    render(<SequenceBuilderPage />);

    fireEvent.click(screen.getByRole("button", { name: /publish to opcenter/i }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /publish \d+ vins to opcenter/i }));
    });

    expect(localStorage.getItem("ifos_pending_publish")).not.toBeNull();

    const stored = localStorage.getItem("ifos_pending_publish");
    const record = JSON.parse(stored!) as { syncJobId: string; sequencePlanId: string };
    // syncJobId must match the one returned by the sync service
    expect(record.syncJobId).toBe("sync-job-accept-2");
    // sequencePlanId must match the plan so it is only restored for the correct plan
    expect(record.sequencePlanId).toBe(STUB_PLAN_ID);
  });

  it("AC3: getSyncJob is NOT called before 30 seconds — the 30s retry interval is strictly enforced", async () => {
    vi.mocked(api.publishSequence).mockResolvedValue(QUEUED_RESPONSE);
    vi.mocked(api.getSyncJob).mockResolvedValue(PUBLISHED_JOB);
    render(<SequenceBuilderPage />);

    fireEvent.click(screen.getByRole("button", { name: /publish to opcenter/i }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /publish \d+ vins to opcenter/i }));
    });

    expect(screen.getByText(/pending publish/i)).toBeTruthy();

    // Advance 29 s — retry must not have fired yet
    await act(async () => {
      await vi.advanceTimersByTimeAsync(29_000);
    });

    expect(vi.mocked(api.getSyncJob)).not.toHaveBeenCalled();
  });

  it("AC3: getSyncJob is called with the correct syncJobId after exactly 30 seconds (automatic retry fires)", async () => {
    vi.mocked(api.publishSequence).mockResolvedValue(QUEUED_RESPONSE);
    vi.mocked(api.getSyncJob).mockResolvedValue(PUBLISHED_JOB);
    render(<SequenceBuilderPage />);

    fireEvent.click(screen.getByRole("button", { name: /publish to opcenter/i }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /publish \d+ vins to opcenter/i }));
    });

    expect(screen.getByText(/pending publish/i)).toBeTruthy();
    await advanceRetryInterval();

    expect(vi.mocked(api.getSyncJob)).toHaveBeenCalledWith("sync-job-accept-2");
  });

  it("AC3: success banner replaces the Pending Publish badge once the 30s retry resolves to 'published'", async () => {
    vi.mocked(api.publishSequence).mockResolvedValue(QUEUED_RESPONSE);
    vi.mocked(api.getSyncJob).mockResolvedValue(PUBLISHED_JOB);
    render(<SequenceBuilderPage />);

    fireEvent.click(screen.getByRole("button", { name: /publish to opcenter/i }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /publish \d+ vins to opcenter/i }));
    });

    expect(screen.getByText(/pending publish/i)).toBeTruthy();
    await advanceRetryInterval();

    // Pending badge must be gone; success banner must appear
    expect(screen.queryByText(/pending publish/i)).toBeNull();
    expect(
      screen.getByRole("heading", { name: /successfully published to siemens opcenter/i }),
    ).toBeTruthy();
  });

  it("AC3: Publish to OpCenter button is hidden while a publish is pending, preventing duplicate submissions", async () => {
    vi.mocked(api.publishSequence).mockResolvedValue(QUEUED_RESPONSE);
    render(<SequenceBuilderPage />);

    fireEvent.click(screen.getByRole("button", { name: /publish to opcenter/i }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /publish \d+ vins to opcenter/i }));
    });

    expect(screen.getByText(/pending publish/i)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /publish to opcenter/i })).toBeNull();
  });
});
