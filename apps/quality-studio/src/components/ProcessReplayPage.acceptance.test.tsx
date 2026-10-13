/**
 * Acceptance tests for US-013 · Process Replay
 *
 * Covers the seam between vin-digital-thread-api (replay/snapshot endpoints)
 * and quality-studio (ProcessReplayPage component).  Each test is named after
 * the criterion it proves.
 *
 * AC1 — all 14 prep stages, booth parameters, and climate data rendered as
 *        aligned timeline tracks when replay loads
 * AC2 — scrubbing to a timestamp shows parameter values, stage name, and
 *        tunnel position in the data panel
 * AC3 — missing stage shown as grey gap with "Data Unavailable" label;
 *        completeness % shown at the top of the replay view
 */

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { I18nextProvider } from "react-i18next";
import i18n from "../i18n";
import { ProcessReplayPage } from "./ProcessReplayPage";
import * as api from "../services/api";
import type { ProcessReplayResponse, ThreadSnapshotResponse } from "../types/processReplay";

// ---------------------------------------------------------------------------
// Precondition guard: verify the API surface the component calls still exists
// ---------------------------------------------------------------------------
it("precondition: getVinProcessReplay is exported from the api module", () => {
  expect(typeof api.getVinProcessReplay).toBe("function");
});

it("precondition: getVinThreadSnapshot is exported from the api module", () => {
  expect(typeof api.getVinThreadSnapshot).toBe("function");
});

// ---------------------------------------------------------------------------
// Shared fixtures
// ---------------------------------------------------------------------------

const TEST_VIN = "WBS4E0108RG271034";

/** Build a full replay response with exactly 14 prep stages. */
function makeFullReplay(overrides: Partial<ProcessReplayResponse> = {}): ProcessReplayResponse {
  return {
    vin: TEST_VIN,
    overallCompletenessPercent: 100,
    replayWindowStart: "2026-10-12T00:00:00Z",
    replayWindowEnd: "2026-10-12T00:10:00Z",
    prepStages: Array.from({ length: 14 }, (_, i) => ({
      stageNumber: i + 1,
      stageName: `Stage ${i + 1}`,
      available: true,
      samples: [
        {
          ts: `2026-10-12T00:0${Math.min(i, 5)}:00Z`,
          parameters: {
            temperature_c: 20 + i,
            ecoat_voltage_v: 300 + i * 5,
            phosphate_conc: 1.2,
            humidity_pct: 55,
          },
          tunnelPositionMm: 5000 + i * 3000,
        },
      ],
    })),
    boothParameters: [
      {
        boothId: "BOOTH_1",
        available: true,
        samples: [{ ts: "2026-10-12T00:05:00Z", parameters: { pressure_bar: 2.1 } }],
      },
    ],
    climateData: {
      available: true,
      samples: [{ ts: "2026-10-12T00:02:00Z", parameters: { ambient_temp_c: 21.5 } }],
    },
    page: { next_cursor: null, limit: 200 },
    ...overrides,
  };
}

/** Build a replay response where stage `missingStageNumber` is absent (OQ-6 scenario). */
function makePartialReplay(missingStageNumber: number): ProcessReplayResponse {
  return makeFullReplay({
    overallCompletenessPercent: Math.round(((14 - 1) / 14) * 100),
    prepStages: Array.from({ length: 14 }, (_, i) => {
      const n = i + 1;
      const missing = n === missingStageNumber;
      return {
        stageNumber: n,
        stageName: `Stage ${n}`,
        available: !missing,
        samples: missing
          ? []
          : [
              {
                ts: `2026-10-12T00:0${Math.min(i, 5)}:00Z`,
                parameters: { temperature_c: 20 + i },
                tunnelPositionMm: 5000 + i * 3000,
              },
            ],
      };
    }),
  });
}

