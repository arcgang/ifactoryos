import "../i18n";
import { render, screen, act, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SequenceBuilderPage } from "./SequenceBuilderPage";
import type { OptimizeResponse } from "../types/optimizer";

// ── Fixtures ──────────────────────────────────────────────────────────────────

const RUN_ID = "00000000-0000-0000-0000-aabbccddeeff";

function makeRankedVin(position: number, overrides?: Partial<{
  vin: string;
  constraintScore: number;
  vipPriority: boolean;
  regulatoryHold: boolean;
}>) {
  return {
    vin: `WBY31CF080NCH${String(position).padStart(5, "0")}`,
    position,
    constraintScore: 0.9 - (position - 1) * 0.05,
    vipPriority: false,
    regulatoryHold: false,
    ...overrides,
  };
}

function makeMonteCarloResponse(overrides?: Partial<OptimizeResponse>): OptimizeResponse {
  return {
    runId: RUN_ID,
    weekIso: "2026-W42",
    resultType: "monte_carlo",
    rankedSequence: [
      makeRankedVin(1, { vipPriority: true }),
      makeRankedVin(2),
      makeRankedVin(3, { regulatoryHold: true }),
    ],
    completedAt: "2026-10-12T05:49:41Z",
    durationMs: 38247,
    confidenceScore: 0.91,
    my26ConfigVersion: null,
    constraintSnapshotTimestamp: "2026-10-12T05:45:11Z",
    ...overrides,
  };
}

function makeHeuristicResponse(): OptimizeResponse {
  return makeMonteCarloResponse({ resultType: "heuristic_fallback", confidenceScore: 0.5 });
}

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Advance fake timers by ms without moving wall-clock. */
async function tick(ms: number) {
  await act(async () => {
    vi.advanceTimersByTime(ms);
  });
}

// ── Setup ─────────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
});

afterEach(() => {
  vi.useRealTimers();
});

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("SequenceBuilderPage — idle state", () => {
  it("renders Run Optimizer button in idle state", () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(makeMonteCarloResponse()),
      text: () => Promise.resolve(""),
    } as Response);

    render(<SequenceBuilderPage />);

    expect(screen.getByTestId("run-optimizer-btn")).toBeTruthy();
    expect(screen.getByTestId("run-optimizer-btn")).not.toBeDisabled();
  });

  it("renders empty sequence state before any run", () => {
    render(<SequenceBuilderPage />);
    expect(screen.getByText(/no optimised sequence/i)).toBeTruthy();
  });
});

describe("SequenceBuilderPage — monte_carlo result", () => {
  it("renders ranked sequence table with position, constraintScore, VIP badge, and REGULATORY HOLD badge", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(makeMonteCarloResponse()),
      text: () => Promise.resolve(""),
    } as Response);

    render(<SequenceBuilderPage />);

    await userEvent.setup({ delay: null }).click(screen.getByTestId("run-optimizer-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("results-section")).toBeTruthy();
    });

    // position 1 — VIP
    expect(screen.getByLabelText(/position 1/i)).toBeTruthy();
    // position 3 — REG HOLD
    expect(screen.getByLabelText(/position 3/i)).toBeTruthy();

    // VIP badge for position 1
    const vipBadges = screen.getAllByLabelText(/vip/i);
    expect(vipBadges.length).toBeGreaterThanOrEqual(1);

    // REG HOLD badge for position 3
    const regBadges = screen.getAllByLabelText(/reg hold/i);
    expect(regBadges.length).toBeGreaterThanOrEqual(1);

    // constraintScore values displayed as decimals
    expect(screen.getByText("0.90")).toBeTruthy();
    expect(screen.getByText("0.85")).toBeTruthy();
    expect(screen.getByText("0.80")).toBeTruthy();
  });

  it("does NOT render heuristic banner when resultType is monte_carlo", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(makeMonteCarloResponse()),
      text: () => Promise.resolve(""),
    } as Response);

    render(<SequenceBuilderPage />);
    await userEvent.setup({ delay: null }).click(screen.getByTestId("run-optimizer-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("results-section")).toBeTruthy();
    });

    expect(screen.queryByTestId("heuristic-banner")).toBeNull();
  });

  it("rows are ordered ascending by position", async () => {
    const resp = makeMonteCarloResponse();
    // shuffle the rankedSequence
    resp.rankedSequence = [...resp.rankedSequence].reverse();

    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(resp),
      text: () => Promise.resolve(""),
    } as Response);

    render(<SequenceBuilderPage />);
    await userEvent.setup({ delay: null }).click(screen.getByTestId("run-optimizer-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("results-section")).toBeTruthy();
    });

    const badges = screen.getAllByLabelText(/position \d+/i);
    const positions = badges.map((b) => parseInt(b.textContent ?? "0", 10));
    expect(positions).toEqual([1, 2, 3]);
  });
});

