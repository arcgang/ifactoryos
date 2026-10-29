/**
 * Acceptance tests for US-008 — Station Detail weld trace, threshold overlay, dispatch audit.
 *
 * Criterion coverage (through App navigation entry point — the seam no unit test covers):
 *   AC1 — After navigating from Command Center to Station Detail, weld current and voltage
 *          traces for the last 60 minutes are displayed with the predictive alert threshold
 *          overlaid as a reference line.
 *   AC2 — For a predictive alert, the threshold crossing timestamp and predicted time to
 *          failure are both visible together on the trace.
 *   AC3 — When Dispatch Maintenance is confirmed, acknowledgeAndonAlert is called with the
 *          Shift Lead's JWT-derived userId, the correct alertId, and disposition="resolved",
 *          which triggers the backend audit trail write; the success response is shown.
 *
 * Seam exercised: App (navigation + JWT userId extraction) → StationDetailPage → api.ts
 * The existing StationDetailPage.test.tsx tests each feature in isolation with a userId
 * prop; these tests drive the complete path where the userId originates from the JWT token
 * stored in localStorage and flows through App to the acknowledge API call.
 */

import { render, screen, waitFor, fireEvent, cleanup } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { I18nextProvider } from "react-i18next";
import i18n from "./i18n";
import { App } from "./App";
import * as api from "./services/api";
import type { AndonAlertResponse, AcknowledgeResponse } from "./services/api";

// ---------------------------------------------------------------------------
// Fixtures — far-future timestamps to avoid hardcoded absolutes
// ---------------------------------------------------------------------------

// App.tsx hard-codes this alertId when rendering StationDetailPage on the station-detail route.
const ALERT_ID = "550e8400-e29b-41d4-a716-446655440000";
const THRESHOLD_CROSSING_AT = "2040-06-01T05:55:00.000Z";

const OPEN_ALERT: AndonAlertResponse = {
  alertId: ALERT_ID,
  stationId: "weld-cell-03",
  lineId: "line-b",
  alertType: "predictive",
  predictedFailureType: "weld_tip_wear",
  minutesToPredictedFailure: 12,
  recommendedAction: "Replace weld tip within 12 min",
  status: "open",
  detectedAt: "2040-06-01T06:00:00.000Z",
  correlationId: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
  weldTrace: {
    weld_current_a: [
      { timestamp: "2040-06-01T05:01:00.000Z", value: 8300.0 },
      { timestamp: "2040-06-01T05:30:00.000Z", value: 8500.0 },
      { timestamp: "2040-06-01T05:59:00.000Z", value: 8700.0 },
    ],
    weld_voltage_v: [
      { timestamp: "2040-06-01T05:01:00.000Z", value: 219.0 },
      { timestamp: "2040-06-01T05:30:00.000Z", value: 220.5 },
      { timestamp: "2040-06-01T05:59:00.000Z", value: 222.0 },
    ],
  },
  thresholdCrossingAt: THRESHOLD_CROSSING_AT,
  thresholdValue: 9000.0,
};

const ACK_RESPONSE: AcknowledgeResponse = {
  alertId: ALERT_ID,
  status: "acknowledged",
  acknowledgedAt: "2040-06-01T07:00:00.000Z",
  eventId: "audit-event-acceptance-001",
};

// ---------------------------------------------------------------------------
// JWT helpers — must match the extraction logic in useAuth.ts
// ---------------------------------------------------------------------------

function b64url(obj: unknown): string {
  return btoa(JSON.stringify(obj))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=/g, "");
}

const TOKEN_KEY = "lc_access_token";

function setToken(roles: string[], sub = "shift-lead@bmw-munich.de"): void {
  const header = b64url({ alg: "RS256", typ: "JWT" });
  const payload = b64url({ sub, roles, iat: 1_000_000_000 });
  localStorage.setItem(TOKEN_KEY, `${header}.${payload}.fakesig`);
}

// ---------------------------------------------------------------------------
// Render helper
// ---------------------------------------------------------------------------

function renderApp() {
  return render(
    <I18nextProvider i18n={i18n}>
      <App />
    </I18nextProvider>,
  );
}

// ---------------------------------------------------------------------------
// Setup / teardown
// ---------------------------------------------------------------------------

beforeEach(async () => {
  localStorage.clear();
  await i18n.changeLanguage("en-GB");
  vi.restoreAllMocks();
  // Suppress real network calls from CommandCenterPage (rendered on default route).
  // getAndonAlert / acknowledgeAndonAlert are spied at the module level and never
  // reach fetch; this mock only affects CommandCenterPage's apiRequest calls.
  vi.spyOn(global, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify({ data: [], page: { next_cursor: null, limit: 200 } }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ),
  );
});

afterEach(() => {
  cleanup();
});

// ---------------------------------------------------------------------------
// Navigation helper — waits for RBAC to resolve then navigates to station-detail
// ---------------------------------------------------------------------------

async function navigateToStationDetail(): Promise<void> {
  await waitFor(() => expect(screen.getByText("Station Detail")).toBeTruthy());
  fireEvent.click(screen.getByText("Station Detail"));
}

