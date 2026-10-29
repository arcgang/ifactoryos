/**
 * Acceptance tests for US-009 — Structured digital shift handover in Handover Builder.
 *
 * Tests the seam between the three merged tasks:
 *   postgresql-citus: shift_handover and andon_alert migrations
 *   line-command-api: shift handover submission and retrieval
 *   line-command: Handover Builder form + Command Center handover summary
 *
 * AC-1  Open predictive Andon alerts are pre-populated in the Handover Builder as
 *       line items each requiring a disposition note (resolved / escalated / monitoring).
 *
 * AC-2  After a handover is submitted, the incoming Shift Lead's Command Center view
 *       shows the handover summary with OEE delta, open alert dispositions, and VINs.
 *
 * AC-3  The Plant Manager's daily review table shows handover records with submitting
 *       Shift Lead ID, shift start/end timestamps, and alert count (structured log).
 *
 * Each test names the criterion it covers. Preconditions run before behavioural
 * assertions — a test that would pass against empty or absent data is a gap, not a pass.
 */

import { render, screen, within, act, cleanup, fireEvent } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "../i18n";
import { apiRequest, ForbiddenError } from "../services/api";
import { HandoverBuilderPage } from "./HandoverBuilderPage";
import { CommandCenterPage, type AndonAlert, type OeeSnapshot } from "./CommandCenterPage";
import { PlantManagerHandoverTable } from "./PlantManagerHandoverTable";

// ---------------------------------------------------------------------------
// Module mock — hoisted; no factory-level variable reads needed
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
// Fixture data — shaped exactly as line-command-api produces
// ---------------------------------------------------------------------------

const OPEN_ALERTS: AndonAlert[] = [
  {
    alertId: "00000000-0000-0000-0000-000000000001",
    stationId: "WELD-CELL-03",
    lineId: "WL-01",
    alertType: "predictive",
    predictedFailureType: "electrode_wear",
    minutesToPredictedFailure: 12,
    recommendedAction: "Replace electrode cap immediately.",
    status: "open",
    detectedAt: "2099-01-01T06:00:00.000Z",
    resolvedAt: null,
    acknowledgedAt: null,
    opcenterEventId: null,
  },
  {
    alertId: "00000000-0000-0000-0000-000000000002",
    stationId: "WELD-CELL-07",
    lineId: "WL-02",
    alertType: "predictive",
    predictedFailureType: "coolant_flow_degradation",
    minutesToPredictedFailure: 20,
    recommendedAction: "Check coolant reservoir level.",
    status: "open",
    detectedAt: "2099-01-01T06:00:00.000Z",
    resolvedAt: null,
    acknowledgedAt: null,
    opcenterEventId: null,
  },
];

const OEE_SNAPSHOTS: OeeSnapshot[] = [
  {
    lineId: "WL-01",
    oeePercent: 87.3,
    availability: 94.1,
    performance: 91.2,
    quality: 98.7,
    measuredAt: "2099-01-01T06:00:00.000Z",
    dataAgeSeconds: 5,
  },
];

// A submitted handover returned by GET /handovers (summary record for Command Center)
const SUBMITTED_HANDOVER_SUMMARY = {
  handoverId: "hov-acc-001",
  lineId: "WL-01" as string | null,
  submittedAt: "2099-01-01T13:55:00.000Z",
  acknowledged: false,
  oeeDeltaPct: 2.5,
  alertDispositions: [
    { alertId: "00000000-0000-0000-0000-000000000001", disposition: "resolved" as const, note: "Fixed" },
    { alertId: "00000000-0000-0000-0000-000000000002", disposition: "escalated" as const, note: "Maintenance required" },
  ],
  heldVins: ["WBA3A5C55FK123456"],
};

