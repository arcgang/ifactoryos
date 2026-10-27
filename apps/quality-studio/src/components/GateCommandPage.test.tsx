import type { ReactNode } from "react";
import {
  render,
  screen,
  waitFor,
  fireEvent,
  act,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { I18nextProvider } from "react-i18next";
import i18n from "../i18n";
import { GateCommandPage, GateCompletenessCard, CertificateStudioPanel } from "./GateCommandPage";
import * as api from "../services/api";
import * as adoption from "../services/adoption";
import * as adoptionEvents from "../instrumentation/adoptionEvents";
import type { GateResultRecord, GateResultListResponse } from "../services/api";
import type { GateResultListResponse as GateResultListResponseType } from "../types/gateResults";

// ---------------------------------------------------------------------------
// Helpers for GateCommandPage tests
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
// Helpers for GateCompletenessCard tests
// ---------------------------------------------------------------------------

function makeGateResultSimple(
  gateType: string,
  result: "pass" | "conditional_pass" | "fail",
  index: number,
): object {
  return {
    gateResultId: `gr-${index}`,
    vin: "WBA31AB09NCG47381",
    gateId: `GATE-${gateType.toUpperCase().slice(0, 3)}`,
    gateType,
    result,
    measurements: [],
    measuredAt: "2026-10-12T14:21:05Z",
    receivedFromOpcenterAt: "2026-10-12T14:21:10Z",
    opcenterEventId: null,
  };
}

function makeResponse(gateTypes: string[]): GateResultListResponseType {
  return {
    data: gateTypes.map((gt, i) => makeGateResultSimple(gt, "pass", i) as never),
    page: { next_cursor: null, limit: 50 },
  };
}

const VIN = "WBA31AB09NCG47381";

function Wrapper({ children }: { children: ReactNode }) {
  return (
    <I18nextProvider i18n={i18n}>
      {children}
    </I18nextProvider>
  );
}

function renderCard(vin = VIN) {
  return render(
    <Wrapper>
      <GateCompletenessCard vin={vin} />
    </Wrapper>,
  );
}

// ---------------------------------------------------------------------------
// Setup for GateCommandPage tests
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

// ---------------------------------------------------------------------------
// GateCompletenessCard — completeness aggregation
// ---------------------------------------------------------------------------

describe("GateCompletenessCard — completeness aggregation", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("en-GB");
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("shows loading state initially", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise(() => {})), // never resolves
    );
    renderCard();
    expect(screen.getByText("Loading gate results…")).toBeTruthy();
  });

  it("shows error state when fetch fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new Error("Network error"))),
    );
    renderCard();
    await waitFor(() =>
      expect(screen.getByText("Error loading gate results. Please retry.")).toBeTruthy(),
    );
  });

  it("0/5 — all five gate types show as Pending", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(makeResponse([])),
        }),
      ),
    );
    renderCard();
    await waitFor(() => expect(screen.getAllByText("Pending")).toHaveLength(5));
    expect(screen.getByText("0 of 5 gate results received from OpCenter")).toBeTruthy();
  });

  it("3/5 — received types show their result, absent types show Pending", async () => {
    const response = makeResponse(["dimensional", "leak", "electrical"]);
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(response),
        }),
      ),
    );
    renderCard();
    await waitFor(() =>
      expect(screen.getByText("3 of 5 gate results received from OpCenter")).toBeTruthy(),
    );
    expect(screen.getAllByText("Pass")).toHaveLength(3);
    expect(screen.getAllByText("Pending")).toHaveLength(2);
  });

  it("5/5 — shows 'Health score complete' and no Pending badges", async () => {
    const response = makeResponse([
      "dimensional",
      "leak",
      "electrical",
      "nvh",
      "adas",
    ]);
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(response),
        }),
      ),
    );
    renderCard();
    await waitFor(() =>
      expect(screen.getByText("Health score complete")).toBeTruthy(),
    );
    expect(screen.queryAllByText("Pending")).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// GateCompletenessCard — Certify button disabled / enabled
// ---------------------------------------------------------------------------

