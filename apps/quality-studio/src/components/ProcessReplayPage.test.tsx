import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { I18nextProvider } from "react-i18next";
import i18n from "../i18n";
import { ProcessReplayPage } from "./ProcessReplayPage";
import { DefectDashboardPage } from "./DefectDashboardPage";
import * as api from "../services/api";
import * as adoptionEvents from "../instrumentation/adoptionEvents";
import type { ProcessReplayResponse, ThreadSnapshotResponse } from "../types/processReplay";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeReplayResponse(overrides: Partial<ProcessReplayResponse> = {}): ProcessReplayResponse {
  const base: ProcessReplayResponse = {
    vin: "WBS4E0108RG271034",
    overallCompletenessPercent: 100,
    replayWindowStart: "2026-10-12T04:11:00Z",
    replayWindowEnd: "2026-10-12T09:23:00Z",
    prepStages: Array.from({ length: 14 }, (_, i) => ({
      stageNumber: i + 1,
      stageName: `Stage ${i + 1}`,
      available: true,
      samples: [
        {
          ts: "2026-10-12T04:30:00Z",
          parameters: { temperature_c: 23.5, humidity_pct: 60 },
          tunnelPositionMm: 10000 + i * 3000,
        },
      ],
    })),
    boothParameters: [
      {
        boothId: "BOOTH_1",
        available: true,
        samples: [{ ts: "2026-10-12T05:00:00Z", parameters: { pressure_bar: 2.1 } }],
      },
    ],
    climateData: {
      available: true,
      samples: [{ ts: "2026-10-12T04:15:00Z", parameters: { ambient_temp_c: 21 } }],
    },
    page: { next_cursor: null, limit: 200 },
  };
  return { ...base, ...overrides };
}

function makeSnapshotResponse(overrides: Partial<ThreadSnapshotResponse> = {}): ThreadSnapshotResponse {
  const base: ThreadSnapshotResponse = {
    vin: "WBS4E0108RG271034",
    atTs: "2026-10-12T06:00:00Z",
    stageName: "Stage 6",
    stageNumber: 6,
    tunnelPositionMm: 47820,
    parameters: { temperature_c: 24.1, humidity_pct: 62, ecoat_voltage_v: 320 },
    dataAvailable: true,
  };
  return { ...base, ...overrides };
}

// ---------------------------------------------------------------------------
// Wrapper
// ---------------------------------------------------------------------------

function Wrapper({ children }: { children: React.ReactNode }) {
  return <I18nextProvider i18n={i18n}>{children}</I18nextProvider>;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ProcessReplayPage — loading state", () => {
  beforeEach(() => {
    vi.spyOn(api, "getVinProcessReplay").mockReturnValue(new Promise(() => {}));
  });
  afterEach(() => vi.restoreAllMocks());

  it("shows a loading indicator while the replay API is pending", async () => {
    await i18n.changeLanguage("en-GB");
    render(
      <Wrapper>
        <ProcessReplayPage vin="WBS4E0108RG271034" onBack={() => {}} />
      </Wrapper>,
    );
    expect(screen.getByTestId("replay-loading")).toBeTruthy();
  });
});

describe("ProcessReplayPage — error states", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows an error message on 500 and offers retry", async () => {
    await i18n.changeLanguage("en-GB");
    vi.spyOn(api, "getVinProcessReplay").mockRejectedValue(
      new api.ApiError(500, "Internal server error"),
    );
    render(
      <Wrapper>
        <ProcessReplayPage vin="WBS4E0108RG271034" onBack={() => {}} />
      </Wrapper>,
    );
    await waitFor(() => expect(screen.getByTestId("replay-error")).toBeTruthy());
    expect(screen.getByTestId("replay-retry")).toBeTruthy();
  });

  it("shows not-found state on 404 without retry button", async () => {
    await i18n.changeLanguage("en-GB");
    vi.spyOn(api, "getVinProcessReplay").mockRejectedValue(
      new api.ApiError(404, "Not found"),
    );
    render(
      <Wrapper>
        <ProcessReplayPage vin="WBS4E0108RG271034" onBack={() => {}} />
      </Wrapper>,
    );
    await waitFor(() => expect(screen.getByTestId("replay-not-found")).toBeTruthy());
    expect(screen.queryByTestId("replay-retry")).toBeNull();
  });
});

