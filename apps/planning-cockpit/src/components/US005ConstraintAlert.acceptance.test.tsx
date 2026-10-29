/**
 * Acceptance tests for US-005 — Mid-week Constraint Radar alert + targeted re-sequence
 *
 * Exercises the seam between:
 *   Task 1 (backend): breach consumer persists alert → GET /alerts endpoint returns it
 *   Task 2 (frontend): ConstraintRadarPage polls /alerts → ConstraintAlertPanel renders it
 *                      → user triggers targeted re-sequence → publishes to OpCenter
 *
 * AC1: In-app alert surfaces in Constraint Radar after threshold breach, identifying
 *      affected VINs and the constraint causing the breach
 * AC2: Re-sequence Affected VINs pre-loads only the alert's VINs for a targeted run,
 *      not the full weekly sequence
 * AC3: Published re-sequence is attributed to 'planning-cockpit' in the audit log
 */

import "../i18n";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ConstraintRadarPage } from "./ConstraintRadarPage";
import type { AlertListResponse } from "../types/alerts";
import type { ThresholdListResponse } from "../types/constraints";
import type { OptimizeResponse } from "../types/optimizer";

// ── Fixtures ──────────────────────────────────────────────────────────────────

const ALERT_ID = "00000000-0000-0000-0005-000000000001";
const RUN_ID   = "00000000-0000-0000-0005-000000000002";

// Three affected VINs — the disruption scope after a kitting readiness breach
const AFFECTED_VINS = [
  "WBS3C4C08RC210447",
  "WBS3C4C09RC210448",
  "WBS3C4C07RC210449",
];

const BREACH_ALERT: AlertListResponse["data"][number] = {
  alertId:        ALERT_ID,
  constraintType: "kitting_readiness",
  currentValue:   72.3,
  thresholdValue: 88,
  affectedVins:   AFFECTED_VINS,
  status:         "active",
  detectedAt:     "2099-01-01T10:00:00Z",
};

const MOCK_THRESHOLDS: ThresholdListResponse = {
  data: [
    {
      thresholdId:    "00000000-0000-0000-0000-000000000001",
      constraintType: "paint_capacity",
      warningLevel:   85,
      criticalLevel:  95,
      unit:           "%",
      updatedAt:      null,
      updatedBy:      null,
    },
    {
      thresholdId:    "00000000-0000-0000-0000-000000000002",
      constraintType: "body_wip_queue",
      warningLevel:   160,
      criticalLevel:  190,
      unit:           "units",
      updatedAt:      null,
      updatedBy:      null,
    },
    {
      thresholdId:    "00000000-0000-0000-0000-000000000003",
      constraintType: "kitting_readiness",
      warningLevel:   88,
      criticalLevel:  75,
      unit:           "%",
      updatedAt:      null,
      updatedBy:      null,
    },
    {
      thresholdId:    "00000000-0000-0000-0000-000000000004",
      constraintType: "maintenance_window",
      warningLevel:   6,
      criticalLevel:  10,
      unit:           "windows",
      updatedAt:      null,
      updatedBy:      null,
    },
  ],
  page: { next_cursor: null, limit: 50 },
};

// rankedSequence is scoped to the 3 alert VINs — not the full weekly sequence
const RESEQ_RESPONSE: OptimizeResponse = {
  runId:                      RUN_ID,
  weekIso:                    "2099-W01",
  resultType:                 "monte_carlo",
  rankedSequence:             AFFECTED_VINS.map((vin, i) => ({
    vin,
    position:        i + 1,
    constraintScore: 0.9 - i * 0.05,
  })),
  completedAt:                "2099-01-01T10:05:00Z",
  durationMs:                 4200,
  confidenceScore:            0.91,
  my26ConfigVersion:          null,
  constraintSnapshotTimestamp: "2099-01-01T10:00:00Z",
};

// ── Helper ────────────────────────────────────────────────────────────────────

function makeApiSpy(alertsResponse: AlertListResponse) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(
    (input: RequestInfo | URL) => {
      const url =
        typeof input === "string"
          ? input
          : input instanceof URL
            ? input.toString()
            : (input as Request).url;

      let body: unknown;
      if (url.includes("/resequence")) {
        body = RESEQ_RESPONSE;
      } else if (url.includes("/alerts")) {
        body = alertsResponse;
      } else {
        // /constraints/thresholds and /sequence/publish both return ok:true
        body = MOCK_THRESHOLDS;
      }

      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(""),
      } as Response);
    },
  );
}

// ── Setup ─────────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks();
});

// ── Acceptance tests ──────────────────────────────────────────────────────────

