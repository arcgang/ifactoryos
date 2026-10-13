import "../i18n";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ConstraintAlertPanel } from "./ConstraintAlertPanel";
import type { ConstraintAlertRecord } from "../types/alerts";
import type { OptimizeResponse } from "../types/optimizer";

// ── Fixtures ──────────────────────────────────────────────────────────────────

const ALERT_ID_1 = "00000000-0000-0000-0001-000000000001";
const ALERT_ID_2 = "00000000-0000-0000-0001-000000000002";
const RUN_ID = "00000000-0000-0000-0002-000000000001";

const AFFECTED_VINS = [
  "WBS3C4C08RC210447",
  "WBS3C4C09RC210448",
  "WBS3C4C07RC210449",
];

function makeAlert(overrides?: Partial<ConstraintAlertRecord>): ConstraintAlertRecord {
  return {
    alertId: ALERT_ID_1,
    constraintType: "kitting_readiness",
    currentValue: 79.2,
    thresholdValue: 82,
    affectedVins: AFFECTED_VINS,
    status: "active",
    detectedAt: "2026-10-12T10:34:41Z",
    acknowledgedAt: null,
    acknowledgedBy: null,
    ...overrides,
  };
}

function makeReseqResponse(overrides?: Partial<OptimizeResponse>): OptimizeResponse {
  return {
    runId: RUN_ID,
    weekIso: "2026-W42",
    resultType: "monte_carlo",
    rankedSequence: AFFECTED_VINS.map((vin, i) => ({
      vin,
      position: i + 1,
      constraintScore: 0.9 - i * 0.05,
    })),
    completedAt: "2026-10-12T10:39:01Z",
    durationMs: 4200,
    confidenceScore: 0.91,
    my26ConfigVersion: null,
    constraintSnapshotTimestamp: "2026-10-12T10:34:41Z",
    ...overrides,
  };
}

// ── Setup ─────────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks();
});

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("ConstraintAlertPanel — rendering", () => {
  it("renders nothing when alerts array is empty", () => {
    const { container } = render(
      <ConstraintAlertPanel alerts={[]} onAlertDismissed={() => {}} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders an alert card when active alert data is returned", () => {
    render(
      <ConstraintAlertPanel
        alerts={[makeAlert()]}
        onAlertDismissed={() => {}}
      />,
    );
    expect(screen.getByTestId(`alert-card-${ALERT_ID_1}`)).toBeTruthy();
  });

  it("renders constraint type label and current value", () => {
    render(
      <ConstraintAlertPanel
        alerts={[makeAlert()]}
        onAlertDismissed={() => {}}
      />,
    );
    const card = screen.getByTestId(`alert-card-${ALERT_ID_1}`);
    expect(card.textContent).toMatch(/kitting readiness/i);
    expect(card.textContent).toContain("79.2");
  });

  it("renders all three affected VINs from the alert payload", () => {
    render(
      <ConstraintAlertPanel
        alerts={[makeAlert()]}
        onAlertDismissed={() => {}}
      />,
    );
    for (const vin of AFFECTED_VINS) {
      expect(screen.getByText(vin)).toBeTruthy();
    }
  });

  it("renders multiple alert cards when multiple active alerts are present", () => {
    const alerts = [
      makeAlert({ alertId: ALERT_ID_1, constraintType: "kitting_readiness" }),
      makeAlert({ alertId: ALERT_ID_2, constraintType: "agv_delay" }),
    ];
    render(
      <ConstraintAlertPanel alerts={alerts} onAlertDismissed={() => {}} />,
    );
    expect(screen.getByTestId(`alert-card-${ALERT_ID_1}`)).toBeTruthy();
    expect(screen.getByTestId(`alert-card-${ALERT_ID_2}`)).toBeTruthy();
  });
});

describe("ConstraintAlertPanel — Re-sequence button", () => {
  it("renders Re-sequence Affected VINs button when active alert is displayed", () => {
    render(
      <ConstraintAlertPanel
        alerts={[makeAlert()]}
        onAlertDismissed={() => {}}
      />,
    );
    expect(
      screen.getByTestId(`resequence-btn-${ALERT_ID_1}`),
    ).toBeTruthy();
  });

  it("button is absent when no active alerts are present", () => {
    render(
      <ConstraintAlertPanel alerts={[]} onAlertDismissed={() => {}} />,
    );
    expect(
      screen.queryByTestId(`resequence-btn-${ALERT_ID_1}`),
    ).toBeNull();
  });

  it("calls POST /resequence with only the affected VINs from the alert on button click", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(makeReseqResponse()),
      text: () => Promise.resolve(""),
    } as Response);

    render(
      <ConstraintAlertPanel
        alerts={[makeAlert()]}
        onAlertDismissed={() => {}}
      />,
    );

    await userEvent.setup({ delay: null }).click(
      screen.getByTestId(`resequence-btn-${ALERT_ID_1}`),
    );

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalled();
    });

    const resequenceCalls = fetchSpy.mock.calls.filter(([url]) =>
      typeof url === "string" && url.includes("/resequence"),
    );
    expect(resequenceCalls.length).toBe(1);

    const bodyStr = resequenceCalls[0]?.[1]?.body as string;
    const body = JSON.parse(bodyStr) as {
      alertId: string;
      affectedVins: Array<{ vin: string }>;
    };

    expect(body.alertId).toBe(ALERT_ID_1);
    expect(body.affectedVins).toHaveLength(AFFECTED_VINS.length);
    expect(body.affectedVins.map((v) => v.vin)).toEqual(AFFECTED_VINS);
  });

  it("does NOT include VINs outside the alert's affectedVins in the POST /resequence payload", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(makeReseqResponse()),
      text: () => Promise.resolve(""),
    } as Response);

    render(
      <ConstraintAlertPanel
        alerts={[makeAlert()]}
        onAlertDismissed={() => {}}
      />,
    );

    await userEvent.setup({ delay: null }).click(
      screen.getByTestId(`resequence-btn-${ALERT_ID_1}`),
    );

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalled();
    });

    const resequenceCalls = fetchSpy.mock.calls.filter(([url]) =>
      typeof url === "string" && url.includes("/resequence"),
    );
    const bodyStr = resequenceCalls[0]?.[1]?.body as string;
    const body = JSON.parse(bodyStr) as { affectedVins: Array<{ vin: string }> };

    // Must equal exactly the 3 alert VINs — no extras
    expect(body.affectedVins).toHaveLength(3);
  });
});

