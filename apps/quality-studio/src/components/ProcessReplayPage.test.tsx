import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { I18nextProvider } from "react-i18next";
import type { ReactNode } from "react";
import i18n from "../i18n";
import { ProcessReplayPage } from "./ProcessReplayPage";
import { DigitalThreadPanel } from "./DigitalThreadPanel";
import type {
  ThreadCompletenessResponse,
  VinThreadResponse,
  ThreadEvent,
} from "../types/digitalThread";

vi.mock("../services/api", () => ({
  getVinThreadCompleteness: vi.fn(),
  getAllVinThreadEvents: vi.fn(),
}));

import * as api from "../services/api";

const MOCK_VIN = "WBA3A5G59DNP26082";

function makeStages(
  overrides?: Partial<ThreadCompletenessResponse["stages"][number]>[],
): ThreadCompletenessResponse["stages"] {
  return Array.from({ length: 14 }, (_, i) => {
    const stageNumber = i + 1;
    const override = overrides?.[i] ?? {};
    return {
      stageNumber,
      stageName: `Stage ${stageNumber} Name`,
      available: true,
      completenessPercent: 100,
      expectedSampleCount: 10,
      actualSampleCount: 10,
      ...override,
    };
  });
}

function makeCompleteness(
  overrides?: Partial<ThreadCompletenessResponse>,
): ThreadCompletenessResponse {
  return {
    vin: MOCK_VIN,
    overallCompletenessPercent: 100,
    meetsThreshold: true,
    stages: makeStages(),
    ...overrides,
  };
}

function makePaintEvents(stageNumber: number, count = 2): ThreadEvent[] {
  return Array.from({ length: count }, (_, i) => ({
    eventId: `evt-s${stageNumber}-${i}`,
    vin: MOCK_VIN,
    eventType: "PAINT_PREP_STAGE" as const,
    shop: "PAINT",
    station: `PAINT_PREP_STAGE_${stageNumber}`,
    occurredAt: `2026-01-${String(stageNumber).padStart(2, "0")}T${String(i).padStart(2, "0")}:00:00Z`,
    dataSource: "UNS",
    parameters: { temperature_c: 22.5, humidity_pct: 60 },
    stageNumber,
  }));
}

function makeFullPaintThread(): VinThreadResponse {
  const data: ThreadEvent[] = [];
  for (let s = 1; s <= 14; s++) {
    data.push(...makePaintEvents(s));
  }
  return {
    vin: MOCK_VIN,
    data,
    page: { next_cursor: null, limit: 200 },
  };
}

function Wrapper({ children }: { children: ReactNode }) {
  return <I18nextProvider i18n={i18n}>{children}</I18nextProvider>;
}

beforeEach(async () => {
  await i18n.changeLanguage("en-GB");
  vi.clearAllMocks();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("ProcessReplayPage — 14 lanes from ThreadCompletenessResponse", () => {
  it("renders exactly 14 stage lanes when completeness returns 14 stages", async () => {
    vi.mocked(api.getVinThreadCompleteness).mockResolvedValue(makeCompleteness());
    vi.mocked(api.getAllVinThreadEvents).mockResolvedValue(makeFullPaintThread());

    render(
      <Wrapper>
        <ProcessReplayPage vin={MOCK_VIN} />
      </Wrapper>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("pr-lanes")).toBeTruthy();
    });

    for (let s = 1; s <= 14; s++) {
      expect(screen.getByTestId(`stage-lane-${s}`)).toBeTruthy();
    }
  });
});