describe("GateCompletenessCard — Certify button", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("en-GB");
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("Certify button has aria-disabled when gate types are absent", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(makeResponse(["dimensional", "leak"])),
        }),
      ),
    );
    renderCard();
    await waitFor(() =>
      expect(screen.getByText("2 of 5 gate results received from OpCenter")).toBeTruthy(),
    );
    const btn = screen.getByRole("button", { name: "Certify" });
    expect(btn.getAttribute("aria-disabled")).toBe("true");
  });

  it("Certify button is not aria-disabled when all 5 gate types are present", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve(
              makeResponse(["dimensional", "leak", "electrical", "nvh", "adas"]),
            ),
        }),
      ),
    );
    renderCard();
    await waitFor(() =>
      expect(screen.getByText("Health score complete")).toBeTruthy(),
    );
    const btn = screen.getByRole("button", { name: "Certify" });
    expect(btn.getAttribute("aria-disabled")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// GateCompletenessCard — tooltip content lists absent gate names
// ---------------------------------------------------------------------------

describe("GateCompletenessCard — tooltip", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("en-GB");
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("tooltip lists the absent gate type names", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve(makeResponse(["dimensional", "leak", "electrical"])),
        }),
      ),
    );
    renderCard();
    await waitFor(() =>
      expect(screen.getByText("3 of 5 gate results received from OpCenter")).toBeTruthy(),
    );
    // Tooltip element should contain the pending gate names
    const tooltip = screen.getByRole("tooltip");
    expect(tooltip.textContent).toContain("NVH");
    expect(tooltip.textContent).toContain("ADAS");
  });
});

// ---------------------------------------------------------------------------
// GateCompletenessCard — timeout countdown state machine
// ---------------------------------------------------------------------------

describe("GateCompletenessCard — timeout countdown", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("en-GB");
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("shows timeout alert after the timeout threshold elapses with pending gates", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(makeResponse(["dimensional"])),
        }),
      ),
    );
    renderCard();
    await waitFor(() =>
      expect(screen.getByText("1 of 5 gate results received from OpCenter")).toBeTruthy(),
    );

    // Advance 10 minutes + 1 second past the 600-second default timeout
    await act(async () => {
      vi.advanceTimersByTime(601_000);
    });

    await waitFor(() =>
      expect(screen.getByRole("alert")).toBeTruthy(),
    );

    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("Gate station timeout");
  });

  it("polling stops after timeout (fetch count does not increase after timeout)", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(makeResponse([])),
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderCard();

    await waitFor(() =>
      expect(screen.getByText("0 of 5 gate results received from OpCenter")).toBeTruthy(),
    );

    // Trigger timeout — polls fire every 10s up to ~600s, then stop
    await act(async () => {
      vi.advanceTimersByTime(601_000);
    });

    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());

    // Capture call count AFTER timeout
    const callsAtTimeout = fetchMock.mock.calls.length;

    // Advance another 30s — no extra polls should happen after timeout
    await act(async () => {
      vi.advanceTimersByTime(30_000);
    });

    expect(fetchMock.mock.calls.length).toBe(callsAtTimeout);
  });
});

// ---------------------------------------------------------------------------
// GateCompletenessCard — Manual Override modal
// ---------------------------------------------------------------------------