describe("ConstraintAlertPanel — Scenario Lab panel", () => {
  it("Scenario Lab panel receives only the targeted rankedSequence (3 VINs from alert)", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(makeReseqResponse()),
      text: () => Promise.resolve(""),
    } as Response);

    render(
      <ConstraintAlertPanel
        alerts={[makeAlert()]}
        onAlertDismissed={() => {}}
      />,
    );

    await userEvent.setup({ delay: null }).click(
      screen.getByTestId(`resequence-btn-${ALERT_ID_1}`),
    );

    await waitFor(() => {
      expect(screen.getByTestId("scenario-lab-panel")).toBeTruthy();
    });

    const panel = screen.getByTestId("scenario-lab-panel");
    for (const vin of AFFECTED_VINS) {
      expect(panel.textContent).toContain(vin);
    }
  });

  it("Scenario Lab panel is labelled Targeted Re-sequence to distinguish it from the full weekly run", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(makeReseqResponse()),
      text: () => Promise.resolve(""),
    } as Response);

    render(
      <ConstraintAlertPanel
        alerts={[makeAlert()]}
        onAlertDismissed={() => {}}
      />,
    );

    await userEvent.setup({ delay: null }).click(
      screen.getByTestId(`resequence-btn-${ALERT_ID_1}`),
    );

    await waitFor(() => {
      expect(screen.getByTestId("scenario-lab-panel")).toBeTruthy();
    });

    expect(screen.getByTestId("scenario-lab-panel").textContent).toMatch(
      /targeted re-sequence/i,
    );
  });
});

describe("ConstraintAlertPanel — publish flow", () => {
  it("renders confirmation toast on 200 publish response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(makeReseqResponse()),
      text: () => Promise.resolve(""),
    } as Response);

    render(
      <ConstraintAlertPanel
        alerts={[makeAlert()]}
        onAlertDismissed={() => {}}
      />,
    );

    const user = userEvent.setup({ delay: null });

    await user.click(screen.getByTestId(`resequence-btn-${ALERT_ID_1}`));

    await waitFor(() => {
      expect(screen.getByTestId("scenario-lab-panel")).toBeTruthy();
    });

    await user.click(screen.getByTestId("publish-resequence-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("publish-confirmation-toast")).toBeTruthy();
    });

    expect(screen.getByTestId("publish-confirmation-toast").textContent).toMatch(
      /re-sequence published/i,
    );
    expect(screen.getByTestId("publish-confirmation-toast").textContent).toMatch(
      /planning-cockpit/i,
    );
  });

  it("calls POST /sequence/publish with isResequence:true and the runId from the re-sequence result", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(makeReseqResponse()),
      text: () => Promise.resolve(""),
    } as Response);

    render(
      <ConstraintAlertPanel
        alerts={[makeAlert()]}
        onAlertDismissed={() => {}}
      />,
    );

    const user = userEvent.setup({ delay: null });

    await user.click(screen.getByTestId(`resequence-btn-${ALERT_ID_1}`));

    await waitFor(() => {
      expect(screen.getByTestId("scenario-lab-panel")).toBeTruthy();
    });

    await user.click(screen.getByTestId("publish-resequence-btn"));

    await waitFor(() => {
      const publishCalls = fetchSpy.mock.calls.filter(([url]) =>
        typeof url === "string" && url.includes("/sequence/publish"),
      );
      expect(publishCalls.length).toBe(1);
    });

    const publishCalls = fetchSpy.mock.calls.filter(([url]) =>
      typeof url === "string" && url.includes("/sequence/publish"),
    );
    const bodyStr = publishCalls[0]?.[1]?.body as string;
    const body = JSON.parse(bodyStr) as { sequenceId: string; isResequence: boolean };
    expect(body.sequenceId).toBe(RUN_ID);
    expect(body.isResequence).toBe(true);
  });
});

