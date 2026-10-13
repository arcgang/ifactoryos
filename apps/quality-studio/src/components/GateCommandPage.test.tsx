import {
  render,
  screen,
  waitFor,
  fireEvent,
  act,
} from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { I18nextProvider } from "react-i18next";
import i18n from "../i18n";
import { GateCommandPage, CertificateStudioPanel } from "./GateCommandPage";
import * as api from "../services/api";
import * as adoption from "../services/adoption";
import type { GateResultRecord, GateResultListResponse } from "../services/api";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeGateResult(
  overrides: Partial<GateResultRecord> & {
    gateType: api.GateType;
    result: api.GateResult;
  },
): GateResultRecord {
  return {
    gateResultId: `gr-${overrides.gateType}`,
    vin: "WBY73CF080NCH14427",
    gateId: `GATE-${overrides.gateType.toUpperCase()}`,
    measurements: [
      {
        measurementId: `m-${overrides.gateType}-1`,
        name: "Test measurement",
        value: 10,
        unit: "mm",
        toleranceLow: 5,
        toleranceHigh: 15,
        withinTolerance: true,
        reworkThresholdLow: null,
        reworkThresholdHigh: null,
      },
    ],
    measuredAt: "2026-10-13T14:05:00Z",
    receivedFromOpcenterAt: "2026-10-13T14:07:00Z",
    opcenterEventId: null,
    ...overrides,
  };
}

const ALL_PASS_RESULTS: GateResultRecord[] = [
  makeGateResult({ gateType: "dimensional", result: "pass" }),
  makeGateResult({ gateType: "leak", result: "pass" }),
  makeGateResult({ gateType: "electrical", result: "pass" }),
  makeGateResult({ gateType: "nvh", result: "pass" }),
  makeGateResult({ gateType: "adas", result: "pass" }),
];

const COND_PASS_NVH_RESULTS: GateResultRecord[] = [
  makeGateResult({ gateType: "dimensional", result: "pass" }),
  makeGateResult({ gateType: "leak", result: "pass" }),
  makeGateResult({ gateType: "electrical", result: "pass" }),
  makeGateResult({
    gateType: "nvh",
    result: "conditional_pass",
    measurements: [
      {
        measurementId: "m-nvh-road-noise",
        name: "Road noise",
        value: 68.4,
        unit: "dB",
        toleranceLow: 58.0,
        toleranceHigh: 66.0,
        withinTolerance: false,
        reworkThresholdLow: 50.0,
        reworkThresholdHigh: 72.0,
      },
      {
        measurementId: "m-nvh-wind",
        name: "Wind noise",
        value: 61.2,
        unit: "dB",
        toleranceLow: 55.0,
        toleranceHigh: 65.0,
        withinTolerance: true,
        reworkThresholdLow: null,
        reworkThresholdHigh: null,
      },
    ],
  }),
  makeGateResult({ gateType: "adas", result: "pass" }),
];

function makeListResponse(
  data: GateResultRecord[],
): GateResultListResponse {
  return { data, page: { next_cursor: null, limit: 200 } };
}

