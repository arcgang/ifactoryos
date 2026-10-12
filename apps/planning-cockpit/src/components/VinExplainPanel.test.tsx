import "../i18n";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { VinExplainPanel } from "./VinExplainPanel";
import type { VinExplanation } from "../types/overrides";

const RUN_ID = "00000000-0000-0000-0000-aabbccddeeff";
const VIN = "WBY31CF080NCH00001";

function makeExplanation(overrides?: Partial<VinExplanation>): VinExplanation {
  return {
    runId: RUN_ID,
    vin: VIN,
    position: 3,
    topFactors: [
      {
        factorName: "paint_batch_conflict",
        weightedScore: 0.72,
        dataSource: "uns/paint/booth3/batch",
        rawValue: 1.0,
      },
      {
        factorName: "kitting_gap",
        weightedScore: 0.45,
        dataSource: "kitting.readiness_pct",
        rawValue: 78.5,
      },
      {
        factorName: "maintenance_window",
        weightedScore: 0.31,
        dataSource: "uns/maintenance/windows",
        rawValue: null,
      },
    ],
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("VinExplainPanel — loading state", () => {
  it("shows loading skeleton while fetch is in flight", () => {
    vi.spyOn(globalThis, "fetch").mockReturnValue(new Promise(() => {}));

    render(
      <VinExplainPanel
        runId={RUN_ID}
        vin={VIN}
        position={3}
        onClose={() => {}}
      />,
    );

    expect(screen.getByTestId("explain-loading")).toBeTruthy();
  });
});

describe("VinExplainPanel — populated", () => {
  it("renders three factor rows with weighted score and data source after load", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(makeExplanation()),
      text: () => Promise.resolve(""),
    } as Response);

    render(
      <VinExplainPanel
        runId={RUN_ID}
        vin={VIN}
        position={3}
        onClose={() => {}}
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId("explain-factor-paint_batch_conflict")).toBeTruthy();
    });

    // All three factor rows
    expect(screen.getByTestId("explain-factor-paint_batch_conflict")).toBeTruthy();
    expect(screen.getByTestId("explain-factor-kitting_gap")).toBeTruthy();
    expect(screen.getByTestId("explain-factor-maintenance_window")).toBeTruthy();

    // Weighted scores shown to 2 decimal places
    expect(screen.getByText("0.72")).toBeTruthy();
    expect(screen.getByText("0.45")).toBeTruthy();
    expect(screen.getByText("0.31")).toBeTruthy();

    // Data source strings
    expect(screen.getByText("uns/paint/booth3/batch")).toBeTruthy();
    expect(screen.getByText("kitting.readiness_pct")).toBeTruthy();
    expect(screen.getByText("uns/maintenance/windows")).toBeTruthy();
  });

  it("shows localised factor names in English", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(makeExplanation()),
      text: () => Promise.resolve(""),
    } as Response);

    render(
      <VinExplainPanel
        runId={RUN_ID}
        vin={VIN}
        position={3}
        onClose={() => {}}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("Paint Batch Conflict")).toBeTruthy();
    });

    expect(screen.getByText("Kitting Gap")).toBeTruthy();
    expect(screen.getByText("Maintenance Window")).toBeTruthy();
  });
});

describe("VinExplainPanel — error state", () => {
  it("shows inline error on API failure without blocking panel", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: false,
      status: 404,
      json: () => Promise.resolve({ code: "NOT_FOUND", message: "Run or VIN not found", correlation_id: "abc" }),
      text: () => Promise.resolve("Run or VIN not found"),
    } as Response);

    render(
      <VinExplainPanel
        runId={RUN_ID}
        vin={VIN}
        position={3}
        onClose={() => {}}
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId("explain-error")).toBeTruthy();
    });

    // Panel is still rendered (not blocking)
    expect(screen.queryByTestId("explain-loading")).toBeNull();
  });

  it("shows error message from API response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: false,
      status: 500,
      json: () => Promise.resolve({}),
      text: () => Promise.resolve("Internal server error"),
    } as Response);

    render(
      <VinExplainPanel
        runId={RUN_ID}
        vin={VIN}
        position={3}
        onClose={() => {}}
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId("explain-error")).toBeTruthy();
    });

    expect(screen.getByTestId("explain-error").textContent).toMatch(/internal server error/i);
  });
});