describe("ProcessReplayPage — grey gap rendering (OQ-6 partial traceability)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders a grey gap for a prep stage with available=false", async () => {
    await i18n.changeLanguage("en-GB");
    const unavailableStageIdx = 6; // stage 7 (0-indexed)
    const data = makeReplayResponse({
      overallCompletenessPercent: 91,
      prepStages: Array.from({ length: 14 }, (_, i) => ({
        stageNumber: i + 1,
        stageName: `Stage ${i + 1}`,
        available: i !== unavailableStageIdx,
        samples:
          i !== unavailableStageIdx
            ? [{ ts: "2026-10-12T04:30:00Z", parameters: {}, tunnelPositionMm: null }]
            : [],
      })),
    });
    vi.spyOn(api, "getVinProcessReplay").mockResolvedValue(data);
    render(
      <Wrapper>
        <ProcessReplayPage vin="WBS4E0108RG271034" onBack={() => {}} />
      </Wrapper>,
    );
    await waitFor(() => expect(screen.getByTestId("replay-shell")).toBeTruthy());
    // At least one gap should be visible
    const gaps = screen.getAllByTestId("data-gap");
    expect(gaps.length).toBeGreaterThanOrEqual(1);
  });

  it("never silently omits unavailable stages — all 14 tracks are rendered", async () => {
    await i18n.changeLanguage("en-GB");
    const data = makeReplayResponse({
      prepStages: Array.from({ length: 14 }, (_, i) => ({
        stageNumber: i + 1,
        stageName: `Stage ${i + 1}`,
        available: false,
        samples: [],
      })),
    });
    vi.spyOn(api, "getVinProcessReplay").mockResolvedValue(data);
    render(
      <Wrapper>
        <ProcessReplayPage vin="WBS4E0108RG271034" onBack={() => {}} />
      </Wrapper>,
    );
    await waitFor(() => expect(screen.getByTestId("replay-shell")).toBeTruthy());
    const gaps = screen.getAllByTestId("data-gap");
    // All 14 prep stages unavailable — expect 14 gaps (plus possibly booth/climate)
    expect(gaps.length).toBeGreaterThanOrEqual(14);
  });
});

describe("ProcessReplayPage — completeness percentage display", () => {
  afterEach(() => vi.restoreAllMocks());

  it("displays the overallCompletenessPercent from the response", async () => {
    await i18n.changeLanguage("en-GB");
    vi.spyOn(api, "getVinProcessReplay").mockResolvedValue(
      makeReplayResponse({ overallCompletenessPercent: 91 }),
    );
    render(
      <Wrapper>
        <ProcessReplayPage vin="WBS4E0108RG271034" onBack={() => {}} />
      </Wrapper>,
    );
    await waitFor(() => expect(screen.getByTestId("completeness-pct")).toBeTruthy());
    expect(screen.getByTestId("completeness-pct").textContent).toBe("91%");
  });

  it("displays 100% completeness when all stages have data", async () => {
    await i18n.changeLanguage("en-GB");
    vi.spyOn(api, "getVinProcessReplay").mockResolvedValue(
      makeReplayResponse({ overallCompletenessPercent: 100 }),
    );
    render(
      <Wrapper>
        <ProcessReplayPage vin="WBS4E0108RG271034" onBack={() => {}} />
      </Wrapper>,
    );
    await waitFor(() => expect(screen.getByTestId("completeness-pct")).toBeTruthy());
    expect(screen.getByTestId("completeness-pct").textContent).toBe("100%");
  });
});

