/**
 * Acceptance tests for US-007: Command Center view with live OEE, Andon alerts,
 * and shift handover notes.
 *
 * AC-1: OEE for all 4 weld lines, data no older than 30 s,
 *       open predictive Andon alerts sorted by time-to-predicted-failure ASC
 * AC-2: Each alert shows station, predicted failure type, countdown (updating
 *       every 60 s), and recommended action
 * AC-3: Incoming handover shown as the first queue item with a "New Handover"
 *       badge until acknowledged
 *
 * Each test names the criterion it covers. Preconditions (fixture integrity,
 * component presence) are asserted first so the suite cannot silently pass on
 * empty or absent data.
 */

import { render, screen, within, act, cleanup } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "../i18n";
import { apiRequest } from "../services/api";
import { CommandCenterPage, type OeeSnapshot, type AndonAlert } from "./CommandCenterPage";

// ---------------------------------------------------------------------------
// Module mock — factory contains no module-level reads; no vi.hoisted needed
// ---------------------------------------------------------------------------

vi.mock("../services/api", () => ({
  apiRequest: vi.fn(),
  ForbiddenError: class ForbiddenError extends Error {
    resource: string;
    userId: string;
    constructor(resource: string, userId: string) {
      super(`403 Forbidden: ${resource}`);
      this.resource = resource;
      this.userId = userId;
    }
  },
}));

const mockApiRequest = vi.mocked(apiRequest);

// ---------------------------------------------------------------------------
// Fixture data — shaped exactly as the line-command-api contract produces
// ---------------------------------------------------------------------------

// Four weld-line snapshots, all with dataAgeSeconds <= 30 (fresh)
const WL01: OeeSnapshot = {
  lineId: "WL-01", oeePercent: 87.3, availability: 94.1, performance: 91.2,
  quality: 98.7, measuredAt: "2026-10-29T06:00:00.000Z", dataAgeSeconds: 5,
};
const WL02: OeeSnapshot = {
  lineId: "WL-02", oeePercent: 71.2, availability: 82.0, performance: 85.0,
  quality: 95.0, measuredAt: "2026-10-29T06:00:00.000Z", dataAgeSeconds: 12,
};
const WL03: OeeSnapshot = {
  lineId: "WL-03", oeePercent: 92.1, availability: 96.0, performance: 94.5,
  quality: 99.0, measuredAt: "2026-10-29T06:00:00.000Z", dataAgeSeconds: 8,
};
const WL04: OeeSnapshot = {
  lineId: "WL-04", oeePercent: 55.0, availability: 72.0, performance: 80.0,
  quality: 90.0, measuredAt: "2026-10-29T06:00:00.000Z", dataAgeSeconds: 20,
};

const ALL_4_SNAPSHOTS: OeeSnapshot[] = [WL01, WL02, WL03, WL04];

// Predictive alerts pre-sorted ascending by minutesToPredictedFailure
// (matches the ORDER BY the API contract guarantees)
const ALERTS_SORTED_ASC: AndonAlert[] = [
  {
    alertId: "alert-001",
    stationId: "WELD-CELL-03",
    lineId: "WL-03",
    alertType: "predictive",
    predictedFailureType: "electrode_wear",
    minutesToPredictedFailure: 4,
    recommendedAction: "Replace electrode cap immediately.",
    status: "open",
    detectedAt: "2026-10-29T06:00:00.000Z",
    resolvedAt: null,
    acknowledgedAt: null,
    opcenterEventId: null,
  },
  {
    alertId: "alert-002",
    stationId: "WELD-CELL-07",
    lineId: "WL-02",
    alertType: "predictive",
    predictedFailureType: "coolant_flow_degradation",
    minutesToPredictedFailure: 12,
    recommendedAction: "Check coolant reservoir level.",
    status: "open",
    detectedAt: "2026-10-29T06:00:00.000Z",
    resolvedAt: null,
    acknowledgedAt: null,
    opcenterEventId: null,
  },
  {
    alertId: "alert-003",
    stationId: "WELD-CELL-11",
    lineId: "WL-04",
    alertType: "predictive",
    predictedFailureType: "tip_dress_required",
    minutesToPredictedFailure: 20,
    recommendedAction: "Schedule tip dressing within next 20 minutes.",
    status: "open",
    detectedAt: "2026-10-29T06:00:00.000Z",
    resolvedAt: null,
    acknowledgedAt: null,
    opcenterEventId: null,
  },
];

