import { render, screen, act, cleanup } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "../i18n";
import { apiRequest } from "../services/api";
import { CommandCenterPage, type OeeSnapshot, type AndonAlert } from "./CommandCenterPage";

// ---------------------------------------------------------------------------
// Mocks
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
// Fixtures
// ---------------------------------------------------------------------------

function makeOeeSnapshot(overrides: Partial<OeeSnapshot> = {}): OeeSnapshot {
  return {
    lineId: "WL-01",
    oeePercent: 87.3,
    availability: 94.1,
    performance: 91.2,
    quality: 98.7,
    measuredAt: new Date(Date.now() - 10_000).toISOString(),
    dataAgeSeconds: 10,
    ...overrides,
  };
}

function makeAlert(overrides: Partial<AndonAlert> = {}): AndonAlert {
  return {
    alertId: "00000000-0000-0000-0000-000000000001",
    stationId: "WELD-CELL-01",
    lineId: "WL-01",
    alertType: "predictive",
    predictedFailureType: "electrode_wear",
    minutesToPredictedFailure: 14,
    recommendedAction: "Replace electrode cap immediately.",
    status: "open",
    detectedAt: new Date().toISOString(),
    resolvedAt: null,
    acknowledgedAt: null,
    opcenterEventId: null,
    ...overrides,
  };
}

const OEE_SNAPSHOTS: OeeSnapshot[] = [
  makeOeeSnapshot({ lineId: "WL-01", oeePercent: 87.3 }),
  makeOeeSnapshot({ lineId: "WL-02", oeePercent: 71.2, dataAgeSeconds: 12 }),
  makeOeeSnapshot({ lineId: "WL-03", oeePercent: 92.1, dataAgeSeconds: 8 }),
  makeOeeSnapshot({ lineId: "WL-04", oeePercent: 55.0, dataAgeSeconds: 20 }),
];