describe("ProcessReplayPage — data panel from snapshot", () => {
  afterEach(() => vi.restoreAllMocks());

  it("populates the data panel with parameter values from ThreadSnapshotResponse", async () => {
    await i18n.changeLanguage("en-GB");
    // Use narrow window so keyboard step moves a visible percentage
    const data = makeReplayResponse({
      replayWindowStart: "2026-10-12T00:00:00Z",
      replayWindowEnd: "2026-10-12T00:05:00Z",
    });
    vi.spyOn(api, "getVinProcessReplay").mockResolvedValue(data);
    vi.spyOn(api, "getVinThreadSnapshot").mockResolvedValue(makeSnapshotResponse());

    render(
      <Wrapper>
        <ProcessReplayPage vin="WBS4E0108RG271034" onBack={() => {}} />
      </Wrapper>,
    );
    await waitFor(() => expect(screen.getByTestId("replay-shell")).toBeTruthy());

    // Use keyboard to scrub to position (avoids jsdom getBoundingClientRect issues)
    const timeline = screen.getByRole("slider");
    fireEvent.keyDown(timeline, { key: "ArrowRight" });

    await waitFor(() => expect(screen.getByTestId("panel-stage-name")).toBeTruthy(), {
      timeout: 2000,
    });
    expect(screen.getByTestId("panel-stage-name").textContent).toBe("Stage 6");
    expect(screen.getByTestId("panel-stage-number")).toBeTruthy();
    expect(screen.getByTestId("panel-tunnel-position")).toBeTruthy();
    expect(screen.getByTestId("param-row-temperature_c")).toBeTruthy();
    expect(screen.getByTestId("param-row-ecoat_voltage_v")).toBeTruthy();
  });

  it("shows Data Unavailable in the panel when dataAvailable=false", async () => {
    await i18n.changeLanguage("en-GB");
    const data = makeReplayResponse({
      replayWindowStart: "2026-10-12T00:00:00Z",
      replayWindowEnd: "2026-10-12T00:05:00Z",
    });
    vi.spyOn(api, "getVinProcessReplay").mockResolvedValue(data);
    vi.spyOn(api, "getVinThreadSnapshot").mockResolvedValue(
      makeSnapshotResponse({ dataAvailable: false, parameters: {} }),
    );

    render(
      <Wrapper>
        <ProcessReplayPage vin="WBS4E0108RG271034" onBack={() => {}} />
      </Wrapper>,
    );
    await waitFor(() => expect(screen.getByTestId("replay-shell")).toBeTruthy());

    const timeline = screen.getByRole("slider");
    fireEvent.keyDown(timeline, { key: "ArrowRight" });

    await waitFor(() => expect(screen.getByTestId("panel-data-unavailable")).toBeTruthy(), {
      timeout: 2000,
    });
  });
});