describe("SequenceBuilderPage — heuristic_fallback result", () => {
  it("renders heuristic fallback banner when resultType is heuristic_fallback", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(makeHeuristicResponse()),
      text: () => Promise.resolve(""),
    } as Response);

    render(<SequenceBuilderPage />);
    await userEvent.setup({ delay: null }).click(screen.getByTestId("run-optimizer-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("heuristic-banner")).toBeTruthy();
    });

    // Banner text includes "HEURISTIC — not Monte Carlo optimum"
    expect(screen.getByTestId("heuristic-banner").textContent).toMatch(/heuristic/i);
    expect(screen.getByTestId("heuristic-banner").textContent).toMatch(/monte carlo/i);
  });

  it("still shows position, constraintScore, and flag fields with heuristic result", async () => {
    const resp = makeHeuristicResponse();
    resp.rankedSequence = [
      makeRankedVin(1, { vipPriority: true }),
      makeRankedVin(2, { regulatoryHold: true }),
    ];

    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(resp),
      text: () => Promise.resolve(""),
    } as Response);

    render(<SequenceBuilderPage />);
    await userEvent.setup({ delay: null }).click(screen.getByTestId("run-optimizer-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("results-section")).toBeTruthy();
    });

    expect(screen.getByLabelText(/position 1/i)).toBeTruthy();
    expect(screen.getByLabelText(/position 2/i)).toBeTruthy();
    expect(screen.getAllByLabelText(/vip/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByLabelText(/reg hold/i).length).toBeGreaterThanOrEqual(1);
  });
});

describe("SequenceBuilderPage — polling", () => {
  it("polls GET /optimize/{runId} every 2 s and stops on 200 with a result", async () => {
    let callCount = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation((url: RequestInfo | URL) => {
      const urlStr = typeof url === "string" ? url : url.toString();
      if (urlStr.includes(`/optimize/${RUN_ID}`)) {
        callCount++;
        if (callCount >= 2) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve(makeMonteCarloResponse()),
            text: () => Promise.resolve(""),
          } as Response);
        }
        // First poll returns a response without rankedSequence (still running)
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ ...makeMonteCarloResponse(), rankedSequence: [] }),
          text: () => Promise.resolve(""),
        } as Response);
      }
      // POST /optimize — returns runId but empty rankedSequence (triggers polling)
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ ...makeMonteCarloResponse(), rankedSequence: [] }),
        text: () => Promise.resolve(""),
      } as Response);
    });

    render(<SequenceBuilderPage />);
    await userEvent.setup({ delay: null }).click(screen.getByTestId("run-optimizer-btn"));

    // Wait for running indicator
    await waitFor(() => {
      expect(screen.getByTestId("running-indicator")).toBeTruthy();
    });

    // Advance 2 s — first poll (returns empty)
    await tick(2000);

    // Advance another 2 s — second poll (returns result)
    await tick(2000);

    await waitFor(() => {
      expect(screen.getByTestId("results-section")).toBeTruthy();
    });

    expect(callCount).toBeGreaterThanOrEqual(2);
  });
});

describe("SequenceBuilderPage — OTel adoption event", () => {
  it("emits optimizer_run adoption event within 1 s of button click", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(makeMonteCarloResponse()),
      text: () => Promise.resolve(""),
    } as Response);

    const events: CustomEvent[] = [];
    window.addEventListener("ifos:adoption", ((e: CustomEvent) => {
      events.push(e);
    }) as EventListener);

    render(<SequenceBuilderPage />);
    await userEvent.setup({ delay: null }).click(screen.getByTestId("run-optimizer-btn"));

    await waitFor(() => {
      const optimizerEvents = events.filter(
        (e) => (e.detail as { action: string }).action === "optimizer_run",
      );
      expect(optimizerEvents.length).toBeGreaterThanOrEqual(1);
    });

    window.removeEventListener("ifos:adoption", ((e: CustomEvent) => {
      events.push(e);
    }) as EventListener);
  });
});

describe("SequenceBuilderPage — timeout branch", () => {
  it("shows error state after 45 s of polling with no result", async () => {
    // POST returns empty sequence (triggers polling)
    // All polls return empty sequence too
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ ...makeMonteCarloResponse(), rankedSequence: [] }),
      text: () => Promise.resolve(""),
    } as Response);

    render(<SequenceBuilderPage />);
    await userEvent.setup({ delay: null }).click(screen.getByTestId("run-optimizer-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("running-indicator")).toBeTruthy();
    });

    // Advance past the 45-second timeout
    await tick(46000);

    await waitFor(() => {
      expect(screen.getByTestId("error-state")).toBeTruthy();
    });
  });
});
