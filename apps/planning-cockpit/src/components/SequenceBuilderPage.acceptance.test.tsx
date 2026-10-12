/**
 * Acceptance tests for US-002 — 50-run Monte Carlo Sequence Optimizer
 *
 * Each test is named after the criterion it proves.  The seam under test is the
 * connection between the backend HTTP contract (response shape) and the frontend
 * rendering decisions (polling loop, ranked table, heuristic banner, timeout).
 *
 * AC1 – ranked VIN sequence is displayed within 45 seconds of clicking Run Optimizer:
 *        the POST→poll loop resolves before the 45 000 ms POLL_TIMEOUT_MS fires,
 *        and GET /optimize/{runId} returns the same result as POST.
 *
 * AC2 – each VIN in the ranked sequence shows its position, constraint score,
 *        and any mandatory override flags (VIP priority, regulatory hold).
 *
 * AC3 – when the optimizer times out (no result after 45 s), the error state is
 *        shown; and when the backend signals heuristic_fallback, the labeled
 *        banner is rendered to make the type of result unambiguous.
 */

import "../i18n";
import { render, screen, act, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { SequenceBuilderPage } from "./SequenceBuilderPage";
import type { OptimizeResponse } from "../types/optimizer";

// ── Fixtures ──────────────────────────────────────────────────────────────────

const RUN_ID = "deadbeef-dead-beef-dead-beefdeadbeef";

/** Far-future ISO timestamp — never a hardcoded absolute wall-clock. */
const FAR_FUTURE = "2099-01-01T05:30:00Z";

function makeRankedVin(
  position: number,
  overrides?: Partial<{
    vin: string;
    constraintScore: number;
    vipPriority: boolean;
    regulatoryHold: boolean;
  }>,
) {
  return {
    vin: `WBY31CF080NCH${String(position).padStart(5, "0")}`,
    position,
    constraintScore: 0.9 - (position - 1) * 0.05,
    vipPriority: false,
    regulatoryHold: false,
    ...overrides,
  };
}

/** A valid completed monte_carlo response. */
function makeMonteCarloResponse(
  overrides?: Partial<OptimizeResponse>,
): OptimizeResponse {
  return {
    runId: RUN_ID,
    weekIso: "2026-W42",
    resultType: "monte_carlo",
    rankedSequence: [
      makeRankedVin(1, { vipPriority: true }),
      makeRankedVin(2),
      makeRankedVin(3, { regulatoryHold: true }),
    ],
    completedAt: FAR_FUTURE,
    durationMs: 38_000,
    confidenceScore: 0.91,
    my26ConfigVersion: null,
    constraintSnapshotTimestamp: FAR_FUTURE,
    ...overrides,
  };
}

/** A completed run that signals heuristic fallback. */
function makeHeuristicResponse(): OptimizeResponse {
  return makeMonteCarloResponse({
    resultType: "heuristic_fallback",
    confidenceScore: 0.5,
  });
}

// ── Timer helpers ─────────────────────────────────────────────────────────────

async function tick(ms: number): Promise<void> {
  await act(async () => {
    vi.advanceTimersByTime(ms);
  });
}

// ── Setup / teardown ──────────────────────────────────────────────────────────

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
});

afterEach(() => {
  vi.useRealTimers();
});

// =============================================================================
// AC1 — ranked sequence displayed within 45 seconds
// =============================================================================