/** A snapshot returned by the snapshot endpoint after scrubbing. */
function makeSnapshot(overrides: Partial<ThreadSnapshotResponse> = {}): ThreadSnapshotResponse {
  return {
    vin: TEST_VIN,
    atTs: "2026-10-12T00:03:00Z",
    stageName: "Stage 4",
    stageNumber: 4,
    tunnelPositionMm: 14000,
    parameters: {
      temperature_c: 23.1,
      ecoat_voltage_v: 315,
      phosphate_conc: 1.25,
      humidity_pct: 58,
    },
    dataAvailable: true,
    ...overrides,
  };
}

function Wrapper({ children }: { children: React.ReactNode }) {
  return <I18nextProvider i18n={i18n}>{children}</I18nextProvider>;
}

// ---------------------------------------------------------------------------
// AC1 · All 14 prep stages + booth + climate rendered as aligned timeline tracks
// ---------------------------------------------------------------------------

describe("AC1 · all 14 prep stage tracks, booth parameters, and climate data are rendered as aligned timeline tracks when the replay loads", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("en-GB");
  });
  afterEach(() => vi.restoreAllMocks());

  it("renders exactly 14 prep-stage timeline rows after the replay loads", async () => {
    vi.spyOn(api, "getVinProcessReplay").mockResolvedValue(makeFullReplay());
    vi.spyOn(api, "getVinThreadSnapshot").mockResolvedValue(makeSnapshot());

    render(
      <Wrapper>
        <ProcessReplayPage vin={TEST_VIN} onBack={() => {}} />
      </Wrapper>,
    );

    await waitFor(() => expect(screen.getByTestId("replay-shell")).toBeTruthy());

    // Each TimelineTrack renders a div with role="row" and aria-label containing the stage name.
    const rows = screen.getAllByRole("row");
    // Collect those whose aria-label starts with "Stage "
    const prepRows = rows.filter((r) =>
      /^Stage \d+/.test(r.getAttribute("aria-label") ?? ""),
    );
    expect(prepRows.length).toBe(14);
  });

  it("renders at least one booth parameter track in the timeline", async () => {
    vi.spyOn(api, "getVinProcessReplay").mockResolvedValue(makeFullReplay());
    vi.spyOn(api, "getVinThreadSnapshot").mockResolvedValue(makeSnapshot());

    render(
      <Wrapper>
        <ProcessReplayPage vin={TEST_VIN} onBack={() => {}} />
      </Wrapper>,
    );

    await waitFor(() => expect(screen.getByTestId("replay-shell")).toBeTruthy());

    // The booth group is rendered as a rowgroup with the matching aria-label.
    const boothGroup = screen.getByRole("rowgroup", { name: /booth/i });
    expect(boothGroup).toBeTruthy();

    // And it contains at least one row for BOOTH_1.
    const boothRow = screen.getByRole("row", { name: /BOOTH_1/i });
    expect(boothRow).toBeTruthy();
  });

  it("renders the climate data track in the timeline", async () => {
    vi.spyOn(api, "getVinProcessReplay").mockResolvedValue(makeFullReplay());
    vi.spyOn(api, "getVinThreadSnapshot").mockResolvedValue(makeSnapshot());

    render(
      <Wrapper>
        <ProcessReplayPage vin={TEST_VIN} onBack={() => {}} />
      </Wrapper>,
    );

    await waitFor(() => expect(screen.getByTestId("replay-shell")).toBeTruthy());

    const climateGroup = screen.getByRole("rowgroup", { name: /climate/i });
    expect(climateGroup).toBeTruthy();
  });

  it("renders all track groups in a single scrollable container (the timeline column)", async () => {
    vi.spyOn(api, "getVinProcessReplay").mockResolvedValue(makeFullReplay());
    vi.spyOn(api, "getVinThreadSnapshot").mockResolvedValue(makeSnapshot());

    render(
      <Wrapper>
        <ProcessReplayPage vin={TEST_VIN} onBack={() => {}} />
      </Wrapper>,
    );

    await waitFor(() => expect(screen.getByTestId("replay-shell")).toBeTruthy());

    // The scrubber slider wraps all track groups — it must exist.
    const slider = screen.getByRole("slider");
    expect(slider).toBeTruthy();

    // All three groups must be descendants of the slider.
    const prepGroup = screen.getByRole("rowgroup", { name: /prep/i });
    const boothGroup = screen.getByRole("rowgroup", { name: /booth/i });
    const climateGroup = screen.getByRole("rowgroup", { name: /climate/i });

    expect(slider.contains(prepGroup)).toBe(true);
    expect(slider.contains(boothGroup)).toBe(true);
    expect(slider.contains(climateGroup)).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// AC2 · Scrubbing shows parameter values, stage name, and tunnel position in data panel
// ---------------------------------------------------------------------------

describe("AC2 · scrubbing the timeline to a timestamp shows parameter values, stage name, and tunnel position in the data panel", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("en-GB");
  });
  afterEach(() => vi.restoreAllMocks());

  it("data panel shows stage name returned by the snapshot endpoint after scrubbing", async () => {
    vi.spyOn(api, "getVinProcessReplay").mockResolvedValue(makeFullReplay());
    vi.spyOn(api, "getVinThreadSnapshot").mockResolvedValue(
      makeSnapshot({ stageName: "Stage 4", stageNumber: 4 }),
    );

    render(
      <Wrapper>
        <ProcessReplayPage vin={TEST_VIN} onBack={() => {}} />
      </Wrapper>,
    );

    await waitFor(() => expect(screen.getByTestId("replay-shell")).toBeTruthy());

    // Scrub to a non-zero position via keyboard.
    const slider = screen.getByRole("slider");
    fireEvent.keyDown(slider, { key: "ArrowRight" });

    await waitFor(
      () => expect(screen.getByTestId("panel-stage-name")).toBeTruthy(),
      { timeout: 2000 },
    );
    expect(screen.getByTestId("panel-stage-name").textContent).toBe("Stage 4");
  });

  it("data panel shows the tunnel position returned by the snapshot endpoint after scrubbing", async () => {
    vi.spyOn(api, "getVinProcessReplay").mockResolvedValue(makeFullReplay());
    vi.spyOn(api, "getVinThreadSnapshot").mockResolvedValue(
      makeSnapshot({ tunnelPositionMm: 14000 }),
    );

    render(
      <Wrapper>
        <ProcessReplayPage vin={TEST_VIN} onBack={() => {}} />
      </Wrapper>,
    );

    await waitFor(() => expect(screen.getByTestId("replay-shell")).toBeTruthy());

    const slider = screen.getByRole("slider");
    fireEvent.keyDown(slider, { key: "ArrowRight" });

    await waitFor(
      () => expect(screen.getByTestId("panel-tunnel-position")).toBeTruthy(),
      { timeout: 2000 },
    );
    const positionEl = screen.getByTestId("panel-tunnel-position");
    // Displayed as "14,000 mm" (toLocaleString) — verify "mm" and the value are present.
    expect(positionEl.textContent).toMatch(/14[,.]?000\s*mm/);
  });

  it("data panel shows ecoat_voltage_v parameter row after scrubbing", async () => {
    vi.spyOn(api, "getVinProcessReplay").mockResolvedValue(makeFullReplay());
    vi.spyOn(api, "getVinThreadSnapshot").mockResolvedValue(
      makeSnapshot({
        parameters: {
          temperature_c: 23.1,
          ecoat_voltage_v: 315,
          phosphate_conc: 1.25,
          humidity_pct: 58,
        },
      }),
    );

    render(
      <Wrapper>
        <ProcessReplayPage vin={TEST_VIN} onBack={() => {}} />
      </Wrapper>,
    );

    await waitFor(() => expect(screen.getByTestId("replay-shell")).toBeTruthy());

    const slider = screen.getByRole("slider");
    fireEvent.keyDown(slider, { key: "ArrowRight" });

    await waitFor(
      () => expect(screen.getByTestId("param-row-ecoat_voltage_v")).toBeTruthy(),
      { timeout: 2000 },
    );
    // Exact value: 315
    const row = screen.getByTestId("param-row-ecoat_voltage_v");
    expect(row.textContent).toContain("315");
  });

  it("data panel shows the VIN alongside stage name and tunnel position", async () => {
    vi.spyOn(api, "getVinProcessReplay").mockResolvedValue(makeFullReplay());
    vi.spyOn(api, "getVinThreadSnapshot").mockResolvedValue(makeSnapshot());

    render(
      <Wrapper>
        <ProcessReplayPage vin={TEST_VIN} onBack={() => {}} />
      </Wrapper>,
    );

    await waitFor(() => expect(screen.getByTestId("replay-shell")).toBeTruthy());

    const slider = screen.getByRole("slider");
    fireEvent.keyDown(slider, { key: "ArrowRight" });

    await waitFor(
      () => expect(screen.getByTestId("panel-stage-name")).toBeTruthy(),
      { timeout: 2000 },
    );

    // The panel renders an element aria-labeled "VIN <vin>".
    const vinEl = screen.getByLabelText(`VIN ${TEST_VIN}`);
    expect(vinEl.textContent).toBe(TEST_VIN);
  });

  it("getVinThreadSnapshot is called with the VIN and an ISO timestamp when scrubbing", async () => {
    vi.spyOn(api, "getVinProcessReplay").mockResolvedValue(makeFullReplay());
    const snapshotSpy = vi
      .spyOn(api, "getVinThreadSnapshot")
      .mockResolvedValue(makeSnapshot());

    render(
      <Wrapper>
        <ProcessReplayPage vin={TEST_VIN} onBack={() => {}} />
      </Wrapper>,
    );

    await waitFor(() => expect(screen.getByTestId("replay-shell")).toBeTruthy());

    const slider = screen.getByRole("slider");
    fireEvent.keyDown(slider, { key: "ArrowRight" });

    // Wait for the debounce (200ms) to fire.
    await waitFor(
      () => expect(snapshotSpy).toHaveBeenCalled(),
      { timeout: 1000 },
    );

    const [calledVin, calledTs] = snapshotSpy.mock.calls[0];
    expect(calledVin).toBe(TEST_VIN);
    // Timestamp must be a valid ISO 8601 string
    expect(new Date(calledTs).toISOString()).toBe(calledTs);
  });
});

