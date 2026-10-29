/**
 * Acceptance tests for US-016
 * Gate Command: auto-populate a unified vehicle health score from all 5 gate
 * results (Dim/Leak/Elec/NVH/ADAS) received via OpCenter.
 *
 * AC-1: All 5 gate results received → unified health score displayed within
 *       10 s, showing pass/conditional/fail per gate with raw measurement values.
 * AC-2: Any gate shows conditional_pass → anomalous measurement highlighted
 *       with tolerance band, actual value, and historical distribution.
 * AC-3: Validator clicks Certify → Digital Birth Certificate generated, event
 *       logged with validatorId, VIN, gate IDs, health score snapshot, and
 *       timestamp.
 *
 * Tests the seam between:
 *   - opcenter-sync-service REST query API   (mocked via api.listGateResults)
 *   - computeHealthScore / buildGateResultSnapshot in GateCommandPage.tsx
 *   - GateCommandPage UI (health banner, gate cards, anomaly highlights)
 *   - issueCertificate / CertificateDetailCard (certify flow)
 *   - emitCertify adoption event (instrumentation)
 */

import "./i18n";
import {
  render,
  screen,
  waitFor,
  fireEvent,
  within,
  cleanup,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { I18nextProvider } from "react-i18next";
import i18n from "./i18n";
import {
  GateCommandPage,
  computeHealthScore,
} from "./components/GateCommandPage";
import * as api from "./services/api";
import * as adoptionEvents from "./instrumentation/adoptionEvents";
import type { GateResultRecord } from "./services/api";

// ---------------------------------------------------------------------------
// Preconditions — fail loudly if production code entry points are absent
// ---------------------------------------------------------------------------

describe("US-016 preconditions — production entry points must exist", () => {
  it("GateCommandPage is a renderable function", () => {
    expect(typeof GateCommandPage).toBe("function");
  });

  it("computeHealthScore is an exported function", () => {
    expect(typeof computeHealthScore).toBe("function");
  });

  it("api.listGateResults is a function", () => {
    expect(typeof api.listGateResults).toBe("function");
  });

  it("api.issueCertificate is a function", () => {
    expect(typeof api.issueCertificate).toBe("function");
  });

  it("emitCertify adoption event function is exported", () => {
    expect(typeof adoptionEvents.emitCertify).toBe("function");
  });

  it("gate_result migration SQL file exists and defines gate_result table", async () => {
    // Read the actual migration file — never assert against a hand-written copy.
    const { default: fs } = await import("fs");
    const { default: path } = await import("path");

    const candidates = [
      // opcenter-sync-service migration (required by AC-1 data pipeline)
      path.resolve(
        __dirname,
        "../../../../services/opcenter-sync-service/migrations/20261013_001_gate_result.sql",
      ),
      // postgresql-citus platform migration
      path.resolve(
        __dirname,
        "../../../../platform/postgresql-citus/migrations/20261013000000_create_gate_result.sql",
      ),
    ];

    let found = false;
    for (const p of candidates) {
      if (fs.existsSync(p)) {
        const sql = fs.readFileSync(p, "utf-8");
        expect(sql).toContain("gate_result");
        expect(sql).toContain("gate_type");
        expect(sql).toContain("result");
        expect(sql).toContain("measurements");
        found = true;
        break;
      }
    }
    expect(found, "gate_result migration file not found in known locations").toBe(true);
  });

  it("gate_result migration enforces all 5 gate_type enum values", async () => {
    const { default: fs } = await import("fs");
    const { default: path } = await import("path");

    const candidates = [
      path.resolve(
        __dirname,
        "../../../../services/opcenter-sync-service/migrations/20261013_001_gate_result.sql",
      ),
      path.resolve(
        __dirname,
        "../../../../platform/postgresql-citus/migrations/20261013000000_create_gate_result.sql",
      ),
    ];

    let sql = "";
    for (const p of candidates) {
      if (fs.existsSync(p)) {
        sql = fs.readFileSync(p, "utf-8");
        break;
      }
    }

    expect(sql.length, "migration file is empty — data layer not present").toBeGreaterThan(0);

    for (const gt of ["dimensional", "leak", "electrical", "nvh", "adas"]) {
      expect(sql, `gate_type '${gt}' not in migration CHECK constraint`).toContain(gt);
    }
    for (const r of ["pass", "conditional_pass", "fail"]) {
      expect(sql, `result '${r}' not in migration CHECK constraint`).toContain(r);
    }
  });
});

// ---------------------------------------------------------------------------
// Shared fixtures
// ---------------------------------------------------------------------------

const TEST_VIN = "WBY73CF080NCH14427";

function makeGateResult(
  overrides: Partial<GateResultRecord> & {
    gateType: api.GateType;
    result: api.GateResult;
  },
): GateResultRecord {
  return {
    gateResultId: `gr-${overrides.gateType}`,
    vin: TEST_VIN,
    gateId: `GATE-${overrides.gateType.toUpperCase()}`,
    measurements: [
      {
        measurementId: `m-${overrides.gateType}-1`,
        name: "Gap width",
        value: 2.5,
        unit: "mm",
        toleranceLow: 2.0,
        toleranceHigh: 3.0,
        withinTolerance: true,
        reworkThresholdLow: null,
        reworkThresholdHigh: null,
      },
    ],
    measuredAt: "2099-01-01T06:00:00Z",
    receivedFromOpcenterAt: "2099-01-01T06:00:05Z",
    opcenterEventId: null,
    ...overrides,
  };
}

/** All 5 gate results with pass */
const ALL_PASS_RESULTS: GateResultRecord[] = [
  makeGateResult({ gateType: "dimensional", result: "pass" }),
  makeGateResult({ gateType: "leak", result: "pass" }),
  makeGateResult({ gateType: "electrical", result: "pass" }),
  makeGateResult({ gateType: "nvh", result: "pass" }),
  makeGateResult({ gateType: "adas", result: "pass" }),
];

/** NVH is conditional_pass with an out-of-tolerance road-noise measurement */
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
): api.GateResultListResponse {
  return { data, page: { next_cursor: null, limit: 200 } };
}