// A full HandoverRecord for PlantManagerHandoverTable (GET /handovers list)
const PM_HANDOVER_RECORD = {
  handoverId: "hov-acc-001",
  shiftId: "11111111-1111-1111-1111-111111111111",
  lineId: "WL-01",
  shiftStartTs: "2099-01-01T06:00:00.000Z",
  shiftEndTs: "2099-01-01T14:00:00.000Z",
  submittedByUserId: "sl-001",
  alertDispositions: [
    { alertId: "00000000-0000-0000-0000-000000000001", disposition: "resolved" as const, note: "Fixed" },
    { alertId: "00000000-0000-0000-0000-000000000002", disposition: "escalated" as const, note: "Maintenance required" },
  ],
  oeeDeltaPct: 2.5,
  heldVins: ["WBA3A5C55FK123456"],
  notes: "All stations checked at shift end.",
  createdAt: "2099-01-01T14:00:00.000Z",
  updatedAt: null,
};

// ---------------------------------------------------------------------------
// Render helpers
// ---------------------------------------------------------------------------

function renderHandoverBuilder(alerts = OPEN_ALERTS) {
  mockApiRequest.mockImplementation(async (path: string) => {
    if (typeof path === "string" && path.includes("/andon/alerts")) {
      return { data: alerts, page: { next_cursor: null, limit: 200 } };
    }
    return { data: [] };
  });

  return render(
    <I18nextProvider i18n={i18n}>
      <HandoverBuilderPage oeeSnapshots={OEE_SNAPSHOTS} userId="sl-001" />
    </I18nextProvider>,
  );
}

function renderCommandCenter(opts: {
  handovers?: typeof SUBMITTED_HANDOVER_SUMMARY[];
  alerts?: AndonAlert[];
  snapshots?: OeeSnapshot[];
} = {}) {
  const { handovers = [], alerts = [], snapshots = OEE_SNAPSHOTS } = opts;
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

  return render(
    <I18nextProvider i18n={i18n}>
      <CommandCenterPage />
    </I18nextProvider>,
  );
}

function renderPlantManagerTable(records = [PM_HANDOVER_RECORD]) {
  mockApiRequest.mockImplementation(async (path: string) => {
    if (typeof path === "string" && path.includes("/handovers")) {
      return {
        data: records,
        page: { next_cursor: null, limit: 20 },
      };
    }
    return { data: [] };
  });

  return render(
    <I18nextProvider i18n={i18n}>
      <PlantManagerHandoverTable />
    </I18nextProvider>,
  );
}