function renderPage(vin = "WBY73CF080NCH14427") {
  return render(
    <I18nextProvider i18n={i18n}>
      <GateCommandPage vin={vin} />
    </I18nextProvider>,
  );
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

beforeEach(async () => {
  await i18n.changeLanguage("en-GB");
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

// ---------------------------------------------------------------------------
// AC1: loading → populated with all 5 gate results
// ---------------------------------------------------------------------------

describe("Gate Command — loading and populated states", () => {
  it("shows loading state on mount before results arrive", async () => {
    vi.spyOn(api, "listGateResults").mockResolvedValue(
      makeListResponse([]),
    );

    renderPage();

    expect(screen.getByTestId("loading-state")).toBeTruthy();
  });

  it("renders health banner and 5 gate cards once all results are present", async () => {
    vi.spyOn(api, "listGateResults").mockResolvedValue(
      makeListResponse(ALL_PASS_RESULTS),
    );

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("health-banner")).toBeTruthy();
    });

    expect(screen.getByTestId("gate-cards")).toBeTruthy();
    expect(screen.getByTestId("gate-card-dimensional")).toBeTruthy();
    expect(screen.getByTestId("gate-card-leak")).toBeTruthy();
    expect(screen.getByTestId("gate-card-electrical")).toBeTruthy();
    expect(screen.getByTestId("gate-card-nvh")).toBeTruthy();
    expect(screen.getByTestId("gate-card-adas")).toBeTruthy();
  });

  it("computes overall pass when all 5 results are pass", async () => {
    vi.spyOn(api, "listGateResults").mockResolvedValue(
      makeListResponse(ALL_PASS_RESULTS),
    );

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("health-banner")).toBeTruthy();
    });

    const banner = screen.getByTestId("health-banner");
    expect(banner.textContent).toContain("Pass");
  });

  it("shows per-gate pass badges for all 5 gates when all pass", async () => {
    vi.spyOn(api, "listGateResults").mockResolvedValue(
      makeListResponse(ALL_PASS_RESULTS),
    );

    renderPage();

    await waitFor(() =>
      expect(screen.getByTestId("per-gate-badge-dimensional")).toBeTruthy(),
    );

    const gateTypes: api.GateType[] = [
      "dimensional",
      "leak",
      "electrical",
      "nvh",
      "adas",
    ];
    for (const gt of gateTypes) {
      const badge = screen.getByTestId(`per-gate-badge-${gt}`);
      expect(badge.textContent).toContain("Pass");
    }
  });

  it("shows conditional_pass badge on NVH gate when NVH is conditional_pass", async () => {
    vi.spyOn(api, "listGateResults").mockResolvedValue(
      makeListResponse(COND_PASS_NVH_RESULTS),
    );

    renderPage();

    await waitFor(() =>
      expect(screen.getByTestId("per-gate-badge-nvh")).toBeTruthy(),
    );

    const nvhBadge = screen.getByTestId("per-gate-badge-nvh");
    expect(nvhBadge.textContent).toContain("Conditional Pass");
  });

  it("shows overall conditional_pass when at least one gate is conditional_pass", async () => {
    vi.spyOn(api, "listGateResults").mockResolvedValue(
      makeListResponse(COND_PASS_NVH_RESULTS),
    );

    renderPage();

    await waitFor(() =>
      expect(screen.getByTestId("health-banner")).toBeTruthy(),
    );

    const banner = screen.getByTestId("health-banner");
    expect(banner.textContent).toContain("Conditional Pass");
  });
});

// ---------------------------------------------------------------------------
// AC2: conditional pass anomaly highlight
// ---------------------------------------------------------------------------