function renderPage(vin = TEST_VIN) {
  return render(
    <I18nextProvider i18n={i18n}>
      <GateCommandPage vin={vin} />
    </I18nextProvider>,
  );
}

// ---------------------------------------------------------------------------
// Setup / teardown
// ---------------------------------------------------------------------------

beforeEach(async () => {
  await i18n.changeLanguage("en-GB");
  localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
  cleanup();
});

// ---------------------------------------------------------------------------
// AC-1: All 5 gate results → unified health score displayed with per-gate
//       result and raw measurement values
// ---------------------------------------------------------------------------

describe(
  "AC-1 — health score displayed with all 5 gate results and raw measurements",
  () => {
    it("shows health score banner once all 5 gate results are available", async () => {
      // AC-1: unified health score displayed
      vi.spyOn(api, "listGateResults").mockResolvedValue(
        makeListResponse(ALL_PASS_RESULTS),
      );

      renderPage();

      await waitFor(() => {
        expect(screen.getByTestId("health-banner")).toBeTruthy();
      });
    });

    it("health banner shows 'Pass' overall when all 5 results are pass", async () => {
      // AC-1: pass/conditional/fail per gate
      vi.spyOn(api, "listGateResults").mockResolvedValue(
        makeListResponse(ALL_PASS_RESULTS),
      );

      renderPage();

      await waitFor(() =>
        expect(screen.getByTestId("health-banner")).toBeTruthy(),
      );

      const banner = screen.getByTestId("health-banner");
      expect(banner.textContent).toContain("Pass");
    });

    it("all 5 per-gate badges are present with Pass status when all 5 pass", async () => {
      // AC-1: showing pass per gate
      vi.spyOn(api, "listGateResults").mockResolvedValue(
        makeListResponse(ALL_PASS_RESULTS),
      );

      renderPage();

      const gateTypes: api.GateType[] = [
        "dimensional",
        "leak",
        "electrical",
        "nvh",
        "adas",
      ];

      for (const gt of gateTypes) {
        await waitFor(() =>
          expect(screen.getByTestId(`per-gate-badge-${gt}`)).toBeTruthy(),
        );
        const badge = screen.getByTestId(`per-gate-badge-${gt}`);
        expect(badge.textContent).toContain("Pass");
      }
    });

    it("overall health score is 'Conditional Pass' when at least one gate is conditional_pass", async () => {
      // AC-1: conditional_pass shown correctly
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

    it("NVH per-gate badge shows 'Conditional Pass' when NVH result is conditional_pass", async () => {
      // AC-1: per-gate result shown for each gate
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

    it("each of the 5 gate cards is rendered showing raw measurement values", async () => {
      // AC-1: raw measurement values shown per gate card
      vi.spyOn(api, "listGateResults").mockResolvedValue(
        makeListResponse(ALL_PASS_RESULTS),
      );

      renderPage();

      await waitFor(() =>
        expect(screen.getByTestId("gate-card-dimensional")).toBeTruthy(),
      );

      for (const gt of ["dimensional", "leak", "electrical", "nvh", "adas"] as api.GateType[]) {
        const card = screen.getByTestId(`gate-card-${gt}`);
        // Each card has a measurement value from the fixture (2.5 mm)
        expect(card.textContent).toContain("2.5");
        expect(card.textContent).toContain("mm");
      }
    });

    it("computeHealthScore produces 'pass' overall when all 5 results are pass", () => {
      // AC-1: unit-level seam — computeHealthScore is the aggregation logic bridging
      // the ingestion pipeline output and the UI's health score display
      const score = computeHealthScore(ALL_PASS_RESULTS);
      expect(score.overall).toBe("pass");
      expect(score.perGate.dimensional).toBe("pass");
      expect(score.perGate.leak).toBe("pass");
      expect(score.perGate.electrical).toBe("pass");
      expect(score.perGate.nvh).toBe("pass");
      expect(score.perGate.adas).toBe("pass");
    });

    it("computeHealthScore produces 'conditional_pass' overall when one gate is conditional", () => {
      // AC-1: seam — computeHealthScore correctly classifies mixed results
      const score = computeHealthScore(COND_PASS_NVH_RESULTS);
      expect(score.overall).toBe("conditional_pass");
      expect(score.perGate.nvh).toBe("conditional_pass");
      expect(score.perGate.dimensional).toBe("pass");
    });

    it("computeHealthScore produces 'fail' overall when any gate fails", () => {
      // AC-1: seam — fail takes priority over conditional_pass
      const results = [
        ...ALL_PASS_RESULTS.slice(0, 4),
        makeGateResult({ gateType: "adas", result: "fail" }),
      ];
      const score = computeHealthScore(results);
      expect(score.overall).toBe("fail");
    });

    it("computeHealthScore deduplicates: only first occurrence per gateType is used", () => {
      // AC-1: seam — duplicate gate results (from polling) must not corrupt score
      const results = [
        makeGateResult({ gateType: "nvh", result: "conditional_pass" }),
        makeGateResult({
          gateResultId: "gr-nvh-2",
          gateType: "nvh",
          result: "fail",
        }),
        makeGateResult({ gateType: "dimensional", result: "pass" }),
        makeGateResult({ gateType: "leak", result: "pass" }),
        makeGateResult({ gateType: "electrical", result: "pass" }),
        makeGateResult({ gateType: "adas", result: "pass" }),
      ];
      const score = computeHealthScore(results);
      // First NVH result is conditional_pass — fail duplicate must be ignored
      expect(score.perGate.nvh).toBe("conditional_pass");
      expect(score.overall).toBe("conditional_pass");
    });
  },
);

// ---------------------------------------------------------------------------
// AC-2: conditional_pass gate → anomaly highlight with tolerance band, actual
//       value, and historical distribution
// ---------------------------------------------------------------------------

describe(
  "AC-2 — conditional_pass anomaly highlighted with tolerance band, value, and distribution",
  () => {
    it("anomaly highlight component is rendered for the out-of-tolerance NVH measurement", async () => {
      // AC-2: anomalous measurement is highlighted
      vi.spyOn(api, "listGateResults").mockResolvedValue(
        makeListResponse(COND_PASS_NVH_RESULTS),
      );

      renderPage();

      await waitFor(() =>
        expect(
          screen.getByTestId("anomaly-highlight-m-nvh-road-noise"),
        ).toBeTruthy(),
      );
    });

    it("anomaly highlight shows the actual measurement value (68.4 dB)", async () => {
      // AC-2: actual value present in highlight
      vi.spyOn(api, "listGateResults").mockResolvedValue(
        makeListResponse(COND_PASS_NVH_RESULTS),
      );

      renderPage();

      await waitFor(() =>
        expect(
          screen.getByTestId("anomaly-highlight-m-nvh-road-noise"),
        ).toBeTruthy(),
      );

      const highlight = screen.getByTestId("anomaly-highlight-m-nvh-road-noise");
      expect(highlight.textContent).toContain("68.4");
    });

    it("anomaly highlight shows tolerance low bound (58 dB)", async () => {
      // AC-2: tolerance band — lower bound
      vi.spyOn(api, "listGateResults").mockResolvedValue(
        makeListResponse(COND_PASS_NVH_RESULTS),
      );

      renderPage();

      await waitFor(() =>
        expect(
          screen.getByTestId("anomaly-highlight-m-nvh-road-noise"),
        ).toBeTruthy(),
      );

      const highlight = screen.getByTestId("anomaly-highlight-m-nvh-road-noise");
      expect(highlight.textContent).toContain("58");
    });

    it("anomaly highlight shows tolerance high bound (66 dB)", async () => {
      // AC-2: tolerance band — upper bound
      vi.spyOn(api, "listGateResults").mockResolvedValue(
        makeListResponse(COND_PASS_NVH_RESULTS),
      );

      renderPage();

      await waitFor(() =>
        expect(
          screen.getByTestId("anomaly-highlight-m-nvh-road-noise"),
        ).toBeTruthy(),
      );

      const highlight = screen.getByTestId("anomaly-highlight-m-nvh-road-noise");
      expect(highlight.textContent).toContain("66");
    });

    it("anomaly highlight renders a tolerance range bar (qs-range-bar-track)", async () => {
      // AC-2: tolerance band visualised — the bar track is the structural element
      vi.spyOn(api, "listGateResults").mockResolvedValue(
        makeListResponse(COND_PASS_NVH_RESULTS),
      );

      renderPage();

      await waitFor(() =>
        expect(
          screen.getByTestId("anomaly-highlight-m-nvh-road-noise"),
        ).toBeTruthy(),
      );

      const highlight = screen.getByTestId("anomaly-highlight-m-nvh-road-noise");
      // The tolerance band element is rendered inside the highlight section
      const rangeTrack = highlight.querySelector(".qs-range-bar-track");
      expect(rangeTrack).not.toBeNull();
      // The tolerance band fill shows where the tolerance window is
      const tolBand = highlight.querySelector(".qs-range-tol-band");
      expect(tolBand).not.toBeNull();
    });

    it("anomaly highlight shows historical distribution chart once data loads", async () => {
      // AC-2: historical distribution for that measurement at that gate
      vi.spyOn(api, "listGateResults").mockResolvedValue(
        makeListResponse(COND_PASS_NVH_RESULTS),
      );

      renderPage();

      await waitFor(() =>
        expect(
          screen.getByTestId("anomaly-highlight-m-nvh-road-noise"),
        ).toBeTruthy(),
      );

      const highlight = screen.getByTestId("anomaly-highlight-m-nvh-road-noise");

      // Distribution chart is shown once the stub data loads (200 ms in stub)
      await waitFor(
        () => {
          const distChart = highlight.querySelector(".qs-dist-chart");
          expect(distChart).not.toBeNull();
          // After loading, the bars container should appear
          const bars = highlight.querySelector(".qs-dist-bars");
          expect(bars).not.toBeNull();
        },
        { timeout: 1000 },
      );
    });

    it("within-tolerance wind-noise measurement is NOT highlighted as anomaly", async () => {
      // AC-2: only out-of-tolerance measurements are highlighted
      vi.spyOn(api, "listGateResults").mockResolvedValue(
        makeListResponse(COND_PASS_NVH_RESULTS),
      );

      renderPage();

      await waitFor(() =>
        expect(screen.getByTestId("gate-card-nvh")).toBeTruthy(),
      );

      // Wind noise is within tolerance → no anomaly highlight
      expect(
        screen.queryByTestId("anomaly-highlight-m-nvh-wind"),
      ).toBeNull();
    });

    it("no anomaly highlights are rendered when all results are pass", async () => {
      // AC-2: highlight only shown for conditional_pass
      vi.spyOn(api, "listGateResults").mockResolvedValue(
        makeListResponse(ALL_PASS_RESULTS),
      );

      renderPage();

      await waitFor(() =>
        expect(screen.getByTestId("health-banner")).toBeTruthy(),
      );

      // No anomaly highlights should be present
      expect(
        document.querySelectorAll("[data-testid^='anomaly-highlight-']").length,
      ).toBe(0);
    });

    it("anomaly highlight section is aria-labelled with the measurement name", async () => {
      // AC-2: accessibility requirement — highlight is a labelled region
      vi.spyOn(api, "listGateResults").mockResolvedValue(
        makeListResponse(COND_PASS_NVH_RESULTS),
      );

      renderPage();

      await waitFor(() =>
        expect(
          screen.getByTestId("anomaly-highlight-m-nvh-road-noise"),
        ).toBeTruthy(),
      );

      const highlight = screen.getByTestId("anomaly-highlight-m-nvh-road-noise");
      // The section must have an aria-labelledby pointing to the heading
      const labelledBy = highlight.getAttribute("aria-labelledby");
      expect(labelledBy).toBeTruthy();
      // The heading with that id must exist and contain the measurement name
      const heading = document.getElementById(labelledBy!);
      expect(heading).not.toBeNull();
      expect(heading!.textContent).toContain("Road noise");
    });
  },
);

// ---------------------------------------------------------------------------
// AC-3: Certify → Digital Birth Certificate generated, event logged with
//       validatorId, VIN, gate IDs, health score snapshot, and timestamp
// ---------------------------------------------------------------------------

describe(
  "AC-3 — Certify generates Digital Birth Certificate and logs event",
  () => {
    it("Certify button is enabled when all 5 gate results are present", async () => {
      // AC-3: precondition — certify is accessible once all gates received
      vi.spyOn(api, "listGateResults").mockResolvedValue(
        makeListResponse(ALL_PASS_RESULTS),
      );

      renderPage();

      await waitFor(() =>
        expect(screen.getByTestId("certify-button")).toBeTruthy(),
      );

      const btn = screen.getByTestId("certify-button");
      expect(btn.getAttribute("aria-disabled")).toBe("false");
    });

    it("clicking Certify renders the CertificateDetailCard (Digital Birth Certificate)", async () => {
      // AC-3: Digital Birth Certificate generated
      vi.spyOn(api, "listGateResults").mockResolvedValue(
        makeListResponse(ALL_PASS_RESULTS),
      );
      vi.spyOn(api, "issueCertificate").mockResolvedValue({
        response: {
          certificateId: "cert-us016-001",
          vin: TEST_VIN,
          fabricTransactionId: "0xabc123fabric",
          fabricBlockNumber: 42,
          anchoredAt: "2099-01-01T06:10:00Z",
          certificateHash: "deadbeefhash001",
        },
        status: 201,
      });

      renderPage();

      await waitFor(() =>
        expect(screen.getByTestId("certify-button")).toBeTruthy(),
      );

      fireEvent.click(screen.getByTestId("certify-button"));

      await waitFor(() =>
        expect(screen.getByTestId("certificate-detail-card")).toBeTruthy(),
      );
    });

    it("Certificate shows the correct VIN", async () => {
      // AC-3: VIN is in the logged event / certificate
      vi.spyOn(api, "listGateResults").mockResolvedValue(
        makeListResponse(ALL_PASS_RESULTS),
      );
      vi.spyOn(api, "issueCertificate").mockResolvedValue({
        response: {
          certificateId: "cert-us016-002",
          vin: TEST_VIN,
          fabricTransactionId: "0xabc124fabric",
          fabricBlockNumber: 43,
          anchoredAt: "2099-01-01T06:10:01Z",
          certificateHash: "deadbeefhash002",
        },
        status: 201,
      });

      renderPage();

      await waitFor(() =>
        expect(screen.getByTestId("certify-button")).toBeTruthy(),
      );

      fireEvent.click(screen.getByTestId("certify-button"));

      await waitFor(() =>
        expect(screen.getByTestId("certificate-detail-card")).toBeTruthy(),
      );

      const card = screen.getByTestId("certificate-detail-card");
      const vinEl = within(card).getByTestId("cert-detail-vin");
      expect(vinEl.textContent).toBe(TEST_VIN);
    });

    it("Certificate shows the Fabric transaction ID (anchoring proof)", async () => {
      // AC-3: gate IDs / fabric anchor is part of the logged event
      vi.spyOn(api, "listGateResults").mockResolvedValue(
        makeListResponse(ALL_PASS_RESULTS),
      );
      vi.spyOn(api, "issueCertificate").mockResolvedValue({
        response: {
          certificateId: "cert-us016-003",
          vin: TEST_VIN,
          fabricTransactionId: "0xfabrictx999",
          fabricBlockNumber: 99,
          anchoredAt: "2099-01-01T06:11:00Z",
          certificateHash: "deadbeefhash003",
        },
        status: 201,
      });

      renderPage();

      await waitFor(() =>
        expect(screen.getByTestId("certify-button")).toBeTruthy(),
      );

      fireEvent.click(screen.getByTestId("certify-button"));

      await waitFor(() =>
        expect(screen.getByTestId("certificate-detail-card")).toBeTruthy(),
      );

      const card = screen.getByTestId("certificate-detail-card");
      const txEl = within(card).getByTestId("cert-detail-fabric-tx-id");
      expect(txEl.textContent).toContain("0xfabrictx999");
    });

    it("Certificate shows the health score", async () => {
      // AC-3: health score snapshot is in the certificate
      vi.spyOn(api, "listGateResults").mockResolvedValue(
        makeListResponse(ALL_PASS_RESULTS),
      );
      vi.spyOn(api, "issueCertificate").mockResolvedValue({
        response: {
          certificateId: "cert-us016-004",
          vin: TEST_VIN,
          fabricTransactionId: "0xfabrictx004",
          fabricBlockNumber: 4,
          anchoredAt: "2099-01-01T06:12:00Z",
          certificateHash: "deadbeefhash004",
        },
        status: 201,
      });

      renderPage();

      await waitFor(() =>
        expect(screen.getByTestId("certify-button")).toBeTruthy(),
      );

      fireEvent.click(screen.getByTestId("certify-button"));

      await waitFor(() =>
        expect(screen.getByTestId("certificate-detail-card")).toBeTruthy(),
      );

      const card = screen.getByTestId("certificate-detail-card");
      const healthEl = within(card).getByTestId("cert-detail-health-score");
      // All pass → 95.0
      expect(healthEl.textContent).toContain("95");
    });

    it("issueCertificate is called with VIN, gateResultSnapshot, healthScore, validatorId, and issuedAt", async () => {
      // AC-3: event logged with VIN, gate IDs, health score snapshot, validatorId, timestamp
      vi.spyOn(api, "listGateResults").mockResolvedValue(
        makeListResponse(ALL_PASS_RESULTS),
      );

      const issueSpy = vi
        .spyOn(api, "issueCertificate")
        .mockResolvedValue({
          response: {
            certificateId: "cert-us016-005",
            vin: TEST_VIN,
            fabricTransactionId: "0xfabrictx005",
            fabricBlockNumber: 5,
            anchoredAt: "2099-01-01T06:13:00Z",
            certificateHash: "deadbeefhash005",
          },
          status: 201,
        });

      renderPage();

      await waitFor(() =>
        expect(screen.getByTestId("certify-button")).toBeTruthy(),
      );

      fireEvent.click(screen.getByTestId("certify-button"));

      await waitFor(() => expect(issueSpy).toHaveBeenCalledOnce());

      const callArg = issueSpy.mock.calls[0][0];

      // VIN
      expect(callArg.vin).toBe(TEST_VIN);

      // gate IDs — snapshot contains station results derived from gate records
      expect(callArg.gateResultSnapshot).toBeDefined();
      expect(callArg.gateResultSnapshot.stationResults).toHaveLength(5);
      const codes = callArg.gateResultSnapshot.stationResults.map(
        (sr) => sr.stationCode,
      );
      expect(codes).toContain("DIM");
      expect(codes).toContain("LEAK");
      expect(codes).toContain("ELEC");
      expect(codes).toContain("NVH");
      expect(codes).toContain("ADAS");

      // health score snapshot
      expect(typeof callArg.healthScore).toBe("number");
      expect(callArg.healthScore).toBeGreaterThan(0);

      // validatorId
      expect(typeof callArg.validatorId).toBe("string");

      // issuedAt timestamp
      expect(typeof callArg.issuedAt).toBe("string");
      expect(callArg.issuedAt.length).toBeGreaterThan(0);
      // Must be a parseable ISO date
      expect(() => new Date(callArg.issuedAt)).not.toThrow();
    });

    it("emitCertify adoption event is fired with userId, role, VIN, and fabricTxHash", async () => {
      // AC-3: event logged with validatorId (userId), VIN, and fabric anchor
      vi.spyOn(api, "listGateResults").mockResolvedValue(
        makeListResponse(ALL_PASS_RESULTS),
      );
      vi.spyOn(api, "issueCertificate").mockResolvedValue({
        response: {
          certificateId: "cert-us016-006",
          vin: TEST_VIN,
          fabricTransactionId: "0xdeadbeef_cert",
          fabricBlockNumber: 6,
          anchoredAt: "2099-01-01T06:14:00Z",
          certificateHash: "deadbeefhash006",
        },
        status: 201,
      });

      const emitSpy = vi.spyOn(adoptionEvents, "emitCertify");

      renderPage();

      await waitFor(() =>
        expect(screen.getByTestId("certify-button")).toBeTruthy(),
      );

      fireEvent.click(screen.getByTestId("certify-button"));

      await waitFor(() =>
        expect(emitSpy).toHaveBeenCalledWith(
          expect.objectContaining({
            role: "QUALITY_GATE_VALIDATOR",
            vin: TEST_VIN,
            fabricTxHash: "0xdeadbeef_cert",
          }),
        ),
      );
    });

    it("emitCertify event includes a userId field", async () => {
      // AC-3: event must carry the validator's user ID
      vi.spyOn(api, "listGateResults").mockResolvedValue(
        makeListResponse(ALL_PASS_RESULTS),
      );
      vi.spyOn(api, "issueCertificate").mockResolvedValue({
        response: {
          certificateId: "cert-us016-007",
          vin: TEST_VIN,
          fabricTransactionId: "0xfabrictx007",
          fabricBlockNumber: 7,
          anchoredAt: "2099-01-01T06:15:00Z",
          certificateHash: "deadbeefhash007",
        },
        status: 201,
      });

      const emitSpy = vi.spyOn(adoptionEvents, "emitCertify");

      renderPage();

      await waitFor(() =>
        expect(screen.getByTestId("certify-button")).toBeTruthy(),
      );

      fireEvent.click(screen.getByTestId("certify-button"));

      await waitFor(() =>
        expect(emitSpy).toHaveBeenCalledWith(
          expect.objectContaining({
            userId: expect.any(String),
          }),
        ),
      );
    });

    it("the CERTIFY window CustomEvent is dispatched with action=CERTIFY", async () => {
      // AC-3: ifos:adoption event on the window bus includes all required fields
      vi.spyOn(api, "listGateResults").mockResolvedValue(
        makeListResponse(ALL_PASS_RESULTS),
      );
      vi.spyOn(api, "issueCertificate").mockResolvedValue({
        response: {
          certificateId: "cert-us016-008",
          vin: TEST_VIN,
          fabricTransactionId: "0xfabrictx008",
          fabricBlockNumber: 8,
          anchoredAt: "2099-01-01T06:16:00Z",
          certificateHash: "deadbeefhash008",
        },
        status: 201,
      });

      const dispatchSpy = vi.spyOn(window, "dispatchEvent");

      renderPage();

      await waitFor(() =>
        expect(screen.getByTestId("certify-button")).toBeTruthy(),
      );

      fireEvent.click(screen.getByTestId("certify-button"));

      await waitFor(() =>
        expect(
          dispatchSpy.mock.calls.some(([evt]) => {
            if (!(evt instanceof CustomEvent)) return false;
            const d = (evt as CustomEvent<{ action: string; vin?: string }>)
              .detail;
            return d.action === "CERTIFY" && d.vin === TEST_VIN;
          }),
        ).toBe(true),
      );
    });

    it("issueCertificate called with gateResultSnapshot whose gateId is non-empty", async () => {
      // AC-3: gate IDs must be present in the snapshot sent to Fabric
      vi.spyOn(api, "listGateResults").mockResolvedValue(
        makeListResponse(ALL_PASS_RESULTS),
      );
      const issueSpy = vi.spyOn(api, "issueCertificate").mockResolvedValue({
        response: {
          certificateId: "cert-us016-009",
          vin: TEST_VIN,
          fabricTransactionId: "0xfabrictx009",
          fabricBlockNumber: 9,
          anchoredAt: "2099-01-01T06:17:00Z",
          certificateHash: "deadbeefhash009",
        },
        status: 201,
      });

      renderPage();

      await waitFor(() =>
        expect(screen.getByTestId("certify-button")).toBeTruthy(),
      );

      fireEvent.click(screen.getByTestId("certify-button"));

      await waitFor(() => expect(issueSpy).toHaveBeenCalledOnce());

      const { gateResultSnapshot } = issueSpy.mock.calls[0][0];
      expect(typeof gateResultSnapshot.gateId).toBe("string");
      expect(gateResultSnapshot.gateId.length).toBeGreaterThan(0);
    });

    it("Certify button is absent/disabled before all 5 gate results arrive (partial state)", async () => {
      // AC-3: certify is only available after all 5 gate results are received
      const partialResults = ALL_PASS_RESULTS.slice(0, 3);
      vi.spyOn(api, "listGateResults").mockResolvedValue(
        makeListResponse(partialResults),
      );

      renderPage();

      await waitFor(() =>
        expect(screen.getByTestId("partial-state")).toBeTruthy(),
      );

      expect(screen.queryByTestId("certify-button")).toBeNull();
    });

    it("Certificate shows an issuedAt timestamp that is a valid ISO string", async () => {
      // AC-3: timestamp must be present in the logged event
      vi.spyOn(api, "listGateResults").mockResolvedValue(
        makeListResponse(ALL_PASS_RESULTS),
      );
      vi.spyOn(api, "issueCertificate").mockResolvedValue({
        response: {
          certificateId: "cert-us016-010",
          vin: TEST_VIN,
          fabricTransactionId: "0xfabrictx010",
          fabricBlockNumber: 10,
          anchoredAt: "2099-01-01T06:18:00Z",
          certificateHash: "deadbeefhash010",
        },
        status: 201,
      });

      renderPage();

      await waitFor(() =>
        expect(screen.getByTestId("certify-button")).toBeTruthy(),
      );

      fireEvent.click(screen.getByTestId("certify-button"));

      await waitFor(() =>
        expect(screen.getByTestId("certificate-detail-card")).toBeTruthy(),
      );

      const card = screen.getByTestId("certificate-detail-card");
      const issuedAtEl = within(card).getByTestId("cert-detail-issued-at");
      const ts = issuedAtEl.textContent ?? "";
      expect(ts.length).toBeGreaterThan(0);
      expect(Number.isNaN(Date.parse(ts))).toBe(false);
    });
  },
);