// ---------------------------------------------------------------------------
// AC1 — Weld current and voltage traces displayed for last 60 minutes
//        with predictive alert threshold overlaid as reference line
// ---------------------------------------------------------------------------

describe("AC1 — Weld trace and threshold overlay displayed after navigation from Command Center", () => {
  it("AC1: weld current series with 3 data points rendered after navigating to Station Detail", async () => {
    // Precondition: fixture has 3 weld_current_a points covering the 60-minute window
    expect(OPEN_ALERT.weldTrace.weld_current_a.length).toBe(3);

    vi.spyOn(api, "getAndonAlert").mockResolvedValue(OPEN_ALERT);
    setToken(["shift_lead"]);
    renderApp();

    await navigateToStationDetail();

    const currentSeries = await waitFor(() => screen.getByTestId("weld-current-series"));
    expect(currentSeries.getAttribute("data-point-count")).toBe("3");
  });

  it("AC1: weld voltage series with 3 data points rendered after navigating to Station Detail", async () => {
    // Precondition: fixture has 3 weld_voltage_v points
    expect(OPEN_ALERT.weldTrace.weld_voltage_v.length).toBe(3);

    vi.spyOn(api, "getAndonAlert").mockResolvedValue(OPEN_ALERT);
    setToken(["shift_lead"]);
    renderApp();

    await navigateToStationDetail();

    const voltageSeries = await waitFor(() => screen.getByTestId("weld-voltage-series"));
    expect(voltageSeries.getAttribute("data-point-count")).toBe("3");
  });

  it("AC1: weld trace aria-label contains 'last 60 minutes' confirming the Beckhoff PLC trace window", async () => {
    vi.spyOn(api, "getAndonAlert").mockResolvedValue(OPEN_ALERT);
    setToken(["shift_lead"]);
    renderApp();

    await navigateToStationDetail();

    const traceView = await waitFor(() =>
      screen.getByRole("img", { name: /last 60 minutes/i }),
    );
    expect(traceView).toBeTruthy();
  });

  it("AC1: threshold value is present in the trace aria-label for a predictive alert (overlay reference)", async () => {
    // Precondition: predictive alert with thresholdValue=9000.0
    expect(OPEN_ALERT.alertType).toBe("predictive");
    expect(OPEN_ALERT.thresholdValue).toBe(9000.0);

    vi.spyOn(api, "getAndonAlert").mockResolvedValue(OPEN_ALERT);
    setToken(["shift_lead"]);
    renderApp();

    await navigateToStationDetail();

    // The WeldTraceView aria-label is built as:
    //   "Current (A) and Voltage (V) — last 60 minutes. Threshold 9000 crossed at <ts>"
    const traceView = await waitFor(() =>
      screen.getByRole("img", { name: /threshold 9000/i }),
    );
    expect(traceView).toBeTruthy();
  });

  it("AC1: threshold-crossing marker with predictive class rendered (reference line present)", async () => {
    vi.spyOn(api, "getAndonAlert").mockResolvedValue(OPEN_ALERT);
    setToken(["shift_lead"]);
    renderApp();

    await navigateToStationDetail();

    const crossing = await waitFor(() => screen.getByTestId("threshold-crossing"));
    expect(crossing.className).toContain("threshold-crossing--predictive");
  });
});

// ---------------------------------------------------------------------------
// AC2 — Threshold crossing point highlighted with timestamp and time to failure
// ---------------------------------------------------------------------------

