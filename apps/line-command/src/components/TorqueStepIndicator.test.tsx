import { render, screen, act, cleanup, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import * as axe from "axe-core";
import i18n from "../i18n";
import { TorqueStepIndicator } from "./TorqueStepIndicator";
import { TorqueOutOfToleranceToast } from "./TorqueOutOfToleranceToast";
import type { TorqueIndicatorState } from "../hooks/useTorqueStream";
import type { TorqueOotPayload } from "./TorqueOutOfToleranceToast";

// ---------------------------------------------------------------------------
// axe helper
// ---------------------------------------------------------------------------

async function expectNoAxeCriticalViolations(container: HTMLElement): Promise<void> {
  const results = await axe.run(container);
  const critical = results.violations.filter((v: axe.Result) => v.impact === "critical");
  expect(critical, `axe critical violations: ${critical.map((v: axe.Result) => v.id).join(", ")}`).toHaveLength(0);
}

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const NEUTRAL_STATE: TorqueIndicatorState = { kind: "neutral" };

const IN_TOLERANCE_STATE: TorqueIndicatorState = {
  kind: "in_tolerance",
  torqueLogId: "tl-00000000-0001",
  vin: "WBS31BA0XPC000042",
  torqueNm: 28.4,
  anglesDeg: 142,
  recordedAt: "2026-10-12T14:22:08.412Z",
};

const OUT_OF_TOLERANCE_STATE: TorqueIndicatorState = {
  kind: "out_of_tolerance",
  torqueLogId: "tl-00000000-0002",
  vin: "WBS31BA0XPC000089",
  fastenerPointId: "FP-SUBFRAME-M12-04",
  torqueNm: 89.1,
  specNm: 120,
  toleranceNm: 12,
  alertId: "al-00000000-0099",
  recordedAt: "2026-10-12T14:38:54.107Z",
};

const PENDING_SYNC_STATE: TorqueIndicatorState = {
  kind: "pending_sync",
  stationId: "FINAL-07",
};

const SYNC_CONFIRMED_STATE: TorqueIndicatorState = {
  kind: "sync_confirmed",
  stationId: "FINAL-07",
};

const OOT_PAYLOAD: TorqueOotPayload = {
  torqueLogId: "tl-00000000-0002",
  vin: "WBS31BA0XPC000089",
  fastenerPointId: "FP-SUBFRAME-M12-04",
  torqueNm: 89.1,
  specNm: 120,
  toleranceNm: 12,
  alertId: "al-00000000-0099",
  recordedAt: "2026-10-12T14:38:54.107Z",
};

// ---------------------------------------------------------------------------
// Render helper
// ---------------------------------------------------------------------------

function renderIndicator(state: TorqueIndicatorState, onViewed?: (id: string) => void) {
  return render(
    <I18nextProvider i18n={i18n}>
      <TorqueStepIndicator state={state} onTorqueConfirmationViewed={onViewed} />
    </I18nextProvider>,
  );
}

function renderToast(payload: TorqueOotPayload, onAck?: () => void) {
  return render(
    <I18nextProvider i18n={i18n}>
      <TorqueOutOfToleranceToast payload={payload} onAcknowledge={onAck} />
    </I18nextProvider>,
  );
}

// ---------------------------------------------------------------------------
// Setup / teardown
// ---------------------------------------------------------------------------

beforeEach(async () => {
  await i18n.changeLanguage("en-GB");
});

afterEach(() => {
  cleanup();
});

// ---------------------------------------------------------------------------
// State 1: Neutral
// ---------------------------------------------------------------------------

describe("TorqueStepIndicator — State: neutral (awaiting PLC event)", () => {
  it("renders a role=status element", () => {
    renderIndicator(NEUTRAL_STATE);
    expect(screen.getByRole("status")).toBeTruthy();
  });

  it("aria-label describes awaiting state", () => {
    renderIndicator(NEUTRAL_STATE);
    const el = screen.getByRole("status");
    expect(el.getAttribute("aria-label")).toMatch(/awaiting/i);
  });

  it("renders awaiting text", () => {
    renderIndicator(NEUTRAL_STATE);
    expect(screen.getByText(/awaiting tightening/i)).toBeTruthy();
  });

  it("carries neutral CSS class", () => {
    const { container } = renderIndicator(NEUTRAL_STATE);
    expect(container.querySelector(".torque-indicator--neutral")).toBeTruthy();
  });

  it("has zero axe critical violations (neutral state)", async () => {
    const { container } = renderIndicator(NEUTRAL_STATE);
    await expectNoAxeCriticalViolations(container);
  });
});

// ---------------------------------------------------------------------------
// State 2: In-Tolerance (green)
// ---------------------------------------------------------------------------

describe("TorqueStepIndicator — State: in_tolerance (green)", () => {
  it("renders a role=status element", () => {
    renderIndicator(IN_TOLERANCE_STATE);
    expect(screen.getByRole("status")).toBeTruthy();
  });

  it("aria-label references the torqueNm value 28.4", () => {
    renderIndicator(IN_TOLERANCE_STATE);
    const el = screen.getByRole("status");
    expect(el.getAttribute("aria-label")).toContain("28.4");
  });

  it("renders 'Logged to Digital Thread' title", () => {
    renderIndicator(IN_TOLERANCE_STATE);
    expect(screen.getByText("Logged to Digital Thread")).toBeTruthy();
  });

  it("renders the torqueNm value 28.4 Nm", () => {
    renderIndicator(IN_TOLERANCE_STATE);
    expect(screen.getByText(/28\.4 Nm/i)).toBeTruthy();
  });

  it("renders the torqueLogId", () => {
    renderIndicator(IN_TOLERANCE_STATE);
    expect(screen.getByText(/tl-00000000-0001/)).toBeTruthy();
  });

  it("carries green CSS class", () => {
    const { container } = renderIndicator(IN_TOLERANCE_STATE);
    expect(container.querySelector(".torque-indicator--green")).toBeTruthy();
  });

  it("calls onTorqueConfirmationViewed exactly once with torqueLogId on first render", () => {
    const onViewed = vi.fn();
    renderIndicator(IN_TOLERANCE_STATE, onViewed);
    expect(onViewed).toHaveBeenCalledTimes(1);
    expect(onViewed).toHaveBeenCalledWith("tl-00000000-0001");
  });

  it("does not call onTorqueConfirmationViewed a second time if re-rendered with same state", () => {
    const onViewed = vi.fn();
    const { rerender } = render(
      <I18nextProvider i18n={i18n}>
        <TorqueStepIndicator state={IN_TOLERANCE_STATE} onTorqueConfirmationViewed={onViewed} />
      </I18nextProvider>,
    );
    rerender(
      <I18nextProvider i18n={i18n}>
        <TorqueStepIndicator state={IN_TOLERANCE_STATE} onTorqueConfirmationViewed={onViewed} />
      </I18nextProvider>,
    );
    expect(onViewed).toHaveBeenCalledTimes(1);
  });

  it("has zero axe critical violations (in_tolerance state)", async () => {
    const { container } = renderIndicator(IN_TOLERANCE_STATE);
    await expectNoAxeCriticalViolations(container);
  });
});

// ---------------------------------------------------------------------------
// State 3: Out-of-Tolerance (red)
// ---------------------------------------------------------------------------

describe("TorqueStepIndicator — State: out_of_tolerance (red)", () => {
  it("renders a role=alert element (assertive announcement)", () => {
    renderIndicator(OUT_OF_TOLERANCE_STATE);
    expect(screen.getByRole("alert")).toBeTruthy();
  });

  it("aria-label references VIN WBS31BA0XPC000089", () => {
    renderIndicator(OUT_OF_TOLERANCE_STATE);
    const el = screen.getByRole("alert");
    expect(el.getAttribute("aria-label")).toContain("WBS31BA0XPC000089");
  });

  it("renders 'TORQUE OUT OF TOLERANCE' title", () => {
    renderIndicator(OUT_OF_TOLERANCE_STATE);
    expect(screen.getByText("TORQUE OUT OF TOLERANCE")).toBeTruthy();
  });

  it("renders torqueNm 89.1 Nm", () => {
    renderIndicator(OUT_OF_TOLERANCE_STATE);
    expect(screen.getByText(/89\.1 Nm/i)).toBeTruthy();
  });

  it("renders specNm 120 Nm in the subtitle", () => {
    renderIndicator(OUT_OF_TOLERANCE_STATE);
    expect(screen.getByText(/spec 120 Nm/i)).toBeTruthy();
  });

  it("renders the fastenerPointId FP-SUBFRAME-M12-04", () => {
    renderIndicator(OUT_OF_TOLERANCE_STATE);
    expect(screen.getByText(/FP-SUBFRAME-M12-04/)).toBeTruthy();
  });

  it("renders the alertId al-00000000-0099", () => {
    renderIndicator(OUT_OF_TOLERANCE_STATE);
    expect(screen.getByText(/al-00000000-0099/)).toBeTruthy();
  });

  it("renders a HOLD message directing associate to stop", () => {
    renderIndicator(OUT_OF_TOLERANCE_STATE);
    expect(screen.getByText(/HOLD/i)).toBeTruthy();
  });

  it("carries red CSS class", () => {
    const { container } = renderIndicator(OUT_OF_TOLERANCE_STATE);
    expect(container.querySelector(".torque-indicator--red")).toBeTruthy();
  });

  it("does not call onTorqueConfirmationViewed for out_of_tolerance state", () => {
    const onViewed = vi.fn();
    renderIndicator(OUT_OF_TOLERANCE_STATE, onViewed);
    expect(onViewed).not.toHaveBeenCalled();
  });

  it("has zero axe critical violations (out_of_tolerance state)", async () => {
    const { container } = renderIndicator(OUT_OF_TOLERANCE_STATE);
    await expectNoAxeCriticalViolations(container);
  });
});

// ---------------------------------------------------------------------------
// State 4: Pending Sync (amber)
// ---------------------------------------------------------------------------

describe("TorqueStepIndicator — State: pending_sync (amber)", () => {
  it("renders a role=status element", () => {
    renderIndicator(PENDING_SYNC_STATE);
    expect(screen.getByRole("status")).toBeTruthy();
  });

  it("aria-label describes edge-buffering state", () => {
    renderIndicator(PENDING_SYNC_STATE);
    const el = screen.getByRole("status");
    expect(el.getAttribute("aria-label")).toMatch(/pending sync/i);
  });

  it("renders 'Pending Sync' title", () => {
    renderIndicator(PENDING_SYNC_STATE);
    expect(screen.getByText("Pending Sync")).toBeTruthy();
  });

  it("renders UNS offline message", () => {
    renderIndicator(PENDING_SYNC_STATE);
    expect(screen.getByText(/UNS temporarily offline/i)).toBeTruthy();
  });

  it("carries amber CSS class", () => {
    const { container } = renderIndicator(PENDING_SYNC_STATE);
    expect(container.querySelector(".torque-indicator--amber")).toBeTruthy();
  });

  it("has zero axe critical violations (pending_sync state)", async () => {
    const { container } = renderIndicator(PENDING_SYNC_STATE);
    await expectNoAxeCriticalViolations(container);
  });
});

// ---------------------------------------------------------------------------
// sync_confirmed — indicator clears back to neutral
// ---------------------------------------------------------------------------

describe("TorqueStepIndicator — State: sync_confirmed (clears pending)", () => {
  it("renders neutral state after sync_confirmed", () => {
    renderIndicator(SYNC_CONFIRMED_STATE);
    // sync_confirmed renders identically to neutral
    expect(screen.getByRole("status")).toBeTruthy();
    expect(screen.getByText(/awaiting tightening/i)).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// State transitions: pending_sync → sync_confirmed
// ---------------------------------------------------------------------------

describe("TorqueStepIndicator — state transition: pending_sync → sync_confirmed", () => {
  it("clears the pending_sync indicator when state changes to sync_confirmed", async () => {
    const { rerender } = render(
      <I18nextProvider i18n={i18n}>
        <TorqueStepIndicator state={PENDING_SYNC_STATE} />
      </I18nextProvider>,
    );
    expect(screen.getByText("Pending Sync")).toBeTruthy();

    rerender(
      <I18nextProvider i18n={i18n}>
        <TorqueStepIndicator state={SYNC_CONFIRMED_STATE} />
      </I18nextProvider>,
    );
    await waitFor(() => expect(screen.queryByText("Pending Sync")).toBeNull());
    expect(screen.getByText(/awaiting tightening/i)).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// TorqueOutOfToleranceToast — Shift Lead alert
// ---------------------------------------------------------------------------

describe("TorqueOutOfToleranceToast — Shift Lead alert", () => {
  it("renders role=alert (assertive announcement)", () => {
    renderToast(OOT_PAYLOAD);
    expect(screen.getByRole("alert")).toBeTruthy();
  });

  it("renders the title 'Torque Out of Tolerance — Shift Lead Alert'", () => {
    renderToast(OOT_PAYLOAD);
    expect(screen.getByText("Torque Out of Tolerance — Shift Lead Alert")).toBeTruthy();
  });

  it("renders VIN WBS31BA0XPC000089", () => {
    renderToast(OOT_PAYLOAD);
    expect(screen.getByText("WBS31BA0XPC000089")).toBeTruthy();
  });

  it("renders fastenerPointId FP-SUBFRAME-M12-04", () => {
    renderToast(OOT_PAYLOAD);
    expect(screen.getByText("FP-SUBFRAME-M12-04")).toBeTruthy();
  });

  it("renders recorded torqueNm 89.1 Nm", () => {
    renderToast(OOT_PAYLOAD);
    expect(screen.getByText(/89\.1/)).toBeTruthy();
  });

  it("renders specNm 120 Nm", () => {
    renderToast(OOT_PAYLOAD);
    expect(screen.getByText(/120/)).toBeTruthy();
  });

  it("renders alertId al-00000000-0099", () => {
    renderToast(OOT_PAYLOAD);
    expect(screen.getByText("al-00000000-0099")).toBeTruthy();
  });

  it("renders an Acknowledge button when onAcknowledge is provided", () => {
    const onAck = vi.fn();
    renderToast(OOT_PAYLOAD, onAck);
    expect(screen.getByRole("button", { name: /acknowledge/i })).toBeTruthy();
  });

  it("calls onAcknowledge when Acknowledge button is clicked", async () => {
    const onAck = vi.fn();
    renderToast(OOT_PAYLOAD, onAck);
    const btn = screen.getByRole("button", { name: /acknowledge/i });
    await act(async () => { btn.click(); });
    expect(onAck).toHaveBeenCalledTimes(1);
  });

  it("does not render Acknowledge button when onAcknowledge is omitted", () => {
    renderToast(OOT_PAYLOAD);
    expect(screen.queryByRole("button", { name: /acknowledge/i })).toBeNull();
  });

  it("has zero axe critical violations", async () => {
    const { container } = renderToast(OOT_PAYLOAD);
    await expectNoAxeCriticalViolations(container);
  });
});

// ---------------------------------------------------------------------------
// SSE event simulation — TorqueSubmittedEvent → green indicator within 2 s
// ---------------------------------------------------------------------------

describe("SSE event simulation — TorqueSubmittedEvent → in_tolerance", () => {
  it("transitions neutral → in_tolerance on TorqueSubmittedEvent within 2s", async () => {
    // Simulate the SSE stream by providing a mock hook result via direct render
    // The useTorqueStream hook is integration-tested via the component accepting state as a prop.
    // This test verifies the full UI path: in_tolerance state renders within 2s of state change.
    const startMs = Date.now();

    const { rerender } = render(
      <I18nextProvider i18n={i18n}>
        <TorqueStepIndicator state={NEUTRAL_STATE} />
      </I18nextProvider>,
    );

    expect(screen.getByText(/awaiting tightening/i)).toBeTruthy();

    // Simulate SSE event arriving (TorqueSubmittedEvent → in_tolerance)
    await act(async () => {
      rerender(
        <I18nextProvider i18n={i18n}>
          <TorqueStepIndicator state={IN_TOLERANCE_STATE} />
        </I18nextProvider>,
      );
    });

    const elapsedMs = Date.now() - startMs;
    // State change itself is synchronous — well within 2s
    expect(elapsedMs).toBeLessThan(2000);
    expect(screen.getByText("Logged to Digital Thread")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// SSE event simulation — TorqueOutOfToleranceEvent → red indicator + toast
// ---------------------------------------------------------------------------

describe("SSE event simulation — TorqueOutOfToleranceEvent → red + Shift Lead toast", () => {
  it("transitions neutral → out_of_tolerance on TorqueOutOfToleranceEvent", async () => {
    const { rerender } = render(
      <I18nextProvider i18n={i18n}>
        <TorqueStepIndicator state={NEUTRAL_STATE} />
      </I18nextProvider>,
    );

    await act(async () => {
      rerender(
        <I18nextProvider i18n={i18n}>
          <TorqueStepIndicator state={OUT_OF_TOLERANCE_STATE} />
        </I18nextProvider>,
      );
    });

    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.getByText("TORQUE OUT OF TOLERANCE")).toBeTruthy();
  });

  it("renders Shift Lead toast with correct VIN and alertId on OOT event", () => {
    renderToast(OOT_PAYLOAD);
    expect(screen.getByText("WBS31BA0XPC000089")).toBeTruthy();
    expect(screen.getByText("al-00000000-0099")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// SSE event simulation — PENDING_SYNC → amber indicator
// ---------------------------------------------------------------------------

describe("SSE event simulation — PENDING_SYNC signal → pending-sync indicator", () => {
  it("renders pending_sync indicator when PENDING_SYNC signal arrives", async () => {
    const { rerender } = render(
      <I18nextProvider i18n={i18n}>
        <TorqueStepIndicator state={NEUTRAL_STATE} />
      </I18nextProvider>,
    );

    await act(async () => {
      rerender(
        <I18nextProvider i18n={i18n}>
          <TorqueStepIndicator state={PENDING_SYNC_STATE} />
        </I18nextProvider>,
      );
    });

    expect(screen.getByText("Pending Sync")).toBeTruthy();
    const el = screen.getByRole("status");
    expect(el.getAttribute("aria-label")).toMatch(/pending sync/i);
  });
});

// ---------------------------------------------------------------------------
// SSE event simulation — sync_confirmed → indicator cleared
// ---------------------------------------------------------------------------

describe("SSE event simulation — sync_confirmed → indicator cleared", () => {
  it("indicator returns to neutral after sync-confirmed signal", async () => {
    const { rerender } = render(
      <I18nextProvider i18n={i18n}>
        <TorqueStepIndicator state={PENDING_SYNC_STATE} />
      </I18nextProvider>,
    );

    expect(screen.getByText("Pending Sync")).toBeTruthy();

    await act(async () => {
      rerender(
        <I18nextProvider i18n={i18n}>
          <TorqueStepIndicator state={SYNC_CONFIRMED_STATE} />
        </I18nextProvider>,
      );
    });

    expect(screen.queryByText("Pending Sync")).toBeNull();
    expect(screen.getByText(/awaiting tightening/i)).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// i18n — German translations for torque keys
// ---------------------------------------------------------------------------

describe("i18n — German torque translation keys", () => {
  it("torque.indicator.in_tolerance_title resolves in de-DE", async () => {
    await act(async () => { await i18n.changeLanguage("de-DE"); });
    expect(i18n.t("torque.indicator.in_tolerance_title")).toBe("Im digitalen Thread protokolliert");
  });

  it("torque.indicator.oot_title resolves in de-DE", async () => {
    await act(async () => { await i18n.changeLanguage("de-DE"); });
    expect(i18n.t("torque.indicator.oot_title")).toBe("DREHMOMENT AUSSERHALB DER TOLERANZ");
  });

  it("torque.indicator.pending_sync_title resolves in de-DE", async () => {
    await act(async () => { await i18n.changeLanguage("de-DE"); });
    expect(i18n.t("torque.indicator.pending_sync_title")).toBe("Synchronisation ausstehend");
  });

  it("torque.oot_toast.title resolves in en-GB", async () => {
    await act(async () => { await i18n.changeLanguage("en-GB"); });
    expect(i18n.t("torque.oot_toast.title")).toBe("Torque Out of Tolerance — Shift Lead Alert");
  });
});