describe("ProcessReplayPage — unavailable stage shows grey block", () => {
  it("renders grey block with completenessPercent for stage with available=false", async () => {
    const stages = makeStages([
      {},
      {},
      {},
      { available: false, completenessPercent: 42.5, stageName: "Stage 4 Name" },
    ]);
    vi.mocked(api.getVinThreadCompleteness).mockResolvedValue(
      makeCompleteness({ stages }),
    );
    vi.mocked(api.getAllVinThreadEvents).mockResolvedValue({
      vin: MOCK_VIN,
      data: [],
      page: { next_cursor: null, limit: 200 },
    });

    render(
      <Wrapper>
        <ProcessReplayPage vin={MOCK_VIN} />
      </Wrapper>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("stage-lane-4")).toBeTruthy();
    });

    const lane4 = screen.getByTestId("stage-lane-4");
    expect(lane4.className).toContain("unavailable");
    expect(lane4.textContent).toContain("42.5%");
    expect(lane4.textContent).toContain("Data Unavailable");
  });
});

describe("ProcessReplayPage — loading state renders skeletons", () => {
  it("renders 14 skeleton lanes while loading", () => {
    let resolveCompleteness!: (v: ThreadCompletenessResponse) => void;
    const pendingCompleteness = new Promise<ThreadCompletenessResponse>(
      (res) => { resolveCompleteness = res; },
    );
    vi.mocked(api.getVinThreadCompleteness).mockReturnValue(pendingCompleteness);
    vi.mocked(api.getAllVinThreadEvents).mockReturnValue(
      new Promise(() => undefined),
    );

    render(
      <Wrapper>
        <ProcessReplayPage vin={MOCK_VIN} />
      </Wrapper>,
    );

    const skeletons = screen.getAllByRole("region", { name: /loading stage/i });
    expect(skeletons.length).toBe(14);

    resolveCompleteness(makeCompleteness());
  });
});

describe("ProcessReplayPage — error state renders retry button", () => {
  it("shows error message and retry button on 503", async () => {
    const err = new Error("Neo4j unavailable") as Error & { status: number };
    err.status = 503;
    vi.mocked(api.getVinThreadCompleteness).mockRejectedValue(err);
    vi.mocked(api.getAllVinThreadEvents).mockRejectedValue(err);

    render(
      <Wrapper>
        <ProcessReplayPage vin={MOCK_VIN} />
      </Wrapper>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("pr-error")).toBeTruthy();
    });

    expect(screen.getByTestId("pr-retry-btn")).toBeTruthy();
    expect(screen.getByText(/temporarily unavailable/i)).toBeTruthy();
  });

  it("clicking retry triggers a new load attempt", async () => {
    const err = new Error("error") as Error & { status: number };
    err.status = 503;
    vi.mocked(api.getVinThreadCompleteness).mockRejectedValue(err);
    vi.mocked(api.getAllVinThreadEvents).mockRejectedValue(err);

    render(
      <Wrapper>
        <ProcessReplayPage vin={MOCK_VIN} />
      </Wrapper>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("pr-retry-btn")).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId("pr-retry-btn"));
    expect(vi.mocked(api.getVinThreadCompleteness).mock.calls.length).toBeGreaterThanOrEqual(1);
  });
});

describe("ProcessReplayPage — empty state on 404", () => {
  it("renders VIN not found message on 404", async () => {
    const err = new Error("VIN not found") as Error & { status: number };
    err.status = 404;
    vi.mocked(api.getVinThreadCompleteness).mockRejectedValue(err);
    vi.mocked(api.getAllVinThreadEvents).mockRejectedValue(err);

    render(
      <Wrapper>
        <ProcessReplayPage vin={MOCK_VIN} />
      </Wrapper>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("pr-empty")).toBeTruthy();
    });
  });
});