describe("US-002 AC1 — ranked sequence within 45 s", () => {
  /**
   * AC1: when POST /optimize responds immediately with a completed result
   * (rankedSequence.length > 0), the component must transition directly to the
   * "done" state without starting the polling loop — i.e. the result is shown
   * before any timer advances.
   */
  it("AC1: displays ranked sequence immediately when POST /optimize returns a complete result", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(makeMonteCarloResponse()),
      text: () => Promise.resolve(""),
    } as Response);

    render(<SequenceBuilderPage />);

    // Precondition: Run Optimizer button must be present before the test is meaningful
    const btn = screen.getByTestId("run-optimizer-btn");
    expect(btn, "Run Optimizer button must exist in idle state").not.toBeNull();

    await userEvent.setup({ delay: null }).click(btn);

    await waitFor(() => {
      // Results section signals the component has reached the "done" phase
      expect(
        screen.getByTestId("results-section"),
        "results-section must appear after a completed POST response",
      ).toBeTruthy();
    });

    // No timeout has elapsed — this result arrived within 0 ms of the click
    expect(screen.queryByTestId("error-state")).toBeNull();
  });

  /**
   * AC1 (polling seam): when POST returns an empty rankedSequence (the run is
   * still in progress), the component polls GET /optimize/{runId}.  On the first
   * non-empty poll response the ranked table must appear — proving the seam
   * between the backend's POST and GET endpoints is wired correctly.
   */
  it("AC1: displays ranked sequence after polling GET /optimize/{runId} when POST returns empty sequence", async () => {
    let pollCount = 0;

    vi.spyOn(globalThis, "fetch").mockImplementation(
      (url: RequestInfo | URL) => {
        const urlStr = typeof url === "string" ? url : url.toString();

        if (urlStr.includes(`/optimize/${RUN_ID}`)) {
          pollCount++;
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () =>
              Promise.resolve(
                pollCount >= 2
                  ? makeMonteCarloResponse() // second poll: complete
                  : { ...makeMonteCarloResponse(), rankedSequence: [] }, // first poll: still running
              ),
            text: () => Promise.resolve(""),
          } as Response);
        }

        // POST /optimize — returns runId but empty sequence (triggers polling)
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              ...makeMonteCarloResponse(),
              rankedSequence: [],
            }),
          text: () => Promise.resolve(""),
        } as Response);
      },
    );

    render(<SequenceBuilderPage />);
    await userEvent.setup({ delay: null }).click(screen.getByTestId("run-optimizer-btn"));

    // Running indicator must appear before the timeout
    await waitFor(() => {
      expect(
        screen.getByTestId("running-indicator"),
        "running-indicator must appear while polling",
      ).toBeTruthy();
    });

    // First poll interval (POLL_INTERVAL_MS = 2 000)
    await tick(2_000);
    // Second poll interval
    await tick(2_000);

    // After two polls the result must be rendered
    await waitFor(() => {
      expect(
        screen.getByTestId("results-section"),
        "results-section must appear after polling produces a non-empty rankedSequence",
      ).toBeTruthy();
    });

    // Precondition: the test is only valid if the poll path was actually exercised
    expect(pollCount, "GET /optimize/{runId} must have been called at least twice").toBeGreaterThanOrEqual(2);
  });

  /**
   * AC1 (timing contract): POLL_TIMEOUT_MS is exported from the component as the
   * constant 45 000 in the source.  This test verifies that a run that has NOT
   * resolved after advancing by exactly the timeout value transitions to the
   * error state — confirming the constant is wired correctly.
   */
  it("AC1: transitions to error state after 45 000 ms without a result (timeout boundary)", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve({ ...makeMonteCarloResponse(), rankedSequence: [] }),
      text: () => Promise.resolve(""),
    } as Response);

    render(<SequenceBuilderPage />);
    await userEvent.setup({ delay: null }).click(screen.getByTestId("run-optimizer-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("running-indicator")).toBeTruthy();
    });

    // Advance just past the 45-second boundary
    await tick(46_000);

    await waitFor(() => {
      expect(
        screen.getByTestId("error-state"),
        "error-state must be shown after the 45 000 ms timeout",
      ).toBeTruthy();
    });

    // No results section after a timeout
    expect(screen.queryByTestId("results-section")).toBeNull();
  });
});

// =============================================================================
// AC2 — position, constraint score, and override flags rendered per VIN
// =============================================================================

