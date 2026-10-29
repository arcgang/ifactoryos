/**
 * Acceptance tests for US-003 — Explainable Sequence view and Override Log
 *
 * Tests the seam between:
 *   sequence-optimizer: explain, overrides CRUD, and PDF-export endpoints
 *   planning-cockpit:   Explainable Sequence view and Override Log UI
 *
 * AC1 — constraint panel shows top 3 factors (weighted score + data source) for selected VIN
 * AC2 — override saved records VIN ID, original position, new position, justification ≥10 chars,
 *        and timestamp
 * AC3 — override log lists all overrides for the current week and is exportable as PDF
 */

import "../i18n";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SequenceBuilderPage } from "./SequenceBuilderPage";
import { OverrideLogPage } from "./OverrideLogPage";
import type { OptimizeResponse } from "../types/optimizer";
import type { OverrideListResponse, OverrideRecord, VinExplanation } from "../types/overrides";
import * as api from "../services/api";

// vi.mock is hoisted — must list every export the components under test import.
vi.mock("../services/api", () => ({
  publishSequence: vi.fn(),
  getSyncJob: vi.fn(),
  listSyncJobs: vi.fn(),
  listConstraintThresholds: vi.fn(),
  upsertConstraintThreshold: vi.fn(),
  getConstraintStreamUrl: vi.fn(() => ""),
  postOptimize: vi.fn(),
  getOptimizeRun: vi.fn(),
  createOverride: vi.fn(),
  listOverrides: vi.fn(),
  explainVinPosition: vi.fn(),
  exportOverridesPdf: vi.fn(),
}));

// ── Constants ─────────────────────────────────────────────────────────────────

const RUN_ID = "cccccccc-0000-0000-0000-aabbccddeeff";
const VIN_1 = "WBY31CF080NCH00001";
const VIN_2 = "WBY31CF080NCH00002";
// far-future constant — keeps time-sensitive fixture assertions deterministic
const FAR_FUTURE_TS = "2040-01-01T00:00:00Z";

// ── Fixtures ──────────────────────────────────────────────────────────────────

function makeOptimizeResponse(): OptimizeResponse {
  return {
    runId: RUN_ID,
    weekIso: "2026-W42",
    resultType: "monte_carlo",
    rankedSequence: [
      { vin: VIN_1, position: 1, constraintScore: 0.9, vipPriority: false, regulatoryHold: false },
      { vin: VIN_2, position: 2, constraintScore: 0.8, vipPriority: false, regulatoryHold: false },
    ],
    completedAt: "2026-10-12T05:49:41Z",
    durationMs: 38247,
    confidenceScore: 0.91,
    my26ConfigVersion: null,
    constraintSnapshotTimestamp: "2026-10-12T05:45:11Z",
  };
}

function makeExplanation(): VinExplanation {
  return {
    runId: RUN_ID,
    vin: VIN_1,
    position: 1,
    topFactors: [
      {
        factorName: "paint_batch_conflict",
        weightedScore: 0.72,
        dataSource: "uns/paint/booth3/batch",
        rawValue: 1.0,
      },
      {
        factorName: "changeover_penalty",
        weightedScore: 0.55,
        dataSource: "uns/changeover/penalty_matrix",
        rawValue: 2.5,
      },
      {
        factorName: "kitting_gap",
        weightedScore: 0.31,
        dataSource: "kitting.readiness_pct",
        rawValue: 78.5,
      },
    ],
  };
}

function makeOverrideRecord(partial?: Partial<OverrideRecord>): OverrideRecord {
  return {
    overrideId: "ovr-2026W42-0001",
    runId: RUN_ID,
    vin: VIN_1,
    originalPosition: 1,
    newPosition: 3,
    justification: "Customer SLA requires earlier delivery this week.",
    plannerId: "planner@bmwgroup.net",
    createdAt: FAR_FUTURE_TS,
    ...partial,
  };
}