describe("US-005 — mid-week Constraint Radar alert and targeted re-sequence", () => {

  // ── AC1 ──────────────────────────────────────────────────────────────────

  it(
    "AC1: ConstraintRadarPage polls GET /alerts and renders alert card " +
      "identifying constraint type 'kitting_readiness' and all three affected VINs",
    async () => {
      // Precondition: the fixture we are testing with has the required fields
      expect(BREACH_ALERT.constraintType, "fixture must have constraintType").toBe("kitting_readiness");
      expect(BREACH_ALERT.affectedVins, "fixture must have 3 affected VINs").toHaveLength(3);

      const fetchSpy = makeApiSpy({
        data: [BREACH_ALERT],
        page: { next_cursor: null, limit: 50 },
      });

      render(<ConstraintRadarPage />);

      // Precondition: alert card must appear — proves the polling useEffect fired
      // and passed the response to ConstraintAlertPanel
      await waitFor(() => {
        expect(
          screen.getByTestId(`alert-card-${ALERT_ID}`),
          `alert-card-${ALERT_ID} must be rendered after GET /alerts returns an active alert`,
        ).toBeTruthy();
      });

      // Verify GET /alerts was the source of the data (not a hardcoded render)
      const alertCalls = fetchSpy.mock.calls.filter(([url]) =>
        typeof url === "string" && url.includes("/alerts"),
      );
      expect(alertCalls.length, "GET /alerts must have been called at least once").toBeGreaterThanOrEqual(1);

      const card = screen.getByTestId(`alert-card-${ALERT_ID}`);

      // Must identify the constraint causing the breach (human-readable label)
      expect(card.textContent, "card must show 'Kitting Readiness' constraint label").toMatch(
        /kitting readiness/i,
      );

      // Must identify every affected VIN by value
      for (const vin of AFFECTED_VINS) {
        expect(card.textContent, `card must display affected VIN ${vin}`).toContain(vin);
      }
    },
  );

  it(
    "AC1: alert card shows the current value 72.3 and threshold 88 so the planner " +
      "can see why the threshold was breached",
    async () => {
      // Precondition: fixture values match what we assert below
      expect(BREACH_ALERT.currentValue).toBe(72.3);
      expect(BREACH_ALERT.thresholdValue).toBe(88);

      makeApiSpy({ data: [BREACH_ALERT], page: { next_cursor: null, limit: 50 } });

      render(<ConstraintRadarPage />);

      await waitFor(() => screen.getByTestId(`alert-card-${ALERT_ID}`));

      const card = screen.getByTestId(`alert-card-${ALERT_ID}`);
      expect(card.textContent, "card must display current value 72.3").toContain("72.3");
      expect(card.textContent, "card must display threshold value 88").toContain("88");
    },
  );

  // ── AC2 ──────────────────────────────────────────────────────────────────

  it(
    "AC2: clicking 'Re-sequence Affected VINs' sends only the alert's 3 VINs " +
      "to POST /resequence — not the full weekly sequence",
    async () => {
      // Precondition: AFFECTED_VINS has exactly 3 entries (the disruption scope)
      expect(AFFECTED_VINS).toHaveLength(3);

      const fetchSpy = makeApiSpy({
        data: [BREACH_ALERT],
        page: { next_cursor: null, limit: 50 },
      });

      render(<ConstraintRadarPage />);

      // Precondition: Re-sequence button must be visible (alert card must have loaded)
      await waitFor(() => {
        expect(
          screen.getByTestId(`resequence-btn-${ALERT_ID}`),
          "Re-sequence button must appear once the alert is rendered",
        ).toBeTruthy();
      });

      await userEvent.setup({ delay: null }).click(
        screen.getByTestId(`resequence-btn-${ALERT_ID}`),
      );

      await waitFor(() => {
        const reseqCalls = fetchSpy.mock.calls.filter(([url]) =>
          typeof url === "string" && url.includes("/resequence"),
        );
        expect(reseqCalls.length, "POST /resequence must have been called").toBe(1);
      });

      const reseqCalls = fetchSpy.mock.calls.filter(([url]) =>
        typeof url === "string" && url.includes("/resequence"),
      );
      const bodyStr = reseqCalls[0]![1]!.body as string;
      const body = JSON.parse(bodyStr) as {
        alertId: string;
        affectedVins: Array<{ vin: string }>;
      };

      // Must be scoped to the alert's exact affected VINs — not a larger set
      expect(body.alertId, "request must carry the correct alertId").toBe(ALERT_ID);
      expect(
        body.affectedVins,
        "request must include exactly 3 VINs (the alert's affected set)",
      ).toHaveLength(AFFECTED_VINS.length);
      expect(
        body.affectedVins.map((v) => v.vin),
        "request VINs must exactly match the alert's affectedVins",
      ).toEqual(AFFECTED_VINS);
    },
  );

  it(
    "AC2: Scenario Lab panel renders only the targeted rankedSequence " +
      "(3 VINs from the alert) and is labelled as a targeted run",
    async () => {
      // Precondition: RESEQ_RESPONSE is scoped to the 3 alert VINs
      expect(RESEQ_RESPONSE.rankedSequence).toHaveLength(AFFECTED_VINS.length);
      expect(RESEQ_RESPONSE.rankedSequence.map((r) => r.vin)).toEqual(AFFECTED_VINS);

      makeApiSpy({ data: [BREACH_ALERT], page: { next_cursor: null, limit: 50 } });

      render(<ConstraintRadarPage />);

      await waitFor(() => screen.getByTestId(`resequence-btn-${ALERT_ID}`));
      await userEvent.setup({ delay: null }).click(
        screen.getByTestId(`resequence-btn-${ALERT_ID}`),
      );

      // Precondition: Scenario Lab panel must appear with the targeted result
      await waitFor(() => {
        expect(
          screen.getByTestId("scenario-lab-panel"),
          "scenario-lab-panel must appear after a successful /resequence call",
        ).toBeTruthy();
      });

      const panel = screen.getByTestId("scenario-lab-panel");

      // All 3 targeted VINs must appear in the panel
      for (const vin of AFFECTED_VINS) {
        expect(panel.textContent, `Scenario Lab must display targeted VIN ${vin}`).toContain(
          vin,
        );
      }

      // Panel must be labelled as targeted (distinguishing it from a full weekly run)
      expect(panel.textContent, "panel heading must say 'Targeted Re-sequence'").toMatch(
        /targeted re-sequence/i,
      );
    },
  );

  // ── AC3 ──────────────────────────────────────────────────────────────────

  it(
    "AC3: published re-sequence confirmation toast attributes the operation " +
      "to 'planning-cockpit' in the audit log",
    async () => {
      makeApiSpy({ data: [BREACH_ALERT], page: { next_cursor: null, limit: 50 } });

      render(<ConstraintRadarPage />);

      const user = userEvent.setup({ delay: null });

      await waitFor(() => screen.getByTestId(`resequence-btn-${ALERT_ID}`));
      await user.click(screen.getByTestId(`resequence-btn-${ALERT_ID}`));

      await waitFor(() => screen.getByTestId("scenario-lab-panel"));
      await user.click(screen.getByTestId("publish-resequence-btn"));

      // Precondition: confirmation toast must appear (publish must have succeeded)
      await waitFor(() => {
        expect(
          screen.getByTestId("publish-confirmation-toast"),
          "publish-confirmation-toast must appear after a successful publish",
        ).toBeTruthy();
      });

      const toast = screen.getByTestId("publish-confirmation-toast");

      // Must confirm re-sequence was published (not a generic sequence publish)
      expect(toast.textContent, "toast must confirm re-sequence was published").toMatch(
        /re-sequence published/i,
      );

      // Must attribute the operation to planning-cockpit for OpCenter audit trail
      expect(toast.textContent, "toast must attribute operation to 'planning-cockpit'").toMatch(
        /planning-cockpit/i,
      );
    },
  );

  it(
    "AC3: POST /sequence/publish is called with isResequence: true and the runId " +
      "from the targeted re-sequence result",
    async () => {
      const fetchSpy = makeApiSpy({
        data: [BREACH_ALERT],
        page: { next_cursor: null, limit: 50 },
      });

      render(<ConstraintRadarPage />);

      const user = userEvent.setup({ delay: null });

      await waitFor(() => screen.getByTestId(`resequence-btn-${ALERT_ID}`));
      await user.click(screen.getByTestId(`resequence-btn-${ALERT_ID}`));

      await waitFor(() => screen.getByTestId("scenario-lab-panel"));
      await user.click(screen.getByTestId("publish-resequence-btn"));

      await waitFor(() => {
        const publishCalls = fetchSpy.mock.calls.filter(([url]) =>
          typeof url === "string" && url.includes("/sequence/publish"),
        );
        expect(publishCalls.length, "POST /sequence/publish must have been called once").toBe(1);
      });

      const publishCalls = fetchSpy.mock.calls.filter(([url]) =>
        typeof url === "string" && url.includes("/sequence/publish"),
      );
      const body = JSON.parse(publishCalls[0]![1]!.body as string) as {
        isResequence: boolean;
        sequenceId: string;
      };

      // isResequence: true marks this as a mid-week targeted run in the audit log
      expect(body.isResequence, "publish payload must have isResequence: true").toBe(true);

      // sequenceId must be the runId from the /resequence response
      expect(body.sequenceId, "publish payload must carry the resequence runId").toBe(RUN_ID);
    },
  );
});
