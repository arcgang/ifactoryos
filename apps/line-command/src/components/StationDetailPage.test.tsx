import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { I18nextProvider } from "react-i18next";
import i18n from "../i18n";
import { StationDetailPage } from "./StationDetailPage";
import * as api from "../services/api";
import type { AndonAlertResponse, AcknowledgeResponse } from "../services/api";

// ---------------------------------------------------------------------------
// Fixtures — far-future timestamps to avoid hardcoded absolutes
// ---------------------------------------------------------------------------

const ALERT_ID = "550e8400-e29b-41d4-a716-446655440000";
const DETECTED_AT = "2040-06-01T06:00:00.000Z";
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
  detectedAt: DETECTED_AT,
  correlationId: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
  weldTrace: {
    weld_current_a: [
      { timestamp: "2040-06-01T05:59:00.000Z", value: 8500.0 },
      { timestamp: "2040-06-01T05:59:30.000Z", value: 8600.0 },
    ],
    weld_voltage_v: [
      { timestamp: "2040-06-01T05:59:00.000Z", value: 220.0 },
      { timestamp: "2040-06-01T05:59:30.000Z", value: 221.5 },
    ],
  },
  thresholdCrossingAt: THRESHOLD_CROSSING_AT,
  thresholdValue: 9000.0,
};

const ACK_RESPONSE: AcknowledgeResponse = {
  alertId: ALERT_ID,
  status: "acknowledged",
  acknowledgedAt: "2040-06-01T07:00:00.000Z",
  eventId: "event-uuid-001",
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderPage(props: Partial<Parameters<typeof StationDetailPage>[0]> = {}) {
  return render(
    <I18nextProvider i18n={i18n}>
      <StationDetailPage alertId={ALERT_ID} {...props} />
    </I18nextProvider>,
  );
}

beforeEach(async () => {
  vi.restoreAllMocks();
  await i18n.changeLanguage("en-GB");
});

// ---------------------------------------------------------------------------
// Loading state
// ---------------------------------------------------------------------------

describe("StationDetailPage — loading", () => {
  it("shows loading text while the alert fetch is in flight", () => {
    vi.spyOn(api, "getAndonAlert").mockReturnValue(new Promise(() => undefined));
    renderPage();
    expect(screen.getByRole("status")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Alert display
// ---------------------------------------------------------------------------

describe("StationDetailPage — alert fetch returns trace arrays and crossing fields", () => {
  it("renders weld current series with 2 data points", async () => {
    vi.spyOn(api, "getAndonAlert").mockResolvedValue(OPEN_ALERT);
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId("weld-current-series")).toBeTruthy(),
    );
    const el = screen.getByTestId("weld-current-series");
    expect(el.getAttribute("data-point-count")).toBe("2");
  });

  it("renders weld voltage series with 2 data points", async () => {
    vi.spyOn(api, "getAndonAlert").mockResolvedValue(OPEN_ALERT);
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId("weld-voltage-series")).toBeTruthy(),
    );
    const el = screen.getByTestId("weld-voltage-series");
    expect(el.getAttribute("data-point-count")).toBe("2");
  });

  it("renders threshold crossing at the exact crossing timestamp", async () => {
    vi.spyOn(api, "getAndonAlert").mockResolvedValue(OPEN_ALERT);
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId("threshold-crossing")).toBeTruthy(),
    );
    const el = screen.getByTestId("threshold-crossing");
    expect(el.getAttribute("data-crossing-at")).toBe(THRESHOLD_CROSSING_AT);
  });

  it("renders minutesToPredictedFailure value of 12", async () => {
    vi.spyOn(api, "getAndonAlert").mockResolvedValue(OPEN_ALERT);
    renderPage();
    await waitFor(() => expect(screen.getByText(/12/)).toBeTruthy());
  });

  it("renders recommended action text", async () => {
    vi.spyOn(api, "getAndonAlert").mockResolvedValue(OPEN_ALERT);
    renderPage();
    await waitFor(() =>
      expect(screen.getByText(/Replace weld tip within 12 min/)).toBeTruthy(),
    );
  });
});

// ---------------------------------------------------------------------------
// Fetch error
// ---------------------------------------------------------------------------

describe("StationDetailPage — fetch error", () => {
  it("shows error message when fetch rejects", async () => {
    vi.spyOn(api, "getAndonAlert").mockRejectedValue(new Error("network error"));
    renderPage();
    await waitFor(() =>
      expect(screen.getByRole("alert")).toBeTruthy(),
    );
  });
});

// ---------------------------------------------------------------------------
// Dispatch button — confirm → success
// ---------------------------------------------------------------------------