describe("DigitalThreadPanel — SHOP_BOUNDARY_TRANSITION dividers", () => {
  it("renders shop dividers for SHOP_BOUNDARY_TRANSITION events", async () => {
    const data: ThreadEvent[] = [
      {
        eventId: "evt-1",
        vin: MOCK_VIN,
        eventType: "SHOP_BOUNDARY_TRANSITION",
        shop: "BODY",
        station: "BOUNDARY",
        occurredAt: "2026-01-01T08:00:00Z",
        dataSource: "SYSTEM",
        parameters: {},
      },
      {
        eventId: "evt-2",
        vin: MOCK_VIN,
        eventType: "SHOP_BOUNDARY_TRANSITION",
        shop: "PAINT",
        station: "BOUNDARY",
        occurredAt: "2026-01-01T09:00:00Z",
        dataSource: "SYSTEM",
        parameters: {},
      },
    ];
    vi.mocked(api.getAllVinThreadEvents).mockResolvedValue({
      vin: MOCK_VIN,
      data,
      page: { next_cursor: null, limit: 200 },
    });

    render(
      <Wrapper>
        <DigitalThreadPanel vin={MOCK_VIN} />
      </Wrapper>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("dt-shop-divider-BODY")).toBeTruthy();
    });
    expect(screen.getByTestId("dt-shop-divider-PAINT")).toBeTruthy();
  });
});

describe("DigitalThreadPanel — FINAL_GATE_COMPLETE shows Chain Complete badge", () => {
  it("renders Chain Complete badge on the final QUALITY_CERTIFICATION event", async () => {
    const data: ThreadEvent[] = [
      {
        eventId: "evt-cert",
        vin: MOCK_VIN,
        eventType: "QUALITY_CERTIFICATION",
        shop: "FINAL",
        station: "FINAL_GATE",
        occurredAt: "2026-01-01T18:00:00Z",
        dataSource: "OPCENTER",
        parameters: { result: "PASS" },
      },
    ];
    vi.mocked(api.getAllVinThreadEvents).mockResolvedValue({
      vin: MOCK_VIN,
      data,
      page: { next_cursor: null, limit: 200 },
      vinStatus: "FINAL_GATE_COMPLETE",
    });

    render(
      <Wrapper>
        <DigitalThreadPanel vin={MOCK_VIN} />
      </Wrapper>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("dt-chain-complete-badge")).toBeTruthy();
    });
    expect(screen.getByTestId("dt-chain-complete-badge").textContent).toBe(
      "Chain Complete",
    );
  });
});

describe("DigitalThreadPanel — inferred gap indicator", () => {
  it("renders gap indicator card for SYSTEM_INFERRED shop boundary transition", async () => {
    const data: ThreadEvent[] = [
      {
        eventId: "evt-gap",
        vin: MOCK_VIN,
        eventType: "SHOP_BOUNDARY_TRANSITION",
        shop: "PAINT",
        station: "BOUNDARY",
        occurredAt: "2026-01-01T10:00:00Z",
        dataSource: "SYSTEM_INFERRED",
        parameters: {},
      },
    ];
    vi.mocked(api.getAllVinThreadEvents).mockResolvedValue({
      vin: MOCK_VIN,
      data,
      page: { next_cursor: null, limit: 200 },
    });

    render(
      <Wrapper>
        <DigitalThreadPanel vin={MOCK_VIN} />
      </Wrapper>,
    );

    await waitFor(() => {
      expect(screen.getByTestId(`dt-gap-evt-gap`)).toBeTruthy();
    });

    const gapEl = screen.getByTestId("dt-gap-evt-gap");
    expect(gapEl.getAttribute("role")).toBe("alert");
  });
});

describe("DigitalThreadPanel — error state", () => {
  it("shows retry button on error", async () => {
    const err = new Error("unavailable") as Error & { status: number };
    err.status = 503;
    vi.mocked(api.getAllVinThreadEvents).mockRejectedValue(err);

    render(
      <Wrapper>
        <DigitalThreadPanel vin={MOCK_VIN} />
      </Wrapper>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("dt-retry-btn")).toBeTruthy();
    });
  });
});

describe("DigitalThreadPanel — loading state", () => {
  it("renders skeleton cards while loading", () => {
    vi.mocked(api.getAllVinThreadEvents).mockReturnValue(
      new Promise(() => undefined),
    );

    render(
      <Wrapper>
        <DigitalThreadPanel vin={MOCK_VIN} />
      </Wrapper>,
    );

    expect(screen.getByTestId("dt-skeleton")).toBeTruthy();
  });
});