describe("GateCompletenessCard — Manual Override modal", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("en-GB");
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  function makeUser() {
    return userEvent.setup({ advanceTimers: (ms) => vi.advanceTimersByTime(ms) });
  }

  async function renderWithTimeout() {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        if (typeof url === "string" && url.includes("gate-results")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve(makeResponse(["dimensional"])),
          });
        }
        // supervisor approval endpoint — not yet deployed
        return Promise.reject(new Error("Not found"));
      }),
    );
    renderCard();

    await waitFor(() =>
      expect(screen.getByText("1 of 5 gate results received from OpCenter")).toBeTruthy(),
    );

    await act(async () => {
      vi.advanceTimersByTime(601_000);
    });

    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
  }

  it("Manual Override button appears after timeout", async () => {
    await renderWithTimeout();
    expect(screen.getByRole("button", { name: "Manual Override" })).toBeTruthy();
  });

  it("clicking Manual Override opens dialog with correct role and label", async () => {
    const user = makeUser();
    await renderWithTimeout();
    await user.click(screen.getByRole("button", { name: "Manual Override" }));
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(
      screen.getByRole("heading", {
        name: "Manual Override — Gate Certification",
      }),
    ).toBeTruthy();
  });

  it("justification textarea is present with required label", async () => {
    const user = makeUser();
    await renderWithTimeout();
    await user.click(screen.getByRole("button", { name: "Manual Override" }));
    const textarea = screen.getByRole("textbox", { name: /Justification/i });
    expect(textarea).toBeTruthy();
    expect(textarea.getAttribute("aria-required")).toBe("true");
  });

  it("shows validation error when justification is fewer than 20 characters", async () => {
    const user = makeUser();
    await renderWithTimeout();
    await user.click(screen.getByRole("button", { name: "Manual Override" }));
    const textarea = screen.getByRole("textbox", { name: /Justification/i });
    await user.type(textarea, "Too short");
    await user.click(screen.getByRole("button", { name: "Request Supervisor Approval" }));
    expect(
      screen.getByText("Justification must be at least 20 characters."),
    ).toBeTruthy();
  });

  it("shows pending state after Request Supervisor Approval with valid justification", async () => {
    const user = makeUser();
    await renderWithTimeout();
    await user.click(screen.getByRole("button", { name: "Manual Override" }));
    const textarea = screen.getByRole("textbox", { name: /Justification/i });
    await user.type(textarea, "This is a long enough justification text to pass validation.");
    await user.click(screen.getByRole("button", { name: "Request Supervisor Approval" }));
    // Button is now disabled — check it's present and has aria-disabled
    await waitFor(() => {
      const btn = screen.getByRole("button", { name: /Pending approval/i });
      expect(btn).toBeTruthy();
      expect(btn.getAttribute("aria-disabled")).toBe("true");
    });
  });

  it("closes modal when Cancel is clicked", async () => {
    const user = makeUser();
    await renderWithTimeout();
    await user.click(screen.getByRole("button", { name: "Manual Override" }));
    expect(screen.getByRole("dialog")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("Gate Command — instrumentation adoption events", () => {
  it("emits CERTIFY event with fabricTxHash on successful certify", async () => {
    vi.spyOn(api, "listGateResults").mockResolvedValue(
      makeListResponse(ALL_PASS_RESULTS),
    );
    vi.spyOn(api, "certifyVehicle").mockResolvedValue({
      certificateId: "cert-123",
      fabricTxHash: "a3f9c2d1e8b047bf2e1d5f8a3c9d6b2e",
      vin: "WBY73CF080NCH14427",
      issuedAt: "2026-10-13T09:31:16.000Z",
      validatorId: "kb-entra-001",
      healthScore: { overall: "pass", perGate: {} },
      gateResultsSnapshot: [],
    });
    const emitSpy = vi.spyOn(adoptionEvents, "emitCertify").mockResolvedValue(undefined);

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
          fabricTxHash: "a3f9c2d1e8b047bf2e1d5f8a3c9d6b2e",
        }),
      ),
    );
  });

  it("emits CERTIFICATE_STUDIO_OPEN event when certificate panel is rendered", async () => {
    vi.spyOn(api, "listGateResults").mockResolvedValue(
      makeListResponse(ALL_PASS_RESULTS),
    );
    vi.spyOn(api, "certifyVehicle").mockResolvedValue({
      certificateId: "cert-123",
      fabricTxHash: "a3f9c2d1e8b047bf2e1d5f8a3c9d6b2e",
      vin: "WBY73CF080NCH14427",
      issuedAt: "2026-10-13T09:31:16.000Z",
      validatorId: "kb-entra-001",
      healthScore: { overall: "pass", perGate: {} },
      gateResultsSnapshot: [],
    });
    const emitSpy = vi.spyOn(adoptionEvents, "emitCertificateStudioOpen").mockResolvedValue(undefined);

    renderPage();

    await waitFor(() =>
      expect(screen.getByTestId("certify-button")).toBeTruthy(),
    );

    fireEvent.click(screen.getByTestId("certify-button"));

    await waitFor(() =>
      expect(emitSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          action: "CERTIFICATE_STUDIO_OPEN",
          role: "QUALITY_GATE_VALIDATOR",
          vin: "WBY73CF080NCH14427",
        }),
      ),
    );
  });
});