describe("Gate Command — conditional pass anomaly highlight", () => {
  it("renders anomaly highlight for out-of-tolerance measurement", async () => {
    vi.spyOn(api, "listGateResults").mockResolvedValue(
      makeListResponse(COND_PASS_NVH_RESULTS),
    );

    renderPage();

    await waitFor(() =>
      expect(screen.getByTestId("anomaly-highlight-m-nvh-road-noise")).toBeTruthy(),
    );
  });

  it("shows the actual value in the anomaly highlight", async () => {
    vi.spyOn(api, "listGateResults").mockResolvedValue(
      makeListResponse(COND_PASS_NVH_RESULTS),
    );

    renderPage();

    await waitFor(() =>
      expect(screen.getByTestId("anomaly-highlight-m-nvh-road-noise")).toBeTruthy(),
    );

    const highlight = screen.getByTestId("anomaly-highlight-m-nvh-road-noise");
    expect(highlight.textContent).toContain("68.4");
  });

  it("shows tolerance low bound in anomaly highlight", async () => {
    vi.spyOn(api, "listGateResults").mockResolvedValue(
      makeListResponse(COND_PASS_NVH_RESULTS),
    );

    renderPage();

    await waitFor(() =>
      expect(screen.getByTestId("anomaly-highlight-m-nvh-road-noise")).toBeTruthy(),
    );

    const highlight = screen.getByTestId("anomaly-highlight-m-nvh-road-noise");
    expect(highlight.textContent).toContain("58");
  });

  it("shows tolerance high bound in anomaly highlight", async () => {
    vi.spyOn(api, "listGateResults").mockResolvedValue(
      makeListResponse(COND_PASS_NVH_RESULTS),
    );

    renderPage();

    await waitFor(() =>
      expect(screen.getByTestId("anomaly-highlight-m-nvh-road-noise")).toBeTruthy(),
    );

    const highlight = screen.getByTestId("anomaly-highlight-m-nvh-road-noise");
    expect(highlight.textContent).toContain("66");
  });

  it("does NOT render anomaly highlight for within-tolerance measurements", async () => {
    vi.spyOn(api, "listGateResults").mockResolvedValue(
      makeListResponse(COND_PASS_NVH_RESULTS),
    );

    renderPage();

    await waitFor(() =>
      expect(screen.getByTestId("gate-card-nvh")).toBeTruthy(),
    );

    // Wind noise is within tolerance — no highlight expected
    expect(
      screen.queryByTestId("anomaly-highlight-m-nvh-wind"),
    ).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// AC3: Certify button disabled when fewer than 5 gate types present
// ---------------------------------------------------------------------------

describe("Gate Command — Certify button state", () => {
  it("Certify button is absent/disabled in partial state (fewer than 5 gates)", async () => {
    const partialResults = ALL_PASS_RESULTS.slice(0, 3);
    vi.spyOn(api, "listGateResults").mockResolvedValue(
      makeListResponse(partialResults),
    );

    renderPage();

    await waitFor(() =>
      expect(screen.getByTestId("partial-state")).toBeTruthy(),
    );

    // Certify bar only appears in populated state
    expect(screen.queryByTestId("certify-button")).toBeNull();
  });

  it("Certify button is enabled when all 5 gate types are present", async () => {
    vi.spyOn(api, "listGateResults").mockResolvedValue(
      makeListResponse(ALL_PASS_RESULTS),
    );

    renderPage();

    await waitFor(() =>
      expect(screen.getByTestId("certify-button")).toBeTruthy(),
    );

    const certBtn = screen.getByTestId("certify-button");
    expect(certBtn.getAttribute("disabled")).toBeNull();
    expect(certBtn.getAttribute("aria-disabled")).toBe("false");
  });

  it("renders certificate panel on successful certify", async () => {
    vi.spyOn(api, "listGateResults").mockResolvedValue(
      makeListResponse(ALL_PASS_RESULTS),
    );
    vi.spyOn(api, "certifyVehicle").mockResolvedValue({
      certificateId: "cert-001",
      fabricTxHash: "0xabc123",
      vin: "WBY73CF080NCH14427",
      issuedAt: "2026-10-13T14:10:00Z",
      validatorId: "user-001",
      healthScore: { overall: "pass", perGate: { dimensional: "pass", leak: "pass", electrical: "pass", nvh: "pass", adas: "pass" } },
      gateResultsSnapshot: ALL_PASS_RESULTS,
    });
    vi.spyOn(adoption, "emitAdoptionEvent").mockResolvedValue(undefined);

    renderPage();

    await waitFor(() =>
      expect(screen.getByTestId("certify-button")).toBeTruthy(),
    );

    fireEvent.click(screen.getByTestId("certify-button"));

    await waitFor(() =>
      expect(screen.getByTestId("certificate-panel")).toBeTruthy(),
    );

    expect(screen.getByTestId("cert-vin").textContent).toBe("WBY73CF080NCH14427");
    expect(screen.getByTestId("cert-fabric-hash").textContent).toContain("0xabc123");
  });
});

// ---------------------------------------------------------------------------
// Timeout state
// ---------------------------------------------------------------------------

describe("Gate Command — timeout state", () => {
  it("shows timeout alert after 10 minutes without all 5 results", async () => {
    vi.useFakeTimers();
    const partialResults = ALL_PASS_RESULTS.slice(0, 4);
    vi.spyOn(api, "listGateResults").mockResolvedValue(
      makeListResponse(partialResults),
    );

    renderPage();

    // Wait for initial fetch to resolve and render partial state
    await waitFor(() =>
      expect(screen.getByTestId("partial-state")).toBeTruthy(),
    );

    // Advance past the 10-minute timeout boundary
    await act(async () => {
      vi.advanceTimersByTime(10 * 60 * 1000 + 100);
    });

    await waitFor(() =>
      expect(screen.getByTestId("timeout-state")).toBeTruthy(),
    );

    expect(screen.getByTestId("override-panel")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Adoption event payload
// ---------------------------------------------------------------------------

describe("Gate Command — adoption events", () => {
  it("emits GATE_COMMAND_OPEN adoption event on mount", async () => {
    vi.spyOn(api, "listGateResults").mockResolvedValue(
      makeListResponse([]),
    );
    const emitSpy = vi.spyOn(adoption, "emitAdoptionEvent").mockResolvedValue(undefined);

    renderPage();

    await waitFor(() =>
      expect(emitSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          action: "GATE_COMMAND_OPEN",
          role: "QUALITY_GATE_VALIDATOR",
          vin: "WBY73CF080NCH14427",
        }),
      ),
    );
  });

  it("emits CERTIFY adoption event with fabricTxHash on successful certify", async () => {
    vi.spyOn(api, "listGateResults").mockResolvedValue(
      makeListResponse(ALL_PASS_RESULTS),
    );
    vi.spyOn(api, "certifyVehicle").mockResolvedValue({
      certificateId: "cert-002",
      fabricTxHash: "0xdeadbeef",
      vin: "WBY73CF080NCH14427",
      issuedAt: "2026-10-13T14:10:00Z",
      validatorId: "user-002",
      healthScore: { overall: "pass", perGate: { dimensional: "pass", leak: "pass", electrical: "pass", nvh: "pass", adas: "pass" } },
      gateResultsSnapshot: ALL_PASS_RESULTS,
    });
    const emitSpy = vi.spyOn(adoption, "emitAdoptionEvent").mockResolvedValue(undefined);

    renderPage();

    await waitFor(() =>
      expect(screen.getByTestId("certify-button")).toBeTruthy(),
    );

    fireEvent.click(screen.getByTestId("certify-button"));

    await waitFor(() =>
      expect(emitSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          action: "CERTIFY",
          role: "QUALITY_GATE_VALIDATOR",
          vin: "WBY73CF080NCH14427",
          fabricTxHash: "0xdeadbeef",
        }),
      ),
    );
  });
});

// ---------------------------------------------------------------------------
// Certificate Studio panel
// ---------------------------------------------------------------------------

describe("CertificateStudioPanel", () => {
  it("renders VIN, fabricTxHash, issuedAt, validatorId, and health score", () => {
    const cert: api.CertifyResponse = {
      certificateId: "cert-003",
      fabricTxHash: "0xf00baa",
      vin: "WBY73CF080NCH14427",
      issuedAt: "2026-10-13T14:15:00Z",
      validatorId: "validator-99",
      healthScore: {
        overall: "conditional_pass",
        perGate: {
          dimensional: "pass",
          leak: "pass",
          electrical: "pass",
          nvh: "conditional_pass",
          adas: "pass",
        },
      },
      gateResultsSnapshot: COND_PASS_NVH_RESULTS,
    };

    render(
      <I18nextProvider i18n={i18n}>
        <CertificateStudioPanel cert={cert} />
      </I18nextProvider>,
    );

    expect(screen.getByTestId("cert-vin").textContent).toBe("WBY73CF080NCH14427");
    expect(screen.getByTestId("cert-fabric-hash").textContent).toContain("0xf00baa");
    expect(screen.getByTestId("cert-validator-id").textContent).toBe("validator-99");
    expect(screen.getByTestId("cert-health-score").textContent).toBe("conditional_pass");
    expect(screen.getByTestId("cert-gate-nvh")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// i18n language switching
// ---------------------------------------------------------------------------

describe("Gate Command — i18n", () => {
  it("renders German strings when language is de-DE", async () => {
    await i18n.changeLanguage("de-DE");
    vi.spyOn(api, "listGateResults").mockResolvedValue(
      makeListResponse([]),
    );

    renderPage();

    expect(screen.getByTestId("loading-state").textContent).toContain("Gate-Ergebnisse");
  });

  it("renders English strings when language is en-GB", async () => {
    await i18n.changeLanguage("en-GB");
    vi.spyOn(api, "listGateResults").mockResolvedValue(
      makeListResponse([]),
    );

    renderPage();

    expect(screen.getByTestId("loading-state").textContent).toContain("gate results");
  });
});