describe("AC2 — Threshold crossing timestamp and predicted failure countdown highlighted", () => {
  it("AC2: threshold-crossing element carries the exact thresholdCrossingAt timestamp from the alert", async () => {
    // Precondition: alert has thresholdCrossingAt
    expect(OPEN_ALERT.thresholdCrossingAt).toBe(THRESHOLD_CROSSING_AT);

    vi.spyOn(api, "getAndonAlert").mockResolvedValue(OPEN_ALERT);
    setToken(["shift_lead"]);
    renderApp();

    await navigateToStationDetail();

    const crossing = await waitFor(() => screen.getByTestId("threshold-crossing"));
    expect(crossing.getAttribute("data-crossing-at")).toBe(THRESHOLD_CROSSING_AT);
  });

  it("AC2: predicted time to failure (12 min) appears in the threshold-crossing countdown", async () => {
    // Precondition: minutesToPredictedFailure=12 for this predictive alert
    expect(OPEN_ALERT.minutesToPredictedFailure).toBe(12);

    vi.spyOn(api, "getAndonAlert").mockResolvedValue(OPEN_ALERT);
    setToken(["shift_lead"]);
    renderApp();

    await navigateToStationDetail();

    const countdown = await waitFor(() => screen.getByTestId("threshold-crossing-countdown"));
    expect(countdown.textContent).toMatch(/12/);
  });

  it("AC2: crossing timestamp AND countdown are co-located in the same threshold-crossing div", async () => {
    vi.spyOn(api, "getAndonAlert").mockResolvedValue(OPEN_ALERT);
    setToken(["shift_lead"]);
    renderApp();

    await navigateToStationDetail();

    const crossing = await waitFor(() => screen.getByTestId("threshold-crossing"));
    // Both pieces of information must be inside the same highlighted element
    expect(crossing.getAttribute("data-crossing-at")).toBe(THRESHOLD_CROSSING_AT);
    const countdown = crossing.querySelector("[data-testid='threshold-crossing-countdown']");
    expect(countdown).not.toBeNull();
    expect(countdown!.textContent).toMatch(/12/);
  });

  it("AC2: threshold crossing NOT rendered for a reactive alert (nothing to highlight)", async () => {
    const reactiveAlert: AndonAlertResponse = {
      ...OPEN_ALERT,
      alertType: "reactive",
      minutesToPredictedFailure: null,
    };
    vi.spyOn(api, "getAndonAlert").mockResolvedValue(reactiveAlert);
    setToken(["shift_lead"]);
    renderApp();

    await navigateToStationDetail();

    // Wait for trace to confirm the page loaded, then assert no crossing marker
    await waitFor(() => screen.getByTestId("weld-current-series"));
    expect(screen.queryByTestId("threshold-crossing")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// AC3 — Dispatch Maintenance logs event with Shift Lead's userId, alertId,
//        and disposition (which triggers audit trail write + Kafka event)
// ---------------------------------------------------------------------------

describe("AC3 — Dispatch Maintenance sends JWT-derived userId and alertId to acknowledge endpoint", () => {
  it("AC3: acknowledgeAndonAlert receives the JWT sub claim as userId — seam between App JWT and API call", async () => {
    const SHIFT_LEAD_ID = "shift-lead-acceptance@bmw-munich.de";
    vi.spyOn(api, "getAndonAlert").mockResolvedValue(OPEN_ALERT);
    const ackSpy = vi.spyOn(api, "acknowledgeAndonAlert").mockResolvedValue(ACK_RESPONSE);
    // JWT with specific sub — this is what App.tsx passes as userId to StationDetailPage
    setToken(["shift_lead"], SHIFT_LEAD_ID);
    renderApp();

    await navigateToStationDetail();
    await waitFor(() => screen.getByRole("button", { name: /dispatch maintenance/i }));

    fireEvent.click(screen.getByRole("button", { name: /dispatch maintenance/i }));
    await waitFor(() => screen.getByRole("dialog"));
    fireEvent.click(screen.getByRole("button", { name: /yes, dispatch/i }));

    await waitFor(() => expect(ackSpy).toHaveBeenCalledTimes(1));

    const [calledAlertId, calledBody, calledUserId] = ackSpy.mock.calls[0] as [
      string,
      api.AcknowledgeRequest,
      string | undefined,
    ];
    // AC3: alertId matches the station-detail route param set in App.tsx
    expect(calledAlertId).toBe(ALERT_ID);
    // AC3: disposition is "resolved" — the action that triggers MAINTENANCE_DISPATCH audit write
    expect(calledBody.disposition).toBe("resolved");
    // AC3: userId flows from localStorage JWT sub → App userId prop → acknowledgeAndonAlert
    expect(calledUserId).toBe(SHIFT_LEAD_ID);
  });

  it("AC3: success confirmation is shown after dispatch — confirms audit event was accepted", async () => {
    vi.spyOn(api, "getAndonAlert").mockResolvedValue(OPEN_ALERT);
    vi.spyOn(api, "acknowledgeAndonAlert").mockResolvedValue(ACK_RESPONSE);
    setToken(["shift_lead"]);
    renderApp();

    await navigateToStationDetail();
    await waitFor(() => screen.getByRole("button", { name: /dispatch maintenance/i }));

    fireEvent.click(screen.getByRole("button", { name: /dispatch maintenance/i }));
    await waitFor(() => screen.getByRole("dialog"));
    fireEvent.click(screen.getByRole("button", { name: /yes, dispatch/i }));

    // The success role="status" element confirms the backend accepted the dispatch audit event
    const success = await waitFor(() => screen.getByRole("status"));
    expect(success.textContent).toMatch(/maintenance dispatch logged/i);
  });

  it("AC3: Dispatch Maintenance button hidden after success (double-submit prevention for audit idempotency)", async () => {
    vi.spyOn(api, "getAndonAlert").mockResolvedValue(OPEN_ALERT);
    vi.spyOn(api, "acknowledgeAndonAlert").mockResolvedValue(ACK_RESPONSE);
    setToken(["shift_lead"]);
    renderApp();

    await navigateToStationDetail();
    await waitFor(() => screen.getByRole("button", { name: /dispatch maintenance/i }));

    fireEvent.click(screen.getByRole("button", { name: /dispatch maintenance/i }));
    await waitFor(() => screen.getByRole("dialog"));
    fireEvent.click(screen.getByRole("button", { name: /yes, dispatch/i }));

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /dispatch maintenance/i })).toBeNull(),
    );
  });
});