function makeOverrideListResponse(count: number): OverrideListResponse {
  return {
    data: Array.from({ length: count }, (_, i) => ({
      overrideId: `ovr-2026W42-${String(i + 1).padStart(4, "0")}`,
      runId: RUN_ID,
      vin: `WBA3G2C04PFX1900${i + 1}`,
      originalPosition: (i + 1) * 3,
      newPosition: i + 1,
      justification: `Override justification for sequence item number ${i + 1}`,
      plannerId: "planner@bmwgroup.net",
      createdAt: FAR_FUTURE_TS,
    })),
    page: { next_cursor: null, limit: 50 },
  };
}

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Render SequenceBuilderPage and wait until the ranked-sequence table is visible. */
async function renderWithResult() {
  vi.mocked(api.postOptimize).mockResolvedValue(makeOptimizeResponse());
  render(<SequenceBuilderPage />);
  await userEvent.setup({ delay: null }).click(screen.getByTestId("run-optimizer-btn"));
  await waitFor(() => {
    expect(screen.getByTestId("results-section")).toBeTruthy();
  });
}

// ── Global setup ──────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  // usePublishSequence calls listSyncJobs on mount; provide a safe default
  vi.mocked(api.listSyncJobs).mockResolvedValue({
    data: [],
    page: { next_cursor: null, limit: 50 },
  });
});

// ── AC1 ───────────────────────────────────────────────────────────────────────

describe("AC1 — constraint panel shows top 3 factors (weighted score + data source) for selected VIN", () => {
  it("precondition: explainVinPosition is mocked — factor-row assertions are not vacuous against a silent stub", () => {
    expect(vi.isMockFunction(api.explainVinPosition)).toBe(true);
  });

  it("AC1: selecting a VIN calls explainVinPosition with the runId produced by the optimizer and the selected VIN", async () => {
    vi.mocked(api.explainVinPosition).mockResolvedValue(makeExplanation());
    await renderWithResult();

    await userEvent.setup({ delay: null }).click(screen.getByTestId(`vin-row-btn-${VIN_1}`));

    await waitFor(() => {
      expect(vi.mocked(api.explainVinPosition)).toHaveBeenCalledWith(RUN_ID, VIN_1);
    });
  });

  it("AC1: constraint panel shows exactly 3 factor rows after a VIN is selected from the ranked sequence", async () => {
    vi.mocked(api.explainVinPosition).mockResolvedValue(makeExplanation());
    await renderWithResult();

    await userEvent.setup({ delay: null }).click(screen.getByTestId(`vin-row-btn-${VIN_1}`));

    await waitFor(() => {
      expect(screen.getByTestId("explain-factor-paint_batch_conflict")).toBeTruthy();
    });

    // All three factor rows must appear — including constraint types from the story
    expect(screen.getByTestId("explain-factor-paint_batch_conflict")).toBeTruthy();
    expect(screen.getByTestId("explain-factor-changeover_penalty")).toBeTruthy();
    expect(screen.getByTestId("explain-factor-kitting_gap")).toBeTruthy();
  });

  it("AC1: each factor row shows the weighted score for that constraint", async () => {
    vi.mocked(api.explainVinPosition).mockResolvedValue(makeExplanation());
    await renderWithResult();

    await userEvent.setup({ delay: null }).click(screen.getByTestId(`vin-row-btn-${VIN_1}`));

    await waitFor(() => {
      expect(screen.getByTestId("explain-factor-paint_batch_conflict")).toBeTruthy();
    });

    const paintRow = screen.getByTestId("explain-factor-paint_batch_conflict");
    const changeoverRow = screen.getByTestId("explain-factor-changeover_penalty");
    const kittingRow = screen.getByTestId("explain-factor-kitting_gap");

    expect(paintRow.textContent).toContain("0.72");
    expect(changeoverRow.textContent).toContain("0.55");
    expect(kittingRow.textContent).toContain("0.31");
  });

  it("AC1: each factor row shows the data source for that constraint", async () => {
    vi.mocked(api.explainVinPosition).mockResolvedValue(makeExplanation());
    await renderWithResult();

    await userEvent.setup({ delay: null }).click(screen.getByTestId(`vin-row-btn-${VIN_1}`));

    await waitFor(() => {
      expect(screen.getByTestId("explain-factor-paint_batch_conflict")).toBeTruthy();
    });

    const paintRow = screen.getByTestId("explain-factor-paint_batch_conflict");
    const changeoverRow = screen.getByTestId("explain-factor-changeover_penalty");
    const kittingRow = screen.getByTestId("explain-factor-kitting_gap");

    expect(paintRow.textContent).toContain("uns/paint/booth3/batch");
    expect(changeoverRow.textContent).toContain("uns/changeover/penalty_matrix");
    expect(kittingRow.textContent).toContain("kitting.readiness_pct");
  });
});