describe("StationDetailPage — dispatch button: open → acknowledged", () => {
  it("shows Dispatch Maintenance button for an open alert", async () => {
    vi.spyOn(api, "getAndonAlert").mockResolvedValue(OPEN_ALERT);
    renderPage();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Dispatch Maintenance/i })).toBeTruthy(),
    );
  });

  it("opens confirmation dialog when Dispatch Maintenance is clicked", async () => {
    vi.spyOn(api, "getAndonAlert").mockResolvedValue(OPEN_ALERT);
    vi.spyOn(api, "acknowledgeAndonAlert").mockResolvedValue(ACK_RESPONSE);
    renderPage();
    await waitFor(() =>
      screen.getByRole("button", { name: /Dispatch Maintenance/i }),
    );
    fireEvent.click(screen.getByRole("button", { name: /Dispatch Maintenance/i }));
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("calls acknowledgeAndonAlert with alertId and resolved disposition on confirm", async () => {
    vi.spyOn(api, "getAndonAlert").mockResolvedValue(OPEN_ALERT);
    const ackSpy = vi.spyOn(api, "acknowledgeAndonAlert").mockResolvedValue(ACK_RESPONSE);
    renderPage({ userId: "shift-lead@bmwgroup.net" });
    await waitFor(() =>
      screen.getByRole("button", { name: /Dispatch Maintenance/i }),
    );
    fireEvent.click(screen.getByRole("button", { name: /Dispatch Maintenance/i }));
    fireEvent.click(screen.getByRole("button", { name: /Yes, Dispatch/i }));
    await waitFor(() => expect(ackSpy).toHaveBeenCalledTimes(1));
    expect(ackSpy).toHaveBeenCalledWith(
      ALERT_ID,
      { disposition: "resolved" },
      "shift-lead@bmwgroup.net",
    );
  });

  it("shows success message after acknowledged dispatch", async () => {
    vi.spyOn(api, "getAndonAlert").mockResolvedValue(OPEN_ALERT);
    vi.spyOn(api, "acknowledgeAndonAlert").mockResolvedValue(ACK_RESPONSE);
    renderPage();
    await waitFor(() =>
      screen.getByRole("button", { name: /Dispatch Maintenance/i }),
    );
    fireEvent.click(screen.getByRole("button", { name: /Dispatch Maintenance/i }));
    fireEvent.click(screen.getByRole("button", { name: /Yes, Dispatch/i }));
    await waitFor(() =>
      expect(screen.getByRole("status")).toBeTruthy(),
    );
  });

  it("hides dispatch button after successful dispatch", async () => {
    vi.spyOn(api, "getAndonAlert").mockResolvedValue(OPEN_ALERT);
    vi.spyOn(api, "acknowledgeAndonAlert").mockResolvedValue(ACK_RESPONSE);
    renderPage();
    await waitFor(() =>
      screen.getByRole("button", { name: /Dispatch Maintenance/i }),
    );
    fireEvent.click(screen.getByRole("button", { name: /Dispatch Maintenance/i }));
    fireEvent.click(screen.getByRole("button", { name: /Yes, Dispatch/i }));
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /Dispatch Maintenance/i })).toBeNull(),
    );
  });
});

// ---------------------------------------------------------------------------
// Dispatch button — cancel dialog
// ---------------------------------------------------------------------------

describe("StationDetailPage — dispatch button: cancel", () => {
  it("dismisses confirm dialog when cancel is clicked", async () => {
    vi.spyOn(api, "getAndonAlert").mockResolvedValue(OPEN_ALERT);
    renderPage();
    await waitFor(() =>
      screen.getByRole("button", { name: /Dispatch Maintenance/i }),
    );
    fireEvent.click(screen.getByRole("button", { name: /Dispatch Maintenance/i }));
    expect(screen.getByRole("dialog")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Cancel/i }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Dispatch button — API error
// ---------------------------------------------------------------------------

describe("StationDetailPage — dispatch API error", () => {
  it("shows error message when acknowledgeAndonAlert rejects", async () => {
    vi.spyOn(api, "getAndonAlert").mockResolvedValue(OPEN_ALERT);
    vi.spyOn(api, "acknowledgeAndonAlert").mockRejectedValue(new Error("server error"));
    renderPage();
    await waitFor(() =>
      screen.getByRole("button", { name: /Dispatch Maintenance/i }),
    );
    fireEvent.click(screen.getByRole("button", { name: /Dispatch Maintenance/i }));
    fireEvent.click(screen.getByRole("button", { name: /Yes, Dispatch/i }));
    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
  });
});

// ---------------------------------------------------------------------------
// Already-acknowledged alert — no dispatch button
// ---------------------------------------------------------------------------

describe("StationDetailPage — already-acknowledged alert", () => {
  it("does not render Dispatch Maintenance for an acknowledged alert", async () => {
    const acknowledgedAlert: AndonAlertResponse = { ...OPEN_ALERT, status: "acknowledged" };
    vi.spyOn(api, "getAndonAlert").mockResolvedValue(acknowledgedAlert);
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId("weld-current-series")).toBeTruthy(),
    );
    expect(screen.queryByRole("button", { name: /Dispatch Maintenance/i })).toBeNull();
  });
});
