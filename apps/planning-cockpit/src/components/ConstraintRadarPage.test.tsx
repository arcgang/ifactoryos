import "../i18n";
import { render, screen, act, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ConstraintRadarPage } from "./ConstraintRadarPage";
import type { LiveConstraintTelemetry, ThresholdListResponse } from "../types/constraints";

// ── EventSource mock ──────────────────────────────────────────────────────────

type ESCallback = (evt: Event) => void;

class FakeEventSource {
  private readonly _listeners = new Map<string, ESCallback[]>();
  readonly url: string;

  static last: FakeEventSource | null = null;

  constructor(url: string) {
    this.url = url;
    FakeEventSource.last = this;
  }

  addEventListener(type: string, cb: ESCallback): void {
    const arr = this._listeners.get(type) ?? [];
    arr.push(cb);
    this._listeners.set(type, arr);
  }

  close(): void {}

  emit(type: string, data: string): void {
    const cbs = this._listeners.get(type) ?? [];
    const evt = new MessageEvent(type, { data });
    for (const cb of cbs) cb(evt);
  }
}

// ── Threshold fixtures ────────────────────────────────────────────────────────

const PAINT_THRESHOLD_ID  = "00000000-0000-0000-0000-000000000001";
const BODY_THRESHOLD_ID   = "00000000-0000-0000-0000-000000000002";
const KIT_THRESHOLD_ID    = "00000000-0000-0000-0000-000000000003";
const MAINT_THRESHOLD_ID  = "00000000-0000-0000-0000-000000000004";

const MOCK_THRESHOLDS: ThresholdListResponse = {
  data: [
    {
      thresholdId: PAINT_THRESHOLD_ID,
      constraintType: "paint_capacity",
      warningLevel: 85,
      criticalLevel: 95,
      unit: "%",
      updatedAt: null,
      updatedBy: null,
    },
    {
      thresholdId: BODY_THRESHOLD_ID,
      constraintType: "body_wip_queue",
      warningLevel: 160,
      criticalLevel: 190,
      unit: "units",
      updatedAt: null,
      updatedBy: null,
    },
    {
      thresholdId: KIT_THRESHOLD_ID,
      constraintType: "kitting_readiness",
      warningLevel: 88,
      criticalLevel: 75,
      unit: "%",
      updatedAt: null,
      updatedBy: null,
    },
    {
      thresholdId: MAINT_THRESHOLD_ID,
      constraintType: "maintenance_window",
      warningLevel: 6,
      criticalLevel: 10,
      unit: "windows",
      updatedAt: null,
      updatedBy: null,
    },
  ],
  page: { next_cursor: null, limit: 50 },
};

// ── Helpers ───────────────────────────────────────────────────────────────────

/**
 * Build a serialised LiveConstraintTelemetry SSE payload.
 * measuredAt is expressed as an offset from Date.now() so no absolute timestamp is hardcoded.
 */
function makeTelemetry(
  constraintType: LiveConstraintTelemetry["constraintType"],
  currentValue: number,
  lagMs = 100,
): string {
  const now = Date.now();
  const measuredAt = new Date(now - lagMs).toISOString();
  const receivedAt = new Date(now).toISOString();
  const msg: LiveConstraintTelemetry = {
    constraintType,
    currentValue,
    unit: "%",
    measuredAt,
    receivedAt,
  };
  return JSON.stringify(msg);
}

// ── Setup ─────────────────────────────────────────────────────────────────────

beforeEach(() => {
  FakeEventSource.last = null;
  (globalThis as unknown as Record<string, unknown>).EventSource = FakeEventSource;

  vi.spyOn(globalThis, "fetch").mockResolvedValue({
    ok: true,
    status: 200,
    json: () => Promise.resolve(MOCK_THRESHOLDS),
    text: () => Promise.resolve(""),
  } as Response);
});

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("FakeEventSource instance isolation", () => {
  it("each new instance starts with an empty listener map", () => {
    const a = new FakeEventSource("/a");
    a.addEventListener("telemetry", () => {});
    const b = new FakeEventSource("/b");
    // b must have no listeners inherited from a
    expect((b as unknown as { _listeners: Map<string, unknown[]> })["_listeners"].size).toBe(0);
  });
});

describe("ConstraintRadarPage", () => {
  it("renders all four constraint tiles after thresholds load", async () => {
    render(<ConstraintRadarPage />);

    await waitFor(() => {
      expect(screen.getByLabelText(/paint capacity/i)).toBeTruthy();
      expect(screen.getByLabelText(/body wip queue/i)).toBeTruthy();
      expect(screen.getByLabelText(/kitting readiness/i)).toBeTruthy();
      expect(screen.getByLabelText(/maintenance windows/i)).toBeTruthy();
    });
  });

  it("applies amber CSS class when telemetry value 90 exceeds warning threshold 85", async () => {
    render(<ConstraintRadarPage />);
    await waitFor(() => screen.getByLabelText(/paint capacity/i));

    act(() => {
      FakeEventSource.last?.emit("telemetry", makeTelemetry("paint_capacity", 90, 50));
    });

    await waitFor(() => {
      expect(screen.getByLabelText(/paint capacity/i).className).toContain("amber");
    });
  });

  it("removes amber CSS class when telemetry value 70 drops below warning threshold 85", async () => {
    render(<ConstraintRadarPage />);
    await waitFor(() => screen.getByLabelText(/paint capacity/i));

    act(() => {
      FakeEventSource.last?.emit("telemetry", makeTelemetry("paint_capacity", 90, 50));
    });
    await waitFor(() =>
      expect(screen.getByLabelText(/paint capacity/i).className).toContain("amber"),
    );

    act(() => {
      FakeEventSource.last?.emit("telemetry", makeTelemetry("paint_capacity", 70, 50));
    });
    await waitFor(() =>
      expect(screen.getByLabelText(/paint capacity/i).className).not.toContain("amber"),
    );
  });

  it("shows staleness CSS class when 50 messages with lag 1500 ms push p99 above 1000 ms", async () => {
    render(<ConstraintRadarPage />);
    await waitFor(() => screen.getByLabelText(/paint capacity/i));

    act(() => {
      for (let i = 0; i < 50; i++) {
        FakeEventSource.last?.emit("telemetry", makeTelemetry("paint_capacity", 78, 1500));
      }
    });

    await waitFor(() => {
      expect(screen.getByLabelText(/paint capacity/i).className).toContain("stale");
    });
  });

  it("renders a HH:MM:SS timestamp on the paint capacity tile after receiving a telemetry message", async () => {
    render(<ConstraintRadarPage />);
    await waitFor(() => screen.getByLabelText(/paint capacity/i));

    act(() => {
      FakeEventSource.last?.emit("telemetry", makeTelemetry("paint_capacity", 78, 50));
    });

    await waitFor(() => {
      const tile = screen.getByLabelText(/paint capacity/i);
      expect(tile.textContent).toMatch(/\d{2}:\d{2}:\d{2}/);
    });
  });

  it("renders an error alert and retry button when the threshold fetch fails", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("Network error"));

    render(<ConstraintRadarPage />);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeTruthy();
      expect(screen.getByRole("button", { name: /retry/i })).toBeTruthy();
    });
  });
});
