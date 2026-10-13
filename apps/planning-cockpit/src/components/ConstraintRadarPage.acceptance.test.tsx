/**
 * Acceptance tests for US-001 — Constraint Radar
 *
 * Tests the seam between the sequence-optimizer threshold API (task 1)
 * and the planning-cockpit rendering layer (task 2). Each test is named
 * after the criterion it exercises.
 *
 * AC1 – Four constraint tiles displayed with data timestamps ≤60 s from UNS
 * AC2 – Amber highlight with current value and warning threshold side by side
 * AC3 – Staleness indicator with last-known-good timestamp when p99 lag > 1 s
 */

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

// Warning levels: paint_capacity=85, body_wip_queue=160, kitting_readiness=88, maintenance_window=6
const MOCK_THRESHOLDS: ThresholdListResponse = {
  data: [
    {
      thresholdId: "00000000-0000-0000-0000-000000000001",
      constraintType: "paint_capacity",
      warningLevel: 85,
      criticalLevel: 95,
      unit: "%",
      updatedAt: null,
      updatedBy: null,
    },
    {
      thresholdId: "00000000-0000-0000-0000-000000000002",
      constraintType: "body_wip_queue",
      warningLevel: 160,
      criticalLevel: 190,
      unit: "units",
      updatedAt: null,
      updatedBy: null,
    },
    {
      thresholdId: "00000000-0000-0000-0000-000000000003",
      constraintType: "kitting_readiness",
      warningLevel: 88,
      criticalLevel: 75,
      unit: "%",
      updatedAt: null,
      updatedBy: null,
    },
    {
      thresholdId: "00000000-0000-0000-0000-000000000004",
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
 * measuredAt = now − lagMs, receivedAt = now.
 * ageMs = lagMs, so for lagMs < 60 000 the hook records lastKnownGoodAt.
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

// ── Acceptance tests ──────────────────────────────────────────────────────────

describe("US-001 Constraint Radar — acceptance criteria", () => {
  /**
   * AC1: paint capacity utilisation, body WIP queue depth, kitting readiness,
   * and maintenance windows are all displayed with data timestamps no older
   * than 60 seconds from the UNS.
   *
   * Each telemetry message has lagMs=50 (ageMs < 60 000), so every tile
   * receives fresh data and must render a HH:MM:SS data timestamp.
   */
  it("AC1: all four constraint tiles show a HH:MM:SS data timestamp after receiving fresh telemetry within 60 seconds", async () => {
    render(<ConstraintRadarPage />);

    // Precondition: thresholds must have loaded so tiles are present
    await waitFor(() => {
      expect(screen.queryByLabelText(/paint capacity/i)).not.toBeNull();
    });

    // Send one fresh telemetry message (age=50 ms < 60 000 ms) per tile type
    act(() => {
      FakeEventSource.last?.emit("telemetry", makeTelemetry("paint_capacity",      78,  50));
      FakeEventSource.last?.emit("telemetry", makeTelemetry("body_wip_queue",     140,  50));
      FakeEventSource.last?.emit("telemetry", makeTelemetry("kitting_readiness",   82,  50));
      FakeEventSource.last?.emit("telemetry", makeTelemetry("maintenance_window",   3,  50));
    });

    // Every tile must show a HH:MM:SS data timestamp sourced from measuredAt
    await waitFor(() => {
      const tileLabels: RegExp[] = [
        /paint capacity/i,
        /body wip queue/i,
        /kitting readiness/i,
        /maintenance windows/i,
      ];
      for (const label of tileLabels) {
        const tile = screen.getByLabelText(label);
        expect(
          tile.textContent,
          `Tile "${String(label)}" must show a HH:MM:SS timestamp from UNS telemetry`,
        ).toMatch(/\d{2}:\d{2}:\d{2}/);
      }
    });
  });

  /**
   * AC2: when paint_capacity current value 90 crosses the configurable warning
   * threshold 85, the tile is highlighted in amber AND displays the current
   * value (90) and the warning threshold (85) side by side.
   *
   * The threshold value is fetched from the sequence-optimizer API (mocked as
   * MOCK_THRESHOLDS above) and injected into the tile as a prop. This test
   * exercises the seam between the threshold API response and the amber display.
   */
  it("AC2: paint_capacity tile in amber state displays current value 90 and warning level 85 side by side", async () => {
    render(<ConstraintRadarPage />);

    // Precondition: thresholds must have loaded (warningLevel=85 for paint_capacity)
    await waitFor(() => {
      expect(screen.queryByLabelText(/paint capacity/i)).not.toBeNull();
    });

    act(() => {
      // value 90 > warningLevel 85 → breach
      FakeEventSource.last?.emit("telemetry", makeTelemetry("paint_capacity", 90, 50));
    });

    await waitFor(() => {
      const tile = screen.getByLabelText(/paint capacity/i);

      // Tile must carry the amber CSS class
      expect(tile.className).toContain("amber");

      // The threshold row (role="status") must be rendered with both values
      const thresholdRow = tile.querySelector('[role="status"]');
      expect(thresholdRow, "threshold row with role=status must be present when amber").not.toBeNull();
      expect(thresholdRow!.textContent).toContain("Current");
      expect(thresholdRow!.textContent).toContain("90");   // current value displayed
      expect(thresholdRow!.textContent).toContain("Warning");
      expect(thresholdRow!.textContent).toContain("85");   // warning level displayed alongside
    });
  });

  /**
   * AC3: when UNS latency exceeds 1 000 ms p99, a staleness indicator is shown
   * on the affected tile AND includes the last-known-good HH:MM:SS timestamp
   * rather than the "—" placeholder.
   *
   * Each message has lagMs=1 500 (lag > 1 000 → stale after 50 samples) and
   * ageMs=1 500 (< 60 000 → lastKnownGoodAt is populated from measuredAt).
   */
  it("AC3: paint_capacity staleness indicator shows last-known-good HH:MM:SS timestamp when p99 lag exceeds 1000ms", async () => {
    render(<ConstraintRadarPage />);

    // Precondition: thresholds must have loaded so tiles are present
    await waitFor(() => {
      expect(screen.queryByLabelText(/paint capacity/i)).not.toBeNull();
    });

    act(() => {
      // 50 messages with lag=1 500 ms pushes the rolling p99 well above 1 000 ms.
      // ageMs=1 500 ms < 60 000 ms, so lastKnownGoodAt is recorded for each.
      for (let i = 0; i < 50; i++) {
        FakeEventSource.last?.emit("telemetry", makeTelemetry("paint_capacity", 78, 1_500));
      }
    });

    await waitFor(() => {
      const tile = screen.getByLabelText(/paint capacity/i);

      // Tile must carry the stale CSS class
      expect(tile.className).toContain("stale");

      // "Last good: HH:MM:SS" must be rendered with a real timestamp, not "—"
      expect(
        tile.textContent,
        "staleness row must show last-known-good time, not placeholder dash",
      ).toMatch(/Last good:\s*\d{2}:\d{2}:\d{2}/);
      expect(tile.textContent).not.toMatch(/Last good:\s*—/);
    });
  });
});