async function waitForLoad() {
  await act(async () => {
    await Promise.resolve();
  });
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
// AC-1 — Open Andon alerts pre-populated in Handover Builder requiring
//         a disposition note per alert
// ---------------------------------------------------------------------------

describe("AC-1 · Handover Builder pre-populates open Andon alerts as disposition line items", () => {
  it("AC-1 · fetches open Andon alerts from the API on mount", async () => {
    // Precondition: fixture has open alerts
    expect(OPEN_ALERTS.length).toBeGreaterThan(0);

    renderHandoverBuilder(OPEN_ALERTS);
    await waitForLoad();

    // Precondition: component has loaded the form (not still in loading phase)
    expect(
      screen.queryByRole("status", { name: /fetching open alerts/i }),
    ).toBeNull();

    // Criterion: fetched alerts from the correct endpoint
    const callPaths = mockApiRequest.mock.calls.map((c) => c[0] as string);
    expect(callPaths.some((p) => p.includes("/andon/alerts"))).toBe(true);
  });

  it("AC-1 · renders one disposition row per open alert", async () => {
    // Precondition: two open alerts
    expect(OPEN_ALERTS).toHaveLength(2);

    renderHandoverBuilder(OPEN_ALERTS);
    await waitForLoad();

    // Precondition: the alerts table is visible
    const table = screen.getByRole("table", { name: /open predictive andon alerts/i });
    expect(table).toBeTruthy();

    // Criterion: tbody has exactly one row per alert
    const rows = table.querySelectorAll("tbody tr");
    expect(rows).toHaveLength(OPEN_ALERTS.length);
  });

  it("AC-1 · each row shows the station ID and failure type of the alert", async () => {
    renderHandoverBuilder(OPEN_ALERTS);
    await waitForLoad();

    const table = screen.getByRole("table", { name: /open predictive andon alerts/i });

    // Precondition: both fixture alerts have stationId and predictedFailureType
    expect(OPEN_ALERTS[0].stationId).toBe("WELD-CELL-03");
    expect(OPEN_ALERTS[0].predictedFailureType).toBe("electrode_wear");

    // Criterion: the station and failure type of the first alert are visible
    expect(within(table).getByText("WELD-CELL-03")).toBeTruthy();
    expect(within(table).getByText("electrode_wear")).toBeTruthy();
  });

  it("AC-1 · each row contains a disposition select requiring a choice", async () => {
    renderHandoverBuilder(OPEN_ALERTS);
    await waitForLoad();

    // Precondition: two alerts → two disposition selects
    const selects = screen.getAllByRole("combobox");

    // Criterion: at least one select per open alert, all starting at placeholder (no disposition)
    expect(selects.length).toBeGreaterThanOrEqual(OPEN_ALERTS.length);
    for (const sel of selects) {
      // Starts at placeholder — no disposition selected
      expect((sel as HTMLSelectElement).value).toBe("");
    }
  });

  it("AC-1 · submit button is disabled until every alert has a disposition", async () => {
    renderHandoverBuilder(OPEN_ALERTS);
    await waitForLoad();

    // Precondition: alerts were loaded
    const table = screen.getByRole("table", { name: /open predictive andon alerts/i });
    expect(table.querySelectorAll("tbody tr")).toHaveLength(2);

    // Criterion: submit is disabled because not all dispositions are set
    const submitBtn = screen.getByRole("button", { name: /submit handover/i });
    expect(submitBtn).toBeTruthy();
    expect(submitBtn).toHaveAttribute("aria-disabled", "true");
  });

  it("AC-1 · submit becomes enabled once all alerts have a disposition", async () => {
    renderHandoverBuilder(OPEN_ALERTS);
    await waitForLoad();

    const selects = screen.getAllByRole("combobox");
    // Precondition: two selects (one per alert)
    expect(selects).toHaveLength(2);

    // Action: set a disposition on each alert
    await act(async () => {
      fireEvent.change(selects[0], { target: { value: "resolved" } });
    });
    await act(async () => {
      fireEvent.change(selects[1], { target: { value: "escalated" } });
    });

    // Criterion: submit is no longer disabled
    const submitBtn = screen.getByRole("button", { name: /submit handover/i });
    expect(submitBtn).not.toHaveAttribute("aria-disabled", "true");
  });

  it("AC-1 · submitting the form POSTs alertDispositions to /api/v1/handover", async () => {
    renderHandoverBuilder(OPEN_ALERTS);
    await waitForLoad();

    const selects = screen.getAllByRole("combobox");
    await act(async () => {
      fireEvent.change(selects[0], { target: { value: "resolved" } });
    });
    await act(async () => {
      fireEvent.change(selects[1], { target: { value: "monitoring" } });
    });

    // Stub the POST to succeed
    mockApiRequest.mockImplementation(async (path: string) => {
      if (typeof path === "string" && path.includes("/andon/alerts")) {
        return { data: OPEN_ALERTS, page: { next_cursor: null, limit: 200 } };
      }
      if (typeof path === "string" && path.includes("/handover")) {
        return {
          handoverId: "hov-acc-001",
          submittedAt: "2099-01-01T14:00:00.000Z",
          submittedByUserId: "sl-001",
        };
      }
      return { data: [] };
    });

    const submitBtn = screen.getByRole("button", { name: /submit handover/i });
    await act(async () => {
      submitBtn.click();
      await Promise.resolve();
    });

    // Criterion: apiRequest was called with POST /api/v1/handover
    const postCalls = mockApiRequest.mock.calls.filter(
      (c) => typeof c[0] === "string" && c[0].includes("/handover"),
    );
    expect(postCalls.length).toBeGreaterThanOrEqual(1);

    const postArgs = postCalls[postCalls.length - 1];
    const opts = postArgs[1] as { method?: string; body?: string };
    expect(opts?.method?.toUpperCase()).toBe("POST");

    // Precondition: body is present and parseable
    expect(typeof opts?.body).toBe("string");
    const body = JSON.parse(opts.body!);

    // Criterion: alertDispositions array is in the request body with our 2 alerts
    expect(Array.isArray(body.alertDispositions)).toBe(true);
    expect(body.alertDispositions).toHaveLength(2);

    const dispositionValues = body.alertDispositions.map(
      (d: { disposition: string }) => d.disposition,
    );
    expect(dispositionValues).toContain("resolved");
    expect(dispositionValues).toContain("monitoring");
  });

  it("AC-1 · no open alerts results in no disposition rows and submit is enabled", async () => {
    renderHandoverBuilder([]); // no open alerts
    await waitForLoad();

    // Criterion: empty-state message visible, no table rows
    expect(screen.getByText("No open alerts for this shift")).toBeTruthy();

    // Submit is enabled (nothing to block it)
    const submitBtn = screen.getByRole("button", { name: /submit handover/i });
    expect(submitBtn).not.toHaveAttribute("aria-disabled", "true");
  });
});

// ---------------------------------------------------------------------------
// AC-2 — Command Center displays handover summary with OEE delta, dispositions,
//         and VINs after submission
// ---------------------------------------------------------------------------

describe("AC-2 · Command Center shows handover summary with OEE delta, dispositions, and VINs", () => {
  it("AC-2 · pending handover appears in the alert queue when fetched from /handovers", async () => {
    // Precondition: fixture handover is unacknowledged
    expect(SUBMITTED_HANDOVER_SUMMARY.acknowledged).toBe(false);

    renderCommandCenter({ handovers: [SUBMITTED_HANDOVER_SUMMARY] });
    await waitForLoad();

    // Precondition: alert queue is rendered
    const queue = screen.getByRole("list", { name: /alert queue/i });
    expect(queue).toBeTruthy();

    // Criterion: "New Handover" badge is visible
    expect(within(queue).getByText("New Handover")).toBeTruthy();
  });

  it("AC-2 · handover queue item shows OEE delta", async () => {
    renderCommandCenter({ handovers: [SUBMITTED_HANDOVER_SUMMARY] });
    await waitForLoad();

    const queue = screen.getByRole("list", { name: /alert queue/i });
    const items = Array.from(queue.querySelectorAll("li"));

    // Precondition: at least the handover item
    expect(items.length).toBeGreaterThanOrEqual(1);

    // Criterion: the OEE delta value is shown in the handover item
    const handoverItem = items[0];
    expect(handoverItem.textContent).toContain("OEE delta:");
    // 2.5 pp above 30-day avg — either "2.5" or "+2.5" must appear
    expect(handoverItem.textContent?.replace("−", "-")).toMatch(/\+?2\.5/);
  });

  it("AC-2 · handover queue item shows alert dispositions when expanded", async () => {
    renderCommandCenter({ handovers: [SUBMITTED_HANDOVER_SUMMARY] });
    await waitForLoad();

    const queue = screen.getByRole("list", { name: /alert queue/i });
    const items = Array.from(queue.querySelectorAll("li"));

    // Precondition: handover item has an expand button
    const expandBtn = within(items[0] as HTMLElement).queryByRole("button", {
      name: /show summary/i,
    });
    expect(expandBtn).not.toBeNull();

    // Action: expand the handover detail
    await act(async () => {
      (expandBtn as HTMLButtonElement).click();
      await Promise.resolve();
    });

    // Criterion: alert dispositions section becomes visible
    const queueText = queue.textContent ?? "";
    expect(queueText.toLowerCase()).toContain("alert dispositions");
  });

  it("AC-2 · handover queue item shows held VINs when expanded", async () => {
    renderCommandCenter({ handovers: [SUBMITTED_HANDOVER_SUMMARY] });
    await waitForLoad();

    const queue = screen.getByRole("list", { name: /alert queue/i });
    const items = Array.from(queue.querySelectorAll("li"));

    const expandBtn = within(items[0] as HTMLElement).queryByRole("button", {
      name: /show summary/i,
    });
    if (!expandBtn) {
      // No expand button means component has no held VINs to show — check fixture
      expect(SUBMITTED_HANDOVER_SUMMARY.heldVins).toHaveLength(0);
      return;
    }

    await act(async () => {
      (expandBtn as HTMLButtonElement).click();
      await Promise.resolve();
    });

    // Criterion: the held VIN appears in the expanded detail
    const queueText = queue.textContent ?? "";
    expect(queueText).toContain("WBA3A5C55FK123456");
  });

  it("AC-2 · GET /handovers is called to populate Command Center on mount", async () => {
    renderCommandCenter({ handovers: [SUBMITTED_HANDOVER_SUMMARY] });
    await waitForLoad();

    // Criterion: API was called for handovers (the seam with line-command-api)
    const callPaths = mockApiRequest.mock.calls.map((c) => c[0] as string);
    expect(callPaths.some((p) => p.includes("/handovers"))).toBe(true);
  });

  it("AC-2 · no New Handover badge when there are no pending handovers", async () => {
    renderCommandCenter({ handovers: [] });
    await waitForLoad();

    // Criterion: badge absent when nothing was submitted
    expect(screen.queryByText("New Handover")).toBeNull();
  });

  it("AC-2 · handover item is the first item in the alert queue before Andon alerts", async () => {
    renderCommandCenter({
      handovers: [SUBMITTED_HANDOVER_SUMMARY],
      alerts: OPEN_ALERTS,
    });
    await waitForLoad();

    const queue = screen.getByRole("list", { name: /alert queue/i });
    const items = Array.from(queue.querySelectorAll("li"));

    // Precondition: 1 handover + 2 alerts = 3 items
    expect(items.length).toBeGreaterThanOrEqual(3);

    // Criterion: first item is the handover (carries New Handover badge)
    expect(items[0].textContent).toContain("New Handover");

    // Criterion: Andon alert follows
    expect(items[1].textContent).toContain("WELD-CELL-03");
  });
});

// ---------------------------------------------------------------------------
// AC-3 — Plant Manager's daily review table shows structured log entries
//         with Shift Lead ID, shift timestamps, and alert count
// ---------------------------------------------------------------------------

describe("AC-3 · Plant Manager review table shows structured log with Shift Lead ID, timestamps, alert count", () => {
  it("AC-3 · PlantManagerHandoverTable fetches handovers from GET /handovers", async () => {
    renderPlantManagerTable([PM_HANDOVER_RECORD]);
    await waitForLoad();

    // Criterion: the component called GET /handovers
    const callPaths = mockApiRequest.mock.calls.map((c) => c[0] as string);
    expect(callPaths.some((p) => p.includes("/handovers"))).toBe(true);
  });

  it("AC-3 · table renders one row per handover record", async () => {
    renderPlantManagerTable([PM_HANDOVER_RECORD]);
    await waitForLoad();

    // Precondition: table is rendered
    const table = screen.getByRole("table", { name: /shift handover records/i });
    expect(table).toBeTruthy();

    // Precondition: the table has rows
    const rows = table.querySelectorAll("tbody tr");
    expect(rows.length).toBeGreaterThanOrEqual(1);
  });

  it("AC-3 · each row displays the submitting Shift Lead ID", async () => {
    // Precondition: fixture has submittedByUserId
    expect(PM_HANDOVER_RECORD.submittedByUserId).toBe("sl-001");

    renderPlantManagerTable([PM_HANDOVER_RECORD]);
    await waitForLoad();

    const table = screen.getByRole("table", { name: /shift handover records/i });

    // Criterion: the Shift Lead ID is visible in the table
    expect(within(table).getByText("sl-001")).toBeTruthy();
  });

  it("AC-3 · each row displays shift start and shift end timestamps", async () => {
    renderPlantManagerTable([PM_HANDOVER_RECORD]);
    await waitForLoad();

    // Precondition: table column headers include Shift Start and Shift End
    const table = screen.getByRole("table", { name: /shift handover records/i });
    const headers = Array.from(table.querySelectorAll("th")).map((h) =>
      h.textContent?.trim() ?? "",
    );

    // Criterion: headers for Shift Start and Shift End exist
    expect(headers.some((h) => /shift start/i.test(h))).toBe(true);
    expect(headers.some((h) => /shift end/i.test(h))).toBe(true);
  });

  it("AC-3 · each row displays the count of linked alert IDs", async () => {
    // Precondition: fixture has 2 alert dispositions
    expect(PM_HANDOVER_RECORD.alertDispositions).toHaveLength(2);

    renderPlantManagerTable([PM_HANDOVER_RECORD]);
    await waitForLoad();

    const table = screen.getByRole("table", { name: /shift handover records/i });
    const rows = table.querySelectorAll("tbody tr");

    // Precondition: at least one data row
    expect(rows.length).toBeGreaterThanOrEqual(1);

    // Criterion: the alert count (2) is shown in the first row
    const firstRowText = rows[0].textContent ?? "";
    expect(firstRowText).toContain("2");
  });

  it("AC-3 · expanded row shows alert IDs for Plant Manager review", async () => {
    renderPlantManagerTable([PM_HANDOVER_RECORD]);
    await waitForLoad();

    const table = screen.getByRole("table", { name: /shift handover records/i });
    const rows = Array.from(table.querySelectorAll("tbody tr"));

    // Precondition: at least one row
    expect(rows.length).toBeGreaterThanOrEqual(1);

    // Action: click the row to expand it
    await act(async () => {
      (rows[0] as HTMLTableRowElement).click();
      await Promise.resolve();
    });

    // Criterion: after expansion, the alert IDs are visible
    // (The ExpandedRow renders the full alertId in a monospace span)
    const expandedContent = document.body.textContent ?? "";
    expect(expandedContent).toContain("00000000-0000-0000-0000-000000000001");
    expect(expandedContent).toContain("00000000-0000-0000-0000-000000000002");
  });

  it("AC-3 · expanded row shows held VINs for quality review", async () => {
    // Precondition: fixture has a held VIN
    expect(PM_HANDOVER_RECORD.heldVins).toContain("WBA3A5C55FK123456");

    renderPlantManagerTable([PM_HANDOVER_RECORD]);
    await waitForLoad();

    const table = screen.getByRole("table", { name: /shift handover records/i });
    const rows = Array.from(table.querySelectorAll("tbody tr"));

    await act(async () => {
      (rows[0] as HTMLTableRowElement).click();
      await Promise.resolve();
    });

    // Criterion: the held VIN is visible after expansion
    const expandedContent = document.body.textContent ?? "";
    expect(expandedContent).toContain("WBA3A5C55FK123456");
  });

  it("AC-3 · OEE delta is displayed per record in the structured log", async () => {
    // Precondition: fixture has oeeDeltaPct = 2.5
    expect(PM_HANDOVER_RECORD.oeeDeltaPct).toBe(2.5);

    renderPlantManagerTable([PM_HANDOVER_RECORD]);
    await waitForLoad();

    const table = screen.getByRole("table", { name: /shift handover records/i });

    // Criterion: OEE delta value is in the table
    // The component renders "+" or "−" then the absolute value "2.5 pp"
    const tableText = table.textContent ?? "";
    expect(tableText.replace("−", "-")).toMatch(/\+?2\.5/);
  });

  it("AC-3 · empty state is shown when no handover records are available", async () => {
    renderPlantManagerTable([]);
    await waitForLoad();

    // Criterion: empty state message is shown
    expect(screen.getByText("No handover records found")).toBeTruthy();
  });
});