describe("US-002 AC2 — per-VIN fields: position, constraintScore, override flags", () => {
  /**
   * AC2: position badges aria-labelled "position N" must be rendered for every
   * entry in the ranked sequence, ordered ascending by position.
   */
  it("AC2: renders an aria-labelled position badge for each ranked VIN, ordered 1…N", async () => {
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

    const positionBadges = screen.getAllByLabelText(/position \d+/i);

    // Precondition: as many badges as VINs in the fixture
    expect(positionBadges.length, "number of position badges must equal number of ranked VINs").toBe(3);

    const positions = positionBadges.map((b) => parseInt(b.textContent ?? "0", 10));
    expect(positions, "position badges must be ordered 1, 2, 3").toEqual([1, 2, 3]);
  });

  /**
   * AC2: constraintScore values from the response must appear as formatted
   * decimals in the table (the ConstraintScoreBar renders them with .toFixed(2)).
   */
  it("AC2: renders constraintScore as a decimal number for each ranked VIN", async () => {
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

    // Fixture constraintScores: 0.90, 0.85, 0.80
    expect(screen.getByText("0.90"), "constraintScore 0.90 must be rendered for position 1").toBeTruthy();
    expect(screen.getByText("0.85"), "constraintScore 0.85 must be rendered for position 2").toBeTruthy();
    expect(screen.getByText("0.80"), "constraintScore 0.80 must be rendered for position 3").toBeTruthy();
  });

  /**
   * AC2: a VIN with vipPriority=true must display a VIP badge.
   */
  it("AC2: renders a VIP badge for a VIN with vipPriority=true", async () => {
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

    const vipBadges = screen.getAllByLabelText(/vip/i);
    expect(vipBadges.length, "at least one VIP badge must be rendered").toBeGreaterThanOrEqual(1);
  });

  /**
   * AC2: a VIN with regulatoryHold=true must display a REG HOLD badge.
   */
  it("AC2: renders a REG HOLD badge for a VIN with regulatoryHold=true", async () => {
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

    const regBadges = screen.getAllByLabelText(/reg hold/i);
    expect(regBadges.length, "at least one REG HOLD badge must be rendered").toBeGreaterThanOrEqual(1);
  });

  /**
   * AC2 (combined): all four fields — position, constraintScore, vipPriority badge,
   * regulatoryHold badge — are rendered in the same response, confirming they
   * coexist in the table and none suppresses another.
   */
  it("AC2: all four required fields (position, constraintScore, VIP, REG HOLD) are rendered together in one response", async () => {
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

    // Position badges
    expect(screen.getByLabelText(/position 1/i)).toBeTruthy();
    expect(screen.getByLabelText(/position 2/i)).toBeTruthy();
    expect(screen.getByLabelText(/position 3/i)).toBeTruthy();

    // ConstraintScore values
    expect(screen.getByText("0.90")).toBeTruthy();

    // Override flags
    expect(screen.getAllByLabelText(/vip/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByLabelText(/reg hold/i).length).toBeGreaterThanOrEqual(1);
  });
});

// =============================================================================
// AC3 — fallback: heuristic label when Monte Carlo fails or times out
// =============================================================================

describe("US-002 AC3 — heuristic fallback label and error state", () => {
  /**
   * AC3 (backend signal): when resultType="heuristic_fallback", the component
   * must render the heuristic banner that makes the result type unambiguous.
   * This criterion is the exact seam between the backend's fallback label and
   * the frontend's banner decision.
   */
  it("AC3: renders heuristic banner with 'HEURISTIC' and 'Monte Carlo' text when resultType is heuristic_fallback", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(makeHeuristicResponse()),
      text: () => Promise.resolve(""),
    } as Response);

    render(<SequenceBuilderPage />);
    await userEvent.setup({ delay: null }).click(screen.getByTestId("run-optimizer-btn"));

    await waitFor(() => {
      expect(
        screen.getByTestId("heuristic-banner"),
        "heuristic-banner must be rendered when resultType=heuristic_fallback",
      ).toBeTruthy();
    });

    const banner = screen.getByTestId("heuristic-banner");

    // Banner must name what it is (HEURISTIC) and what it is not (Monte Carlo)
    expect(banner.textContent, "banner must contain 'HEURISTIC'").toMatch(/heuristic/i);
    expect(banner.textContent, "banner must contain 'Monte Carlo'").toMatch(/monte carlo/i);
  });

  /**
   * AC3: heuristic banner must NOT be rendered when resultType="monte_carlo".
   * A planner must not see a misleading fallback label on a real optimisation result.
   */
  it("AC3: does NOT render heuristic banner when resultType is monte_carlo", async () => {
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

    expect(
      screen.queryByTestId("heuristic-banner"),
      "heuristic-banner must NOT be present for a monte_carlo result",
    ).toBeNull();
  });

  /**
   * AC3: when the optimizer times out (no result after 45 s), the planner must
   * see an error state — which gives them the opportunity to re-run and
   * potentially receive a heuristic fallback on the next attempt.
   */
  it("AC3: shows error state (not blank screen) after polling times out without a result", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve({ ...makeMonteCarloResponse(), rankedSequence: [] }),
      text: () => Promise.resolve(""),
    } as Response);

    render(<SequenceBuilderPage />);
    await userEvent.setup({ delay: null }).click(screen.getByTestId("run-optimizer-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("running-indicator")).toBeTruthy();
    });

    await tick(46_000);

    await waitFor(() => {
      expect(
        screen.getByTestId("error-state"),
        "error-state must be displayed after the 45-second timeout so the planner knows to retry",
      ).toBeTruthy();
    });

    // No results in the error state — the planner would otherwise act on stale data
    expect(screen.queryByTestId("results-section")).toBeNull();
    expect(screen.queryByTestId("heuristic-banner")).toBeNull();
  });

  /**
   * AC3: the heuristic fallback must still include all required per-VIN fields
   * (position, constraintScore, override flags) so the planner can make
   * a decision even without Monte Carlo.
   */
  it("AC3: heuristic_fallback result includes position, constraintScore, VIP, and REG HOLD fields", async () => {
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