const SORTED_ALERTS: AndonAlert[] = [
  makeAlert({
    alertId: "00000000-0000-0000-0000-000000000001",
    stationId: "WELD-CELL-03",
    lineId: "WL-03",
    minutesToPredictedFailure: 4,
  }),
  makeAlert({
    alertId: "00000000-0000-0000-0000-000000000002",
    stationId: "WELD-CELL-07",
    lineId: "WL-02",
    minutesToPredictedFailure: 12,
  }),
  makeAlert({
    alertId: "00000000-0000-0000-0000-000000000003",
    stationId: "WELD-CELL-11",
    lineId: "WL-04",
    minutesToPredictedFailure: 20,
  }),
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function setupDefaultMocks() {
  mockApiRequest.mockImplementation(async (path: string) => {
    if (typeof path === "string" && path.includes("/oee/snapshots")) {
      return { data: OEE_SNAPSHOTS, page: { next_cursor: null, limit: 200 } };
    }
    if (typeof path === "string" && path.includes("/andon/alerts")) {
      return { data: SORTED_ALERTS, page: { next_cursor: null, limit: 200 } };
    }
    if (typeof path === "string" && path.includes("/handovers")) {
      return { data: [] };
    }
    return { data: [] };
  });
}

function renderPage() {
  return render(
    <I18nextProvider i18n={i18n}>
      <CommandCenterPage />
    </I18nextProvider>,
  );
}

// ---------------------------------------------------------------------------
// Setup / teardown
// ---------------------------------------------------------------------------

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
// AC-1: OEE section renders exactly 4 tiles
// ---------------------------------------------------------------------------

describe("OEE tiles", () => {
  it("renders exactly 4 OEE tiles when snapshots are loaded", async () => {
    setupDefaultMocks();
    renderPage();

    await act(async () => {
      await Promise.resolve();
    });

    const regions = screen.getAllByRole("region");
    expect(regions.length).toBe(4);
  });

  it("each OEE tile shows the lineId", async () => {
    setupDefaultMocks();
    renderPage();

    await act(async () => {
      await Promise.resolve();
    });

    for (const snap of OEE_SNAPSHOTS) {
      expect(screen.getByText(snap.lineId)).toBeTruthy();
    }
  });

  it("shows loading skeleton while data is in flight", () => {
    mockApiRequest.mockImplementation(() => new Promise(() => {})); // never resolves
    renderPage();

    const busyEl = document.querySelector("[aria-busy='true']");
    expect(busyEl).not.toBeNull();
  });
});

// ---------------------------------------------------------------------------
// AC-2: Staleness badge when dataAgeSeconds > 30
// ---------------------------------------------------------------------------

describe("Staleness badge", () => {
  it("shows staleness badge when dataAgeSeconds is 31", async () => {
    mockApiRequest.mockImplementation(async (path: string) => {
      if (typeof path === "string" && path.includes("/oee/snapshots")) {
        return {
          data: [makeOeeSnapshot({ lineId: "WL-01", dataAgeSeconds: 31 })],
          page: { next_cursor: null, limit: 200 },
        };
      }
      if (typeof path === "string" && path.includes("/andon/alerts")) {
        return { data: [], page: { next_cursor: null, limit: 200 } };
      }
      return { data: [] };
    });

    renderPage();
    await act(async () => { await Promise.resolve(); });

    const staleBadge = screen.getByRole("status", {
      name: /stale/i,
    });
    expect(staleBadge).toBeTruthy();
  });

  it("does NOT show staleness badge when dataAgeSeconds is 30", async () => {
    mockApiRequest.mockImplementation(async (path: string) => {
      if (typeof path === "string" && path.includes("/oee/snapshots")) {
        return {
          data: [makeOeeSnapshot({ lineId: "WL-01", dataAgeSeconds: 30 })],
          page: { next_cursor: null, limit: 200 },
        };
      }
      if (typeof path === "string" && path.includes("/andon/alerts")) {
        return { data: [], page: { next_cursor: null, limit: 200 } };
      }
      return { data: [] };
    });

    renderPage();
    await act(async () => { await Promise.resolve(); });

    const staleBadges = screen.queryAllByRole("status", { name: /stale/i });
    expect(staleBadges.length).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// AC-3: Alert queue renders in ascending minutesToPredictedFailure order
// ---------------------------------------------------------------------------

describe("Alert queue order", () => {
  it("renders alerts in the order returned by the API (ascending minutesToPredictedFailure)", async () => {
    setupDefaultMocks();
    renderPage();
    await act(async () => { await Promise.resolve(); });

    const list = screen.getByRole("list", { name: /alert queue/i });
    const items = list.querySelectorAll("li");

    // The first real alert item (after any handover item) should have minutesToPredictedFailure=4
    // We verify order by checking the ⏱ countdown chips appear in the correct sequence
    const countdownSpans = Array.from(items).flatMap((li) =>
      Array.from(li.querySelectorAll("[aria-live='polite']")),
    );

    // First countdown chip has 4 min, second has 12 min, third has 20 min
    expect(countdownSpans[0]?.textContent).toContain("4");
    expect(countdownSpans[1]?.textContent).toContain("12");
    expect(countdownSpans[2]?.textContent).toContain("20");
  });
});

// ---------------------------------------------------------------------------
// AC-4: Countdown decrements after 60 s
// ---------------------------------------------------------------------------

describe("Alert countdown", () => {
  it("decrements minutesToPredictedFailure by 1 after 60 seconds", async () => {
    setupDefaultMocks();
    renderPage();
    await act(async () => { await Promise.resolve(); });

    // Find the first countdown (4 min)
    const list = screen.getByRole("list", { name: /alert queue/i });
    const firstCountdown = list.querySelector("[aria-live='polite']");
    expect(firstCountdown?.textContent).toContain("4");

    // Advance 60 s
    await act(async () => {
      vi.advanceTimersByTime(60_000);
    });

    expect(firstCountdown?.textContent).toContain("3");
  });
});

// ---------------------------------------------------------------------------
// AC-5: New Handover badge present on load when handover is pending
// ---------------------------------------------------------------------------

describe("New Handover badge", () => {
  it("renders 'New Handover' badge as first queue item when a pending handover is returned", async () => {
    mockApiRequest.mockImplementation(async (path: string) => {
      if (typeof path === "string" && path.includes("/oee/snapshots")) {
        return { data: OEE_SNAPSHOTS, page: { next_cursor: null, limit: 200 } };
      }
      if (typeof path === "string" && path.includes("/andon/alerts")) {
        return { data: [], page: { next_cursor: null, limit: 200 } };
      }
      if (typeof path === "string" && path.includes("/handovers")) {
        return {
          data: [
            {
              handoverId: "hov-001",
              lineId: null,
              submittedAt: new Date().toISOString(),
              acknowledged: false,
            },
          ],
        };
      }
      return { data: [] };
    });

    renderPage();
    await act(async () => { await Promise.resolve(); });

    expect(screen.getByText("New Handover")).toBeTruthy();
  });

  it("'New Handover' badge is the first item in the list", async () => {
    mockApiRequest.mockImplementation(async (path: string) => {
      if (typeof path === "string" && path.includes("/oee/snapshots")) {
        return { data: OEE_SNAPSHOTS, page: { next_cursor: null, limit: 200 } };
      }
      if (typeof path === "string" && path.includes("/andon/alerts")) {
        return { data: SORTED_ALERTS, page: { next_cursor: null, limit: 200 } };
      }
      if (typeof path === "string" && path.includes("/handovers")) {
        return {
          data: [
            {
              handoverId: "hov-001",
              lineId: null,
              submittedAt: new Date().toISOString(),
              acknowledged: false,
            },
          ],
        };
      }
      return { data: [] };
    });

    renderPage();
    await act(async () => { await Promise.resolve(); });

    const list = screen.getByRole("list", { name: /alert queue/i });
    const firstItem = list.querySelector("li");
    expect(firstItem?.textContent).toContain("New Handover");
  });

  it("removes New Handover badge after acknowledge", async () => {
    mockApiRequest.mockImplementation(async (path: string) => {
      if (typeof path === "string" && path.includes("/oee/snapshots")) {
        return { data: OEE_SNAPSHOTS, page: { next_cursor: null, limit: 200 } };
      }
      if (typeof path === "string" && path.includes("/andon/alerts")) {
        return { data: [], page: { next_cursor: null, limit: 200 } };
      }
      if (typeof path === "string" && path.includes("/handovers")) {
        return {
          data: [
            {
              handoverId: "hov-001",
              lineId: null,
              submittedAt: new Date().toISOString(),
              acknowledged: false,
            },
          ],
        };
      }
      if (typeof path === "string" && path.includes("/acknowledge")) {
        return { alertId: "hov-001", status: "resolved", acknowledgedAt: new Date().toISOString(), eventId: "evt-001" };
      }
      return { data: [] };
    });

    renderPage();
    await act(async () => { await Promise.resolve(); });

    // Badge is present
    expect(screen.getByText("New Handover")).toBeTruthy();

    // Click acknowledge
    const ackButton = screen.getByRole("button", { name: /acknowledge/i });
    await act(async () => {
      ackButton.click();
      await Promise.resolve();
    });

    // Badge is gone
    expect(screen.queryByText("New Handover")).toBeNull();
  });

  it("does NOT render New Handover badge when no pending handover", async () => {
    setupDefaultMocks();
    renderPage();
    await act(async () => { await Promise.resolve(); });

    expect(screen.queryByText("New Handover")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Error state
// ---------------------------------------------------------------------------

describe("Error states", () => {
  it("shows OEE error banner when OEE fetch fails", async () => {
    mockApiRequest.mockImplementation(async (path: string) => {
      if (typeof path === "string" && path.includes("/oee/snapshots")) {
        throw new Error("HTTP 503");
      }
      if (typeof path === "string" && path.includes("/andon/alerts")) {
        return { data: [], page: { next_cursor: null, limit: 200 } };
      }
      return { data: [] };
    });

    renderPage();
    await act(async () => { await Promise.resolve(); });

    const errorBanner = screen.getByRole("alert");
    expect(errorBanner).toBeTruthy();
    expect(errorBanner.textContent).toContain("OEE feed unavailable");
  });

  it("shows retry button in OEE error banner", async () => {
    mockApiRequest.mockImplementation(async (path: string) => {
      if (typeof path === "string" && path.includes("/oee/snapshots")) {
        throw new Error("HTTP 503");
      }
      return { data: [] };
    });

    renderPage();
    await act(async () => { await Promise.resolve(); });

    expect(screen.getByRole("button", { name: /retry oee fetch/i })).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

describe("Empty state", () => {
  it("shows empty-alerts message when no alerts and no handover", async () => {
    mockApiRequest.mockImplementation(async (path: string) => {
      if (typeof path === "string" && path.includes("/oee/snapshots")) {
        return { data: OEE_SNAPSHOTS, page: { next_cursor: null, limit: 200 } };
      }
      return { data: [] };
    });

    renderPage();
    await act(async () => { await Promise.resolve(); });

    expect(screen.getByText("No open alerts")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// i18n
// ---------------------------------------------------------------------------

describe("i18n", () => {
  it("renders German labels when locale is de-DE", async () => {
    setupDefaultMocks();
    await act(async () => { await i18n.changeLanguage("de-DE"); });
    renderPage();
    await act(async () => { await Promise.resolve(); });

    expect(screen.getByText("Offene Andon-Meldungen")).toBeTruthy();
    expect(screen.getByText("OEE — Schweißlinien 1–4")).toBeTruthy();
  });

  it("renders English labels when locale is en-GB", async () => {
    setupDefaultMocks();
    await i18n.changeLanguage("en-GB");
    renderPage();
    await act(async () => { await Promise.resolve(); });

    expect(screen.getByText("Open Andon Alerts")).toBeTruthy();
    expect(screen.getByText("OEE — Welding Lines 1–4")).toBeTruthy();
  });
});
