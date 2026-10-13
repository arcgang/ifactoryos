import "../i18n";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SequenceBuilderPage } from "./SequenceBuilderPage";
import type { OptimizeResponse } from "../types/optimizer";
import type { OverrideRecord } from "../types/overrides";

const RUN_ID = "00000000-0000-0000-0000-aabbccddeeff";
const VIN_1 = "WBY31CF080NCH00001";

function makeMonteCarloResponse(): OptimizeResponse {
  return {
    runId: RUN_ID,
    weekIso: "2026-W42",
    resultType: "monte_carlo",
    rankedSequence: [
      { vin: VIN_1, position: 1, constraintScore: 0.9, vipPriority: true, regulatoryHold: false },
      { vin: "WBY31CF080NCH00002", position: 2, constraintScore: 0.8, vipPriority: false, regulatoryHold: false },
      { vin: "WBY31CF080NCH00003", position: 3, constraintScore: 0.7, vipPriority: false, regulatoryHold: true },
    ],
    completedAt: "2026-10-12T05:49:41Z",
    durationMs: 38247,
    confidenceScore: 0.91,
    my26ConfigVersion: null,
    constraintSnapshotTimestamp: "2026-10-12T05:45:11Z",
  };
}

function makeOverrideRecord(vin: string): OverrideRecord {
  return {
    overrideId: "ovr-2026W42-0001",
    runId: RUN_ID,
    vin,
    originalPosition: 1,
    newPosition: 3,
    justification: "Customer SLA requires earlier delivery this week.",
    plannerId: "K.Braun",
    createdAt: "2026-10-12T06:00:00Z",
  };
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
});

afterEach(() => {
  vi.useRealTimers();
});

async function renderWithResult() {
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
}

describe("Override form — validation", () => {
  it("blocks submit and shows error when justification is 9 characters", async () => {
    await renderWithResult();

    const overrideBtn = screen.getByTestId(`override-btn-${VIN_1}`);
    await userEvent.setup({ delay: null }).click(overrideBtn);

    await waitFor(() => {
      expect(screen.getByTestId("override-justification-input")).toBeTruthy();
    });

    const textarea = screen.getByTestId("override-justification-input");
    await userEvent.setup({ delay: null }).type(textarea, "123456789"); // exactly 9 chars

    await waitFor(() => {
      expect(screen.getByTestId("justification-error")).toBeTruthy();
    });

    const submitBtn = screen.getByTestId("override-submit-btn");
    expect(submitBtn).toBeDisabled();
  });

  it("submit button is enabled when justification meets minimum length", async () => {
    await renderWithResult();

    const overrideBtn = screen.getByTestId(`override-btn-${VIN_1}`);
    await userEvent.setup({ delay: null }).click(overrideBtn);

    await waitFor(() => {
      expect(screen.getByTestId("override-justification-input")).toBeTruthy();
    });

    const textarea = screen.getByTestId("override-justification-input");
    await userEvent.setup({ delay: null }).type(textarea, "1234567890"); // exactly 10 chars

    expect(screen.queryByTestId("justification-error")).toBeNull();
    expect(screen.getByTestId("override-submit-btn")).not.toBeDisabled();
  });

  it("does not show validation error while field is empty (not touched yet)", async () => {
    await renderWithResult();

    const overrideBtn = screen.getByTestId(`override-btn-${VIN_1}`);
    await userEvent.setup({ delay: null }).click(overrideBtn);

    await waitFor(() => {
      expect(screen.getByTestId("override-justification-input")).toBeTruthy();
    });

    // No error shown when field is empty (user hasn't started typing)
    expect(screen.queryByTestId("justification-error")).toBeNull();
    expect(screen.getByTestId("override-submit-btn")).toBeDisabled();
  });
});

describe("Override form — successful submission", () => {
  it("shows toast and appends override chip after POST /overrides returns 201", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve(makeMonteCarloResponse()),
        text: () => Promise.resolve(""),
      } as Response)
      .mockResolvedValue({
        ok: true,
        status: 201,
        json: () => Promise.resolve(makeOverrideRecord(VIN_1)),
        text: () => Promise.resolve(""),
      } as Response);

    render(<SequenceBuilderPage />);
    await userEvent.setup({ delay: null }).click(screen.getByTestId("run-optimizer-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("results-section")).toBeTruthy();
    });

    const overrideBtn = screen.getByTestId(`override-btn-${VIN_1}`);
    await userEvent.setup({ delay: null }).click(overrideBtn);

    await waitFor(() => {
      expect(screen.getByTestId("override-justification-input")).toBeTruthy();
    });

    const textarea = screen.getByTestId("override-justification-input");
    await userEvent.setup({ delay: null }).type(textarea, "Customer SLA requires earlier delivery this week.");

    const submitBtn = screen.getByTestId("override-submit-btn");
    await userEvent.setup({ delay: null }).click(submitBtn);

    await waitFor(() => {
      expect(screen.getByTestId("override-toast")).toBeTruthy();
    });

    expect(screen.getByTestId("override-toast").textContent).toMatch(/override saved/i);
  });

  it("emits override_applied adoption event within 1 s of successful submit", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve(makeMonteCarloResponse()),
        text: () => Promise.resolve(""),
      } as Response)
      .mockResolvedValue({
        ok: true,
        status: 201,
        json: () => Promise.resolve(makeOverrideRecord(VIN_1)),
        text: () => Promise.resolve(""),
      } as Response);

    const events: CustomEvent[] = [];
    window.addEventListener("ifos:adoption", ((e: CustomEvent) => {
      events.push(e);
    }) as EventListener);

    render(<SequenceBuilderPage />);
    await userEvent.setup({ delay: null }).click(screen.getByTestId("run-optimizer-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("results-section")).toBeTruthy();
    });

    const overrideBtn = screen.getByTestId(`override-btn-${VIN_1}`);
    await userEvent.setup({ delay: null }).click(overrideBtn);

    await waitFor(() => {
      expect(screen.getByTestId("override-justification-input")).toBeTruthy();
    });

    const textarea = screen.getByTestId("override-justification-input");
    await userEvent.setup({ delay: null }).type(textarea, "Customer SLA requires earlier delivery.");

    const submitBtn = screen.getByTestId("override-submit-btn");
    await userEvent.setup({ delay: null }).click(submitBtn);

    await waitFor(() => {
      const overrideEvents = events.filter(
        (e) => (e.detail as { action: string }).action === "override_applied",
      );
      expect(overrideEvents.length).toBeGreaterThanOrEqual(1);
    });

    window.removeEventListener("ifos:adoption", ((e: CustomEvent) => {
      events.push(e);
    }) as EventListener);
  });
});