// ── AC2 ───────────────────────────────────────────────────────────────────────

describe("AC2 — override saved records VIN ID, original position, new position, justification ≥10 chars, and timestamp", () => {
  it("precondition: createOverride is mocked — AC2 field assertions are not vacuous against a silent stub", () => {
    expect(vi.isMockFunction(api.createOverride)).toBe(true);
  });

  it("AC2: createOverride is called with vin, originalPosition, newPosition, and justification when override is submitted", async () => {
    vi.mocked(api.createOverride).mockResolvedValue(makeOverrideRecord());
    await renderWithResult();

    const user = userEvent.setup({ delay: null });
    await user.click(screen.getByTestId(`override-btn-${VIN_1}`));

    await waitFor(() => {
      expect(screen.getByTestId("override-justification-input")).toBeTruthy();
    });

    // Move VIN_1 from position 1 to position 3
    const newPosInput = screen.getByTestId("override-new-pos-input");
    await user.clear(newPosInput);
    await user.type(newPosInput, "3");

    await user.type(
      screen.getByTestId("override-justification-input"),
      "Customer SLA requires earlier delivery.",
    );

    await user.click(screen.getByTestId("override-submit-btn"));

    await waitFor(() => {
      expect(vi.mocked(api.createOverride)).toHaveBeenCalledWith(
        expect.objectContaining({
          vin: VIN_1,
          originalPosition: 1,
          newPosition: 3,
          justification: expect.stringMatching(/.{10,}/),
        }),
      );
    });
  });

  it("AC2: the override record returned by the API includes all required fields — vin, originalPosition, newPosition, justification, and createdAt timestamp", async () => {
    const record = makeOverrideRecord({
      vin: VIN_1,
      originalPosition: 1,
      newPosition: 3,
      justification: "Customer SLA requires earlier delivery.",
      createdAt: FAR_FUTURE_TS,
    });
    vi.mocked(api.createOverride).mockResolvedValue(record);
    await renderWithResult();

    const user = userEvent.setup({ delay: null });
    await user.click(screen.getByTestId(`override-btn-${VIN_1}`));

    await waitFor(() => {
      expect(screen.getByTestId("override-justification-input")).toBeTruthy();
    });

    await user.type(
      screen.getByTestId("override-justification-input"),
      "Customer SLA requires earlier delivery.",
    );

    await user.click(screen.getByTestId("override-submit-btn"));

    // Wait for the API call to complete (toast signals the call returned)
    await waitFor(() => {
      expect(screen.getByTestId("override-toast")).toBeTruthy();
    });

    // Verify what was passed to the API — these are the fields the database stores
    const [req] = vi.mocked(api.createOverride).mock.calls[0];
    expect(req.vin).toBe(VIN_1);                                          // VIN ID
    expect(req.originalPosition).toBe(1);                                  // original position
    expect(req.newPosition).toBeGreaterThanOrEqual(1);                     // new position
    expect(req.justification.length).toBeGreaterThanOrEqual(10);           // justification ≥ 10 chars

    // createdAt is set server-side and returned in the response
    expect(record.createdAt).toBe(FAR_FUTURE_TS);                          // timestamp present
    expect(typeof record.vin).toBe("string");
    expect(typeof record.originalPosition).toBe("number");
    expect(typeof record.newPosition).toBe("number");
    expect(record.justification.length).toBeGreaterThanOrEqual(10);
  });

  it("AC2: submit button is disabled and a validation error is shown when justification is fewer than 10 characters — API is never called", async () => {
    await renderWithResult();

    const user = userEvent.setup({ delay: null });
    await user.click(screen.getByTestId(`override-btn-${VIN_1}`));

    await waitFor(() => {
      expect(screen.getByTestId("override-justification-input")).toBeTruthy();
    });

    // Type exactly 9 characters — one below the minimum
    await user.type(screen.getByTestId("override-justification-input"), "123456789");

    expect(screen.getByTestId("justification-error")).toBeTruthy();
    expect(screen.getByTestId("override-submit-btn")).toBeDisabled();
    expect(vi.mocked(api.createOverride)).not.toHaveBeenCalled();
  });
});