describe("ConstraintAlertPanel — dismiss", () => {
  it("removes the alert card after dismiss is clicked", async () => {
    const onDismissed = vi.fn();

    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ alertId: ALERT_ID_1, status: "acknowledged" }),
      text: () => Promise.resolve(""),
    } as Response);

    render(
      <ConstraintAlertPanel
        alerts={[makeAlert()]}
        onAlertDismissed={onDismissed}
      />,
    );

    await userEvent.setup({ delay: null }).click(
      screen.getByTestId(`dismiss-alert-${ALERT_ID_1}`),
    );

    await waitFor(() => {
      expect(onDismissed).toHaveBeenCalledWith(ALERT_ID_1);
    });
  });
});

describe("ConstraintAlertPanel — OTel adoption events", () => {
  it("emits alert_viewed adoption event within 1 second of alert render", async () => {
    const events: CustomEvent[] = [];
    const handler = ((e: CustomEvent) => { events.push(e); }) as EventListener;
    window.addEventListener("ifos:adoption", handler);

    render(
      <ConstraintAlertPanel
        alerts={[makeAlert()]}
        onAlertDismissed={() => {}}
      />,
    );

    await waitFor(() => {
      const alertViewedEvents = events.filter(
        (e) => (e.detail as { action: string }).action === "alert_viewed",
      );
      expect(alertViewedEvents.length).toBeGreaterThanOrEqual(1);
    });

    window.removeEventListener("ifos:adoption", handler);
  });

  it("emits resequence_triggered adoption event with alertId and affectedVinCount on button click", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(makeReseqResponse()),
      text: () => Promise.resolve(""),
    } as Response);

    const events: CustomEvent[] = [];
    const handler = ((e: CustomEvent) => { events.push(e); }) as EventListener;
    window.addEventListener("ifos:adoption", handler);

    render(
      <ConstraintAlertPanel
        alerts={[makeAlert()]}
        onAlertDismissed={() => {}}
      />,
    );

    await userEvent.setup({ delay: null }).click(
      screen.getByTestId(`resequence-btn-${ALERT_ID_1}`),
    );

    await waitFor(() => {
      const triggered = events.filter(
        (e) => (e.detail as { action: string }).action === "resequence_triggered",
      );
      expect(triggered.length).toBeGreaterThanOrEqual(1);
      const detail = triggered[0]!.detail as {
        alertId: string;
        affectedVinCount: number;
      };
      expect(detail.alertId).toBe(ALERT_ID_1);
      expect(detail.affectedVinCount).toBe(AFFECTED_VINS.length);
    });

    window.removeEventListener("ifos:adoption", handler);
  });

  it("emits resequence_published adoption event with sequenceId on publish", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(makeReseqResponse()),
      text: () => Promise.resolve(""),
    } as Response);

    const events: CustomEvent[] = [];
    const handler = ((e: CustomEvent) => { events.push(e); }) as EventListener;
    window.addEventListener("ifos:adoption", handler);

    render(
      <ConstraintAlertPanel
        alerts={[makeAlert()]}
        onAlertDismissed={() => {}}
      />,
    );

    const user = userEvent.setup({ delay: null });
    await user.click(screen.getByTestId(`resequence-btn-${ALERT_ID_1}`));
    await waitFor(() => screen.getByTestId("scenario-lab-panel"));
    await user.click(screen.getByTestId("publish-resequence-btn"));

    await waitFor(() => {
      const published = events.filter(
        (e) => (e.detail as { action: string }).action === "resequence_published",
      );
      expect(published.length).toBeGreaterThanOrEqual(1);
      const detail = published[0]!.detail as { sequenceId: string };
      expect(detail.sequenceId).toBe(RUN_ID);
    });

    window.removeEventListener("ifos:adoption", handler);
  });
});