// ---------------------------------------------------------------------------
// AC3 · Missing stage shown as grey gap; completeness % at the top
// ---------------------------------------------------------------------------

describe("AC3 · missing prep stage shown as grey gap with 'Data Unavailable' label; completeness % shown at top of replay view", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("en-GB");
  });
  afterEach(() => vi.restoreAllMocks());

  it("renders a grey gap (data-gap) for the missing stage (OQ-6 partial traceability)", async () => {
    // Stage 6 is missing — simulates OQ-6 partial data.
    vi.spyOn(api, "getVinProcessReplay").mockResolvedValue(makePartialReplay(6));
    vi.spyOn(api, "getVinThreadSnapshot").mockResolvedValue(makeSnapshot());

    render(
      <Wrapper>
        <ProcessReplayPage vin={TEST_VIN} onBack={() => {}} />
      </Wrapper>,
    );

    await waitFor(() => expect(screen.getByTestId("replay-shell")).toBeTruthy());

    // At least one gap segment must be rendered.
    const gaps = screen.getAllByTestId("data-gap");
    expect(gaps.length).toBeGreaterThanOrEqual(1);
  });

  it("data-gap is aria-labeled 'Data Unavailable' for the missing stage", async () => {
    vi.spyOn(api, "getVinProcessReplay").mockResolvedValue(makePartialReplay(6));
    vi.spyOn(api, "getVinThreadSnapshot").mockResolvedValue(makeSnapshot());

    render(
      <Wrapper>
        <ProcessReplayPage vin={TEST_VIN} onBack={() => {}} />
      </Wrapper>,
    );

    await waitFor(() => expect(screen.getByTestId("replay-shell")).toBeTruthy());

    const gaps = screen.getAllByTestId("data-gap");
    // Each gap has role="img" with aria-label matching the i18n key "Data Unavailable"
    const unavailableGap = gaps.find(
      (g) =>
        (g.getAttribute("aria-label") ?? "").toLowerCase().includes("unavailable") ||
        (g.getAttribute("aria-label") ?? "").toLowerCase().includes("data"),
    );
    expect(unavailableGap).toBeTruthy();
  });

  it("the missing stage track (available=false) still renders a row so the timeline is contiguous", async () => {
    vi.spyOn(api, "getVinProcessReplay").mockResolvedValue(makePartialReplay(6));
    vi.spyOn(api, "getVinThreadSnapshot").mockResolvedValue(makeSnapshot());

    render(
      <Wrapper>
        <ProcessReplayPage vin={TEST_VIN} onBack={() => {}} />
      </Wrapper>,
    );

    await waitFor(() => expect(screen.getByTestId("replay-shell")).toBeTruthy());

    // All 14 stage rows must still be present even when one has available=false.
    const rows = screen.getAllByRole("row");
    const prepRows = rows.filter((r) =>
      /^Stage \d+/.test(r.getAttribute("aria-label") ?? ""),
    );
    expect(prepRows.length).toBe(14);
  });

  it("completeness-pct element is present and shows the percentage from the replay response", async () => {
    const expected = Math.round(((14 - 1) / 14) * 100); // 92%
    vi.spyOn(api, "getVinProcessReplay").mockResolvedValue(makePartialReplay(6));
    vi.spyOn(api, "getVinThreadSnapshot").mockResolvedValue(makeSnapshot());

    render(
      <Wrapper>
        <ProcessReplayPage vin={TEST_VIN} onBack={() => {}} />
      </Wrapper>,
    );

    await waitFor(() => expect(screen.getByTestId("completeness-pct")).toBeTruthy());
    expect(screen.getByTestId("completeness-pct").textContent).toBe(`${expected}%`);
  });

  it("completeness-pct is rendered at the top of the replay view — before the timeline shell", async () => {
    vi.spyOn(api, "getVinProcessReplay").mockResolvedValue(makePartialReplay(6));
    vi.spyOn(api, "getVinThreadSnapshot").mockResolvedValue(makeSnapshot());

    render(
      <Wrapper>
        <ProcessReplayPage vin={TEST_VIN} onBack={() => {}} />
      </Wrapper>,
    );

    await waitFor(() => expect(screen.getByTestId("replay-shell")).toBeTruthy());

    const pctEl = screen.getByTestId("completeness-pct");
    const shellEl = screen.getByTestId("replay-shell");

    // completeness-pct must precede replay-shell in the DOM.
    const position = pctEl.compareDocumentPosition(shellEl);
    // Node.DOCUMENT_POSITION_FOLLOWING === 4: shellEl comes after pctEl
    expect(position & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("shows a completeness warning note when completeness is below 95%", async () => {
    vi.spyOn(api, "getVinProcessReplay").mockResolvedValue(makePartialReplay(6));
    vi.spyOn(api, "getVinThreadSnapshot").mockResolvedValue(makeSnapshot());

    render(
      <Wrapper>
        <ProcessReplayPage vin={TEST_VIN} onBack={() => {}} />
      </Wrapper>,
    );

    await waitFor(() => expect(screen.getByTestId("completeness-pct")).toBeTruthy());

    // A role="note" warning is rendered when pct < 95 (see CompletenessBar).
    const warning = screen.getByRole("note");
    expect(warning).toBeTruthy();
  });

  it("does NOT show the completeness warning when all 14 stages have data (100%)", async () => {
    vi.spyOn(api, "getVinProcessReplay").mockResolvedValue(
      makeFullReplay({ overallCompletenessPercent: 100 }),
    );
    vi.spyOn(api, "getVinThreadSnapshot").mockResolvedValue(makeSnapshot());

    render(
      <Wrapper>
        <ProcessReplayPage vin={TEST_VIN} onBack={() => {}} />
      </Wrapper>,
    );

    await waitFor(() => expect(screen.getByTestId("completeness-pct")).toBeTruthy());

    expect(screen.queryByRole("note")).toBeNull();
  });
});