describe("ProcessReplayPage — keyboard scrubber navigation", () => {
  afterEach(() => vi.restoreAllMocks());

  it("advances the scrubber position on ArrowRight key press", async () => {
    await i18n.changeLanguage("en-GB");
    // Use a narrow window so each step moves a noticeable percentage
    const data = makeReplayResponse({
      replayWindowStart: "2026-10-12T00:00:00Z",
      replayWindowEnd: "2026-10-12T00:05:00Z", // 5-minute window → 1 step = 100% (clamped)
    });
    vi.spyOn(api, "getVinProcessReplay").mockResolvedValue(data);
    vi.spyOn(api, "getVinThreadSnapshot").mockResolvedValue(makeSnapshotResponse());

    render(
      <Wrapper>
        <ProcessReplayPage vin="WBS4E0108RG271034" onBack={() => {}} />
      </Wrapper>,
    );
    await waitFor(() => expect(screen.getByTestId("replay-shell")).toBeTruthy());

    const slider = screen.getByRole("slider");
    // Home → 0, ArrowRight by 1 step (5 s / 300 s window = 1.67% → rounds to 2)
    fireEvent.keyDown(slider, { key: "Home" });
    expect(slider.getAttribute("aria-valuenow")).toBe("0");

    fireEvent.keyDown(slider, { key: "ArrowRight" });
    const afterRight = Number(slider.getAttribute("aria-valuenow"));
    // One step = (5000 ms / 300000 ms) * 100 = ~1.67% → Math.round = 2
    expect(afterRight).toBeGreaterThan(0);
  });

  it("decrements the scrubber position on ArrowLeft key press after End", async () => {
    await i18n.changeLanguage("en-GB");
    const data = makeReplayResponse({
      replayWindowStart: "2026-10-12T00:00:00Z",
      replayWindowEnd: "2026-10-12T00:05:00Z",
    });
    vi.spyOn(api, "getVinProcessReplay").mockResolvedValue(data);
    vi.spyOn(api, "getVinThreadSnapshot").mockResolvedValue(makeSnapshotResponse());

    render(
      <Wrapper>
        <ProcessReplayPage vin="WBS4E0108RG271034" onBack={() => {}} />
      </Wrapper>,
    );
    await waitFor(() => expect(screen.getByTestId("replay-shell")).toBeTruthy());

    const slider = screen.getByRole("slider");
    fireEvent.keyDown(slider, { key: "End" });
    expect(slider.getAttribute("aria-valuenow")).toBe("100");

    fireEvent.keyDown(slider, { key: "ArrowLeft" });
    const afterLeft = Number(slider.getAttribute("aria-valuenow"));
    expect(afterLeft).toBeLessThan(100);
  });
});

describe("Adoption event emission on Process Replay launch", () => {
  afterEach(() => vi.restoreAllMocks());

  it("emits PROCESS_REPLAY_LAUNCH adoption event when Launch Process Replay is clicked", async () => {
    await i18n.changeLanguage("en-GB");
    const emitSpy = vi.spyOn(adoptionEvents, "emitProcessReplayLaunch");
    const onLaunch = vi.fn();

    render(
      <Wrapper>
        <DefectDashboardPage
          rows={[
            {
              vin: "WBS4E0108RG271034",
              defectType: "Crater",
              aiConfidence: 73,
              suspectStage: "Stage 6",
              flaggedAt: "08:47:12",
              dataCompleteness: 91,
              status: "in_review",
            },
          ]}
          onLaunchReplay={onLaunch}
        />
      </Wrapper>,
    );

    const btn = screen.getByTestId("launch-replay-WBS4E0108RG271034");
    fireEvent.click(btn);

    expect(emitSpy).toHaveBeenCalledWith("WBS4E0108RG271034");
    expect(onLaunch).toHaveBeenCalledWith("WBS4E0108RG271034");
  });

  it("emits event with correct VIN", async () => {
    await i18n.changeLanguage("en-GB");
    const captured: string[] = [];
    vi.spyOn(adoptionEvents, "emitProcessReplayLaunch").mockImplementation((vin) => {
      captured.push(vin);
    });

    render(
      <Wrapper>
        <DefectDashboardPage
          rows={[
            {
              vin: "WBS4E0108RG271034",
              defectType: "Dust",
              aiConfidence: 58,
              suspectStage: "Stage 9",
              flaggedAt: "07:12:55",
              dataCompleteness: 100,
              status: "awaiting_root_cause",
            },
          ]}
          onLaunchReplay={() => {}}
        />
      </Wrapper>,
    );

    fireEvent.click(screen.getByTestId("launch-replay-WBS4E0108RG271034"));
    expect(captured).toEqual(["WBS4E0108RG271034"]);
  });
});

describe("AdoptionEvents — instance isolation", () => {
  it("getSessionId returns a non-empty string", () => {
    const id = adoptionEvents.getSessionId();
    expect(typeof id).toBe("string");
    expect(id.length).toBeGreaterThan(0);
  });

  it("resetAdoptionSession produces a different session ID", () => {
    const first = adoptionEvents.getSessionId();
    adoptionEvents.resetAdoptionSession();
    const second = adoptionEvents.getSessionId();
    expect(second).not.toBe(first);
  });
});