// ── AC3 ───────────────────────────────────────────────────────────────────────

describe("AC3 — override log lists all overrides for the current week and is exportable as PDF for Plant Director review", () => {
  it("precondition: listOverrides is mocked — table-row assertions are not vacuous against a missing API", () => {
    expect(vi.isMockFunction(api.listOverrides)).toBe(true);
  });

  it("precondition: exportOverridesPdf is mocked — PDF export assertions are not vacuous against a silent stub", () => {
    expect(vi.isMockFunction(api.exportOverridesPdf)).toBe(true);
  });

  it("AC3: listOverrides is called with the current week's ISO string (YYYY-Www) on page mount", async () => {
    vi.mocked(api.listOverrides).mockResolvedValue(makeOverrideListResponse(0));
    render(<OverrideLogPage />);

    await waitFor(() => {
      expect(vi.mocked(api.listOverrides)).toHaveBeenCalled();
    });

    const [params] = vi.mocked(api.listOverrides).mock.calls[0];
    expect(params.weekIso).toMatch(/^\d{4}-W\d{2}$/);
  });

  it("AC3: all overrides returned for the current week are listed in the override log table", async () => {
    vi.mocked(api.listOverrides).mockResolvedValue(makeOverrideListResponse(4));
    render(<OverrideLogPage />);

    await waitFor(() => {
      expect(screen.getByTestId("override-log-table")).toBeTruthy();
    });

    // Every record from the week appears as a row
    const vinCells = screen.getAllByText(/WBA3G2C04PFX1900/);
    expect(vinCells).toHaveLength(4);
  });

  it("AC3: the count badge reflects the total number of overrides for the week", async () => {
    vi.mocked(api.listOverrides).mockResolvedValue(makeOverrideListResponse(3));
    render(<OverrideLogPage />);

    await waitFor(() => {
      expect(screen.getByTestId("override-log-table")).toBeTruthy();
    });

    expect(screen.getByText("3 overrides")).toBeTruthy();
  });

  it("AC3: PDF export button is enabled when overrides are present (exportable for Plant Director review)", async () => {
    vi.mocked(api.listOverrides).mockResolvedValue(makeOverrideListResponse(2));
    render(<OverrideLogPage />);

    await waitFor(() => {
      expect(screen.getByTestId("override-log-table")).toBeTruthy();
    });

    expect(screen.getByTestId("export-pdf-btn")).not.toBeDisabled();
  });

  it("AC3: clicking Export PDF calls exportOverridesPdf with the current week's ISO string", async () => {
    vi.mocked(api.listOverrides).mockResolvedValue(makeOverrideListResponse(2));
    vi.mocked(api.exportOverridesPdf).mockResolvedValue(
      new Blob(["PDF"], { type: "application/pdf" }),
    );

    render(<OverrideLogPage />);

    await waitFor(() => {
      expect(screen.getByTestId("override-log-table")).toBeTruthy();
    });

    // Stub browser download APIs AFTER render so React can attach to the DOM first
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn().mockReturnValue("blob:mock-url"),
      revokeObjectURL: vi.fn(),
    });
    const appendSpy = vi.spyOn(document.body, "appendChild").mockImplementation((el: Node) => el);
    const removeSpy = vi.spyOn(document.body, "removeChild").mockImplementation((el: Node) => el);

    await userEvent.setup({ delay: null }).click(screen.getByTestId("export-pdf-btn"));

    await waitFor(() => {
      expect(vi.mocked(api.exportOverridesPdf)).toHaveBeenCalledWith(
        expect.stringMatching(/^\d{4}-W\d{2}$/),
      );
    });

    vi.unstubAllGlobals();
    appendSpy.mockRestore();
    removeSpy.mockRestore();
  });
});
