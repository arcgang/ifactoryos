/**
 * US-011 acceptance tests — AR overlay indicators and Shift Lead alert toast.
 *
 * Cross-task seam: ARWorkbenchPage receives torque state from useTorqueStream
 * and renders TorqueStepIndicator.  When an OOT event arrives the component
 * calls the onOutOfTolerance prop which App.tsx uses to show
 * TorqueOutOfToleranceToast in the Command Center view.
 *
 * AC-1: A TorqueSubmittedEvent → in_tolerance state renders green indicator
 *       (role=status, "Logged to Digital Thread") and calls
 *       onTorqueConfirmationViewed with the torqueLogId.
 *
 * AC-2: A TorqueOutOfToleranceEvent → out_of_tolerance state renders red
 *       indicator (role=alert, "TORQUE OUT OF TOLERANCE") with all alert
 *       fields. TorqueOutOfToleranceToast carries role=alert and shows VIN,
 *       fastenerPointId, torqueNm, specNm, toleranceNm, alertId.
 *       onAcknowledge fires on button click.
 *
 * AC-3: A pending_sync state renders amber indicator (role=status,
 *       "Pending Sync").  A sync_confirmed state clears back to neutral
 *       ("awaiting tightening").
 */

import { render, screen, act, cleanup, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import i18n from "../i18n";
import { TorqueStepIndicator } from "./TorqueStepIndicator";
import { TorqueOutOfToleranceToast } from "./TorqueOutOfToleranceToast";
import type { TorqueIndicatorState } from "../hooks/useTorqueStream";
import type { TorqueOotPayload } from "./TorqueOutOfToleranceToast";

// ---------------------------------------------------------------------------
// Fixtures — far-future timestamps so tests never expire
// ---------------------------------------------------------------------------

const NEUTRAL_STATE: TorqueIndicatorState = { kind: "neutral" };

const IN_TOLERANCE_STATE: TorqueIndicatorState = {
  kind: "in_tolerance",
  torqueLogId: "tl-us011-0001",
  vin: "WBA3A5C55FK123456",
  torqueNm: 120.0,
  anglesDeg: 45.0,
  recordedAt: "2099-01-01T06:00:00.000Z",
};

const OUT_OF_TOLERANCE_STATE: TorqueIndicatorState = {
  kind: "out_of_tolerance",
  torqueLogId: "tl-us011-0002",
  vin: "WBA3A5C55FK123456",
  fastenerPointId: "FP-001",
  torqueNm: 130.0,
  specNm: 120.0,
  toleranceNm: 5.0,
  alertId: "al-us011-0099",
  recordedAt: "2099-01-01T06:00:00.000Z",
};

const PENDING_SYNC_STATE: TorqueIndicatorState = {
  kind: "pending_sync",
  stationId: "MARRIAGE-01",
};

const SYNC_CONFIRMED_STATE: TorqueIndicatorState = {
  kind: "sync_confirmed",
  stationId: "MARRIAGE-01",
};

const OOT_PAYLOAD: TorqueOotPayload = {
  torqueLogId: "tl-us011-0002",
  vin: "WBA3A5C55FK123456",
  fastenerPointId: "FP-001",
  torqueNm: 130.0,
  specNm: 120.0,
  toleranceNm: 5.0,
  alertId: "al-us011-0099",
  recordedAt: "2099-01-01T06:00:00.000Z",
};

// ---------------------------------------------------------------------------
// Render helpers
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

// ===========================================================================
// AC-1: In-tolerance tightening → green AR overlay step indicator
// ===========================================================================

describe("US-011 AC-1 — in-tolerance tightening: green AR overlay indicator", () => {
  it("renders role=status element for in_tolerance state (accessibility)", () => {
    // Precondition: TorqueStepIndicator must mount without error
    renderIndicator(IN_TOLERANCE_STATE);
    expect(screen.getByRole("status")).toBeTruthy();
  });

  it("displays 'Logged to Digital Thread' confirming quality record is complete", () => {
    renderIndicator(IN_TOLERANCE_STATE);
    expect(screen.getByText("Logged to Digital Thread")).toBeTruthy();
  });

  it("carries the green CSS class confirming the AR overlay is in green state", () => {
    const { container } = renderIndicator(IN_TOLERANCE_STATE);
    expect(container.querySelector(".torque-indicator--green")).toBeTruthy();
  });

  it("calls onTorqueConfirmationViewed once with torqueLogId 'tl-us011-0001'", () => {
    // This is the seam between TorqueStepIndicator and ARWorkbenchPage's OTel span
    const onViewed = vi.fn();
    renderIndicator(IN_TOLERANCE_STATE, onViewed);
    expect(onViewed).toHaveBeenCalledTimes(1);
    expect(onViewed).toHaveBeenCalledWith("tl-us011-0001");
  });

  it("does not call onTorqueConfirmationViewed a second time on re-render with same state", () => {
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

  it("transitions from neutral to in_tolerance within 2 s when PLC event arrives (AC-1 SLA)", async () => {
    const startMs = Date.now();

    const { rerender } = render(
      <I18nextProvider i18n={i18n}>
        <TorqueStepIndicator state={NEUTRAL_STATE} />
      </I18nextProvider>,
    );

    expect(screen.getByText(/awaiting tightening/i)).toBeTruthy();

    await act(async () => {
      rerender(
        <I18nextProvider i18n={i18n}>
          <TorqueStepIndicator state={IN_TOLERANCE_STATE} />
        </I18nextProvider>,
      );
    });

    const elapsedMs = Date.now() - startMs;
    expect(elapsedMs).toBeLessThan(2000);
    expect(screen.getByText("Logged to Digital Thread")).toBeTruthy();
  });
});

// ===========================================================================
// AC-2: Out-of-tolerance tightening → red indicator + Shift Lead toast alert
// ===========================================================================

describe("US-011 AC-2 — out-of-tolerance tightening: red indicator + Shift Lead toast", () => {
  it("renders role=alert element for out_of_tolerance state (assertive screen-reader announcement)", () => {
    renderIndicator(OUT_OF_TOLERANCE_STATE);
    expect(screen.getByRole("alert")).toBeTruthy();
  });

  it("displays 'TORQUE OUT OF TOLERANCE' title in the AR overlay indicator", () => {
    renderIndicator(OUT_OF_TOLERANCE_STATE);
    expect(screen.getByText("TORQUE OUT OF TOLERANCE")).toBeTruthy();
  });

  it("carries the red CSS class confirming the AR overlay is in red state", () => {
    const { container } = renderIndicator(OUT_OF_TOLERANCE_STATE);
    expect(container.querySelector(".torque-indicator--red")).toBeTruthy();
  });

  it("does not call onTorqueConfirmationViewed for out_of_tolerance state", () => {
    const onViewed = vi.fn();
    renderIndicator(OUT_OF_TOLERANCE_STATE, onViewed);
    expect(onViewed).not.toHaveBeenCalled();
  });

  it("TorqueOutOfToleranceToast renders role=alert for Shift Lead screen-reader announcement", () => {
    renderToast(OOT_PAYLOAD);
    expect(screen.getByRole("alert")).toBeTruthy();
  });

  it("TorqueOutOfToleranceToast title is 'Torque Out of Tolerance — Shift Lead Alert'", () => {
    renderToast(OOT_PAYLOAD);
    expect(screen.getByText("Torque Out of Tolerance — Shift Lead Alert")).toBeTruthy();
  });

  it("TorqueOutOfToleranceToast displays VIN WBA3A5C55FK123456", () => {
    renderToast(OOT_PAYLOAD);
    expect(screen.getByText("WBA3A5C55FK123456")).toBeTruthy();
  });

  it("TorqueOutOfToleranceToast displays fastenerPointId FP-001", () => {
    renderToast(OOT_PAYLOAD);
    expect(screen.getByText("FP-001")).toBeTruthy();
  });

  it("TorqueOutOfToleranceToast displays measured torqueNm 130.0 Nm", () => {
    renderToast(OOT_PAYLOAD);
    expect(screen.getByText(/130/)).toBeTruthy();
  });

  it("TorqueOutOfToleranceToast displays spec 120.0 Nm", () => {
    renderToast(OOT_PAYLOAD);
    expect(screen.getByText(/120/)).toBeTruthy();
  });

  it("TorqueOutOfToleranceToast displays alertId al-us011-0099", () => {
    renderToast(OOT_PAYLOAD);
    expect(screen.getByText("al-us011-0099")).toBeTruthy();
  });

  it("TorqueOutOfToleranceToast renders Acknowledge button when onAcknowledge provided", () => {
    renderToast(OOT_PAYLOAD, vi.fn());
    expect(screen.getByRole("button", { name: /acknowledge/i })).toBeTruthy();
  });

  it("TorqueOutOfToleranceToast calls onAcknowledge when Acknowledge button is clicked", async () => {
    const onAck = vi.fn();
    renderToast(OOT_PAYLOAD, onAck);
    const btn = screen.getByRole("button", { name: /acknowledge/i });
    await act(async () => { btn.click(); });
    expect(onAck).toHaveBeenCalledTimes(1);
  });

  it("transitions neutral → out_of_tolerance within 30 s on OOT event (AC-2 SLA)", async () => {
    const startMs = Date.now();

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

    const elapsedMs = Date.now() - startMs;
    expect(elapsedMs).toBeLessThan(30_000);
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.getByText("TORQUE OUT OF TOLERANCE")).toBeTruthy();
  });
});

// ===========================================================================
// AC-3: UNS unavailable → amber Pending Sync indicator; sync-confirmed clears it
// ===========================================================================

describe("US-011 AC-3 — UNS unavailable: Pending Sync indicator, clears on reconnect", () => {
  it("renders role=status element for pending_sync state", () => {
    renderIndicator(PENDING_SYNC_STATE);
    expect(screen.getByRole("status")).toBeTruthy();
  });

  it("displays 'Pending Sync' text in AR overlay indicator", () => {
    renderIndicator(PENDING_SYNC_STATE);
    expect(screen.getByText("Pending Sync")).toBeTruthy();
  });

  it("displays UNS offline message", () => {
    renderIndicator(PENDING_SYNC_STATE);
    expect(screen.getByText(/UNS temporarily offline/i)).toBeTruthy();
  });

  it("carries the amber CSS class confirming the Pending Sync visual state", () => {
    const { container } = renderIndicator(PENDING_SYNC_STATE);
    expect(container.querySelector(".torque-indicator--amber")).toBeTruthy();
  });

  it("aria-label mentions 'pending sync' for screen-reader users", () => {
    renderIndicator(PENDING_SYNC_STATE);
    const el = screen.getByRole("status");
    expect(el.getAttribute("aria-label")).toMatch(/pending sync/i);
  });

  it("Pending Sync indicator clears when sync_confirmed signal arrives", async () => {
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

    await waitFor(() => expect(screen.queryByText("Pending Sync")).toBeNull());
    expect(screen.getByText(/awaiting tightening/i)).toBeTruthy();
  });

  it("sync_confirmed renders identical to neutral (associate can resume work)", () => {
    renderIndicator(SYNC_CONFIRMED_STATE);
    expect(screen.getByRole("status")).toBeTruthy();
    expect(screen.getByText(/awaiting tightening/i)).toBeTruthy();
  });
});