const PENDING_HANDOVER = {
  handoverId: "hov-001",
  lineId: "WL-01" as string | null,
  submittedAt: "2026-10-29T05:45:00.000Z",
  acknowledged: false,
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function setupApiMocks(opts: {
  snapshots?: OeeSnapshot[];
  alerts?: AndonAlert[];
  handovers?: Array<{ handoverId: string; lineId: string | null; submittedAt: string; acknowledged: boolean }>;
} = {}) {
  const { snapshots = ALL_4_SNAPSHOTS, alerts = [], handovers = [] } = opts;
  mockApiRequest.mockImplementation(async (path: string) => {
    if (typeof path === "string" && path.includes("/oee/snapshots")) {
      return { data: snapshots, page: { next_cursor: null, limit: 200 } };
    }
    if (typeof path === "string" && path.includes("/andon/alerts")) {
      return { data: alerts, page: { next_cursor: null, limit: 200 } };
    }
    if (typeof path === "string" && path.includes("/handovers")) {
      return { data: handovers };
    }
    return { data: [] };
  });
}

function renderCommandCenter() {
  return render(
    <I18nextProvider i18n={i18n}>
      <CommandCenterPage />
    </I18nextProvider>,
  );
}

async function waitForLoad() {
  await act(async () => {
    await Promise.resolve();
  });
}

beforeEach(async () => {
  vi.useFakeTimers();
  await i18n.changeLanguage("en-GB");
  vi.clearAllMocks();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

// ---------------------------------------------------------------------------
// AC-1: OEE for all 4 weld lines, data no older than 30 s,
//       open predictive Andon alerts sorted by time-to-predicted-failure ASC
// ---------------------------------------------------------------------------

describe("AC-1 · Command Center loads with live OEE for all 4 weld lines and sorted Andon alerts", () => {
  it("AC-1 · displays OEE tiles for all four weld lines WL-01 through WL-04", async () => {
    setupApiMocks();
    renderCommandCenter();
    await waitForLoad();

    // Precondition: fixture must contain exactly 4 snapshots
    expect(ALL_4_SNAPSHOTS).toHaveLength(4);
    // Precondition: component loaded (alert queue is visible)
    expect(screen.getByRole("list", { name: /alert queue/i })).toBeTruthy();

    // Criterion: every weld-line ID is visible in the OEE panel
    for (const snap of ALL_4_SNAPSHOTS) {
      expect(screen.getByText(snap.lineId)).toBeTruthy();
    }
  });

  it("AC-1 · shows no staleness badge when all OEE snapshots are ≤ 30 seconds old", async () => {
    // Precondition: all fixture snapshots must have dataAgeSeconds <= 30
    for (const snap of ALL_4_SNAPSHOTS) {
      expect(snap.dataAgeSeconds).toBeLessThanOrEqual(30);
    }

    setupApiMocks();
    renderCommandCenter();
    await waitForLoad();

    // Precondition: tiles are rendered (not still loading)
    expect(screen.queryByText("WL-01")).not.toBeNull();

    // Criterion: no stale badges appear when data is within the 30 s threshold
    expect(screen.queryAllByRole("status", { name: /stale/i })).toHaveLength(0);
  });

  it("AC-1 · staleness badge appears on a tile whose dataAgeSeconds exceeds 30", async () => {
    const snapshotsWithOneStale: OeeSnapshot[] = [
      WL01, WL02, WL03,
      { ...WL04, dataAgeSeconds: 31 },
    ];

    // Precondition: exactly one snapshot is stale
    expect(snapshotsWithOneStale.filter((s) => s.dataAgeSeconds > 30)).toHaveLength(1);

    setupApiMocks({ snapshots: snapshotsWithOneStale });
    renderCommandCenter();
    await waitForLoad();

    // Criterion: exactly one stale badge is rendered
    expect(screen.getAllByRole("status", { name: /stale/i })).toHaveLength(1);
  });

  it("AC-1 · open Andon alerts are displayed in ascending minutesToPredictedFailure order", async () => {
    // Precondition: fixture alerts are sorted ascending: 4, 12, 20 min
    expect(ALERTS_SORTED_ASC).toHaveLength(3);
    expect(ALERTS_SORTED_ASC[0].minutesToPredictedFailure).toBe(4);
    expect(ALERTS_SORTED_ASC[1].minutesToPredictedFailure).toBe(12);
    expect(ALERTS_SORTED_ASC[2].minutesToPredictedFailure).toBe(20);

    setupApiMocks({ alerts: ALERTS_SORTED_ASC });
    renderCommandCenter();
    await waitForLoad();

    const queue = screen.getByRole("list", { name: /alert queue/i });
    const countdowns = Array.from(queue.querySelectorAll("[aria-live='polite']"));

    // Precondition: 3 countdown chips rendered (one per alert)
    expect(countdowns).toHaveLength(3);

    // Criterion: chips appear in ascending order of minutes remaining
    expect(countdowns[0].textContent).toContain("4");
    expect(countdowns[1].textContent).toContain("12");
    expect(countdowns[2].textContent).toContain("20");
  });

  it("AC-1 · full scenario: 4 fresh OEE tiles and sorted alert queue visible simultaneously on load", async () => {
    setupApiMocks({ alerts: ALERTS_SORTED_ASC });
    renderCommandCenter();
    await waitForLoad();

    // Precondition: both data sets are non-empty
    expect(ALL_4_SNAPSHOTS).toHaveLength(4);
    expect(ALERTS_SORTED_ASC.length).toBeGreaterThan(0);

    // Criterion part A: all 4 weld-line IDs visible
    for (const snap of ALL_4_SNAPSHOTS) {
      expect(screen.getByText(snap.lineId)).toBeTruthy();
    }

    // Criterion part B: no stale badges (fresh data)
    expect(screen.queryAllByRole("status", { name: /stale/i })).toHaveLength(0);

    // Criterion part C: alert queue shows all 3 alerts
    const queue = screen.getByRole("list", { name: /alert queue/i });
    expect(queue.querySelectorAll("[aria-live='polite']")).toHaveLength(3);
  });
});

// ---------------------------------------------------------------------------
// AC-2: Each predictive Andon alert shows station, failure type, countdown,
//       recommended action
// ---------------------------------------------------------------------------

describe("AC-2 · Each predictive Andon alert shows all four required fields", () => {
  // Use the first alert: stationId WELD-CELL-03, electrode_wear, 4 min
  const ALERT = ALERTS_SORTED_ASC[0];

  it("AC-2 · shows the affected station ID", async () => {
    // Precondition: alert has a stationId
    expect(ALERT.stationId).toBe("WELD-CELL-03");

    setupApiMocks({ alerts: [ALERT] });
    renderCommandCenter();
    await waitForLoad();

    const queue = screen.getByRole("list", { name: /alert queue/i });
    // Precondition: alert queue is not empty
    expect(queue.querySelectorAll("li")).toHaveLength(1);

    // Criterion: station ID is rendered in the alert card
    expect(within(queue).getByText("WELD-CELL-03")).toBeTruthy();
  });

  it("AC-2 · shows the predicted failure type", async () => {
    // Precondition: alert has a predictedFailureType
    expect(ALERT.predictedFailureType).toBe("electrode_wear");

    setupApiMocks({ alerts: [ALERT] });
    renderCommandCenter();
    await waitForLoad();

    const queue = screen.getByRole("list", { name: /alert queue/i });
    // Precondition: alert queue is not empty
    expect(queue.querySelectorAll("li")).toHaveLength(1);

    // Criterion: failure type is visible (component renders it with textTransform:uppercase via CSS,
    // but the DOM textContent retains the original casing — check case-insensitively)
    const queueText = queue.textContent?.toUpperCase() ?? "";
    expect(queueText).toContain("ELECTRODE_WEAR");
  });

  it("AC-2 · shows the minutes remaining to predicted failure as a countdown chip", async () => {
    // Precondition: alert has minutesToPredictedFailure
    expect(ALERT.minutesToPredictedFailure).toBe(4);

    setupApiMocks({ alerts: [ALERT] });
    renderCommandCenter();
    await waitForLoad();

    const queue = screen.getByRole("list", { name: /alert queue/i });
    const countdown = queue.querySelector("[aria-live='polite']");

    // Precondition: countdown chip rendered
    expect(countdown).not.toBeNull();

    // Criterion: chip shows the initial minutes remaining
    expect(countdown!.textContent).toContain("4");
  });

  it("AC-2 · countdown chip updates every 60 seconds", async () => {
    setupApiMocks({ alerts: [ALERT] });
    renderCommandCenter();
    await waitForLoad();

    const queue = screen.getByRole("list", { name: /alert queue/i });
    const countdown = queue.querySelector("[aria-live='polite']");
    expect(countdown).not.toBeNull();

    // Precondition: starts at 4 minutes
    expect(countdown!.textContent).toContain("4");

    // Criterion: exactly 60 s later the displayed value decrements by 1
    await act(async () => {
      vi.advanceTimersByTime(60_000);
    });
    expect(countdown!.textContent).toContain("3");
  });

  it("AC-2 · shows the recommended action", async () => {
    // Precondition: alert has a recommendedAction
    expect(ALERT.recommendedAction).toBe("Replace electrode cap immediately.");

    setupApiMocks({ alerts: [ALERT] });
    renderCommandCenter();
    await waitForLoad();

    const queue = screen.getByRole("list", { name: /alert queue/i });
    // Precondition: alert queue is not empty
    expect(queue.querySelectorAll("li")).toHaveLength(1);

    // Criterion: recommended action text is visible in the alert card
    expect(within(queue).getByText("Replace electrode cap immediately.")).toBeTruthy();
  });

  it("AC-2 · all four required fields are present together in a single alert card", async () => {
    setupApiMocks({ alerts: [ALERT] });
    renderCommandCenter();
    await waitForLoad();

    const queue = screen.getByRole("list", { name: /alert queue/i });
    const items = Array.from(queue.querySelectorAll("li"));

    // Precondition: exactly one alert item in the queue (no handover)
    expect(items).toHaveLength(1);

    const cardText = items[0].textContent ?? "";

    // Criterion: card contains all four required fields
    expect(cardText).toContain("WELD-CELL-03");                        // station
    expect(cardText.toUpperCase()).toContain("ELECTRODE_WEAR");         // failure type
    expect(cardText).toContain("4");                                    // minutes remaining
    expect(cardText).toContain("Replace electrode cap immediately.");   // recommended action
  });
});

// ---------------------------------------------------------------------------
// AC-3: Handover notes shown as first queue item with 'New Handover' badge
//       until acknowledged
// ---------------------------------------------------------------------------

describe("AC-3 · Incoming handover shown as first alert queue item with New Handover badge until acknowledged", () => {
  it("AC-3 · New Handover badge is visible when an unacknowledged handover is present", async () => {
    // Precondition: fixture handover is unacknowledged
    expect(PENDING_HANDOVER.acknowledged).toBe(false);

    setupApiMocks({ handovers: [PENDING_HANDOVER] });
    renderCommandCenter();
    await waitForLoad();

    // Precondition: alert queue is rendered
    const queue = screen.getByRole("list", { name: /alert queue/i });
    expect(queue).toBeTruthy();

    // Criterion: "New Handover" badge text is visible
    expect(within(queue).getByText("New Handover")).toBeTruthy();
  });

  it("AC-3 · handover item is the first entry in the alert queue, before all Andon alerts", async () => {
    // Precondition: both pending handover and alerts are present
    setupApiMocks({ alerts: ALERTS_SORTED_ASC, handovers: [PENDING_HANDOVER] });
    renderCommandCenter();
    await waitForLoad();

    const queue = screen.getByRole("list", { name: /alert queue/i });
    const items = Array.from(queue.querySelectorAll("li"));

    // Precondition: 4 items = 1 handover + 3 alerts
    expect(items).toHaveLength(4);

    // Criterion: first item carries the handover badge
    expect(items[0].textContent).toContain("New Handover");

    // Criterion: Andon alerts follow — first alert's station is in the second item
    expect(items[1].textContent).toContain("WELD-CELL-03");
  });

  it("AC-3 · New Handover badge is removed after the Shift Lead acknowledges it", async () => {
    setupApiMocks({ handovers: [PENDING_HANDOVER] });
    renderCommandCenter();
    await waitForLoad();

    // Precondition: badge is present before acknowledgment
    expect(screen.getByText("New Handover")).toBeTruthy();

    // Action: Shift Lead clicks Acknowledge on the handover item
    // (no alerts in queue → only one Acknowledge button)
    const ackBtn = screen.getByRole("button", { name: /acknowledge/i });
    await act(async () => {
      ackBtn.click();
      await Promise.resolve();
    });

    // Criterion: badge is no longer rendered after acknowledgment
    expect(screen.queryByText("New Handover")).toBeNull();
  });

  it("AC-3 · no New Handover badge when the handover has already been acknowledged", async () => {
    setupApiMocks({ handovers: [{ ...PENDING_HANDOVER, acknowledged: true }] });
    renderCommandCenter();
    await waitForLoad();

    // Criterion: an already-acknowledged handover does not show the badge
    expect(screen.queryByText("New Handover")).toBeNull();
  });

  it("AC-3 · no New Handover badge when no handover exists", async () => {
    setupApiMocks({ handovers: [] });
    renderCommandCenter();
    await waitForLoad();

    // Criterion: no badge when there is no pending handover
    expect(screen.queryByText("New Handover")).toBeNull();
  });
});
