import { render, screen, act, cleanup, fireEvent } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import i18n from "../i18n";
import { ExpertCallPanel } from "./ExpertCallPanel";
import type { ExpertCallState } from "../hooks/useExpertCall";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderPanel(state: ExpertCallState, onCallExpert = vi.fn(), onEndCall = vi.fn()) {
  return render(
    <I18nextProvider i18n={i18n}>
      <ExpertCallPanel state={state} onCallExpert={onCallExpert} onEndCall={onEndCall} />
    </I18nextProvider>,
  );
}

const IDLE_STATE: ExpertCallState = { kind: "idle" };
const CONNECTING_STATE: ExpertCallState = { kind: "connecting" };
const ACTIVE_VIDEO_STATE: ExpertCallState = {
  kind: "active",
  callId: "call-uuid-001",
  callMode: "video",
  createdAt: "2026-10-13T09:14:35Z",
  elapsedSeconds: 107,
  annotations: [],
};
const ACTIVE_VOICE_STATE: ExpertCallState = {
  kind: "active",
  callId: "call-uuid-002",
  callMode: "voice",
  createdAt: "2026-10-13T09:20:00Z",
  elapsedSeconds: 42,
  annotations: [],
};
const ENDED_STATE: ExpertCallState = {
  kind: "ended",
  callId: "call-uuid-001",
  auditEntryId: "audit-entry-uuid-003",
  endedAt: "2026-10-13T09:16:22Z",
};
const ERROR_STATE: ExpertCallState = {
  kind: "error",
  message: "Failed to initiate expert call",
};

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
// Idle state — Call Expert button
// ---------------------------------------------------------------------------

describe("ExpertCallPanel — idle state", () => {
  it("renders the Call Expert button in idle state", () => {
    renderPanel(IDLE_STATE);
    expect(screen.getByRole("button", { name: /call a remote expert/i })).toBeTruthy();
  });

  it("Call Expert button has correct English text", () => {
    renderPanel(IDLE_STATE);
    expect(screen.getByText("Call Expert")).toBeTruthy();
  });

  it("Call Expert button has aria-label in English", () => {
    renderPanel(IDLE_STATE);
    const btn = screen.getByRole("button", { name: /call a remote expert for this rare variant/i });
    expect(btn).toBeTruthy();
  });

  it("Call Expert button has aria-label in German", async () => {
    await act(async () => { await i18n.changeLanguage("de-DE"); });
    renderPanel(IDLE_STATE);
    const btn = screen.getByRole("button", { name: /fernspezialist/i });
    expect(btn).toBeTruthy();
  });

  it("calls onCallExpert when Call Expert button is clicked", () => {
    const onCallExpert = vi.fn();
    renderPanel(IDLE_STATE, onCallExpert);
    fireEvent.click(screen.getByRole("button", { name: /call a remote expert/i }));
    expect(onCallExpert).toHaveBeenCalledTimes(1);
  });

  it("does not render connecting indicator in idle state", () => {
    renderPanel(IDLE_STATE);
    expect(screen.queryByText(/Connecting to Expert/i)).toBeNull();
  });

  it("does not render End Call button in idle state", () => {
    renderPanel(IDLE_STATE);
    expect(screen.queryByRole("button", { name: /end call/i })).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Connecting state
// ---------------------------------------------------------------------------

describe("ExpertCallPanel — connecting state", () => {
  it("renders connecting text in connecting state", () => {
    renderPanel(CONNECTING_STATE);
    expect(screen.getByText(/Connecting to Expert/i)).toBeTruthy();
  });

  it("has role=status and aria-live=assertive in connecting state", () => {
    renderPanel(CONNECTING_STATE);
    const el = screen.getByRole("status");
    expect(el.getAttribute("aria-live")).toBe("assertive");
  });

  it("renders Cancel button in connecting state", () => {
    renderPanel(CONNECTING_STATE);
    expect(screen.getByRole("button", { name: /cancel/i })).toBeTruthy();
  });

  it("calls onEndCall when Cancel is clicked in connecting state", () => {
    const onEndCall = vi.fn();
    renderPanel(CONNECTING_STATE, vi.fn(), onEndCall);
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(onEndCall).toHaveBeenCalledTimes(1);
  });

  it("renders SLA hint text", () => {
    renderPanel(CONNECTING_STATE);
    expect(screen.getByText(/10 seconds/i)).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Active — video call
// ---------------------------------------------------------------------------

describe("ExpertCallPanel — active video call", () => {
  it("renders Video Call mode badge in active video state", () => {
    renderPanel(ACTIVE_VIDEO_STATE);
    expect(screen.getByText(/Video Call/i)).toBeTruthy();
  });

  it("renders elapsed timer formatted as MM:SS for 107 seconds (01:47)", () => {
    renderPanel(ACTIVE_VIDEO_STATE);
    expect(screen.getByText("01:47")).toBeTruthy();
  });

  it("renders no-biometrics note in video mode", () => {
    renderPanel(ACTIVE_VIDEO_STATE);
    expect(screen.getByText(/No biometric or gaze data/i)).toBeTruthy();
  });

  it("has role=status and aria-live=assertive in active video state", () => {
    renderPanel(ACTIVE_VIDEO_STATE);
    expect(screen.getByRole("status")).toBeTruthy();
    expect(screen.getByRole("status").getAttribute("aria-live")).toBe("assertive");
  });

  it("renders End Call button in active video state", () => {
    renderPanel(ACTIVE_VIDEO_STATE);
    expect(screen.getByRole("button", { name: /end (expert )?call/i })).toBeTruthy();
  });

  it("calls onEndCall when End Call is clicked", () => {
    const onEndCall = vi.fn();
    renderPanel(ACTIVE_VIDEO_STATE, vi.fn(), onEndCall);
    fireEvent.click(screen.getByRole("button", { name: /end (expert )?call/i }));
    expect(onEndCall).toHaveBeenCalledTimes(1);
  });

  it("renders annotation list when annotations are present", () => {
    const stateWithAnnotations: ExpertCallState = {
      ...ACTIVE_VIDEO_STATE,
      annotations: [
        {
          annotationId: "ann-001",
          sessionId: "sess-001",
          sequenceNumber: 1,
          annotationType: "label",
          spatialAnchor: { x: 0.1, y: 0.2, z: 0.3, coordinateFrame: "world" },
          content: { labelText: "Bolt sequence: 1→3→5→2→4", arrowVector: null, highlightBounds: null },
          expertUserId: "expert-001",
          createdAt: "2026-10-13T09:14:50Z",
        },
      ],
    };
    renderPanel(stateWithAnnotations);
    expect(screen.getByText("Bolt sequence: 1→3→5→2→4")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Active — voice-only call (no video or gaze data)
// ---------------------------------------------------------------------------

describe("ExpertCallPanel — active voice-only call", () => {
  it("renders Voice-Only mode badge in active voice state", () => {
    renderPanel(ACTIVE_VOICE_STATE);
    expect(screen.getByText(/Voice-Only Call/i)).toBeTruthy();
  });

  it("renders elapsed timer for 42 seconds (00:42)", () => {
    renderPanel(ACTIVE_VOICE_STATE);
    expect(screen.getByText("00:42")).toBeTruthy();
  });

  it("renders voice-only note stating no video or gaze data transmitted", () => {
    renderPanel(ACTIVE_VOICE_STATE);
    expect(screen.getByText(/No video or gaze data/i)).toBeTruthy();
  });

  it("does not render annotation overlay in voice mode", () => {
    renderPanel(ACTIVE_VOICE_STATE);
    expect(screen.queryByRole("list")).toBeNull();
  });

  it("renders End Call button in active voice state", () => {
    renderPanel(ACTIVE_VOICE_STATE);
    expect(screen.getByRole("button", { name: /end (expert )?call/i })).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Ended state
// ---------------------------------------------------------------------------

describe("ExpertCallPanel — ended state", () => {
  it("renders ended confirmation text", () => {
    renderPanel(ENDED_STATE);
    expect(screen.getByText(/Call ended/i)).toBeTruthy();
  });

  it("renders audit entry ID in ended state", () => {
    renderPanel(ENDED_STATE);
    expect(screen.getByText("audit-entry-uuid-003")).toBeTruthy();
  });

  it("has role=status and aria-live=assertive in ended state", () => {
    renderPanel(ENDED_STATE);
    expect(screen.getByRole("status")).toBeTruthy();
    expect(screen.getByRole("status").getAttribute("aria-live")).toBe("assertive");
  });
});

// ---------------------------------------------------------------------------
// Error state
// ---------------------------------------------------------------------------

describe("ExpertCallPanel — error state", () => {
  it("renders error message text in error state", () => {
    renderPanel(ERROR_STATE);
    expect(screen.getByText("Failed to initiate expert call")).toBeTruthy();
  });

  it("renders role=alert in error state", () => {
    renderPanel(ERROR_STATE);
    expect(screen.getByRole("alert")).toBeTruthy();
  });

  it("renders Retry button in error state", () => {
    renderPanel(ERROR_STATE);
    expect(screen.getByRole("button", { name: /call a remote expert/i })).toBeTruthy();
  });

  it("calls onCallExpert when Retry is clicked in error state", () => {
    const onCallExpert = vi.fn();
    renderPanel(ERROR_STATE, onCallExpert);
    fireEvent.click(screen.getByRole("button", { name: /call a remote expert/i }));
    expect(onCallExpert).toHaveBeenCalledTimes(1);
  });
});

// ---------------------------------------------------------------------------
// i18n — German translation keys
// ---------------------------------------------------------------------------

describe("ExpertCallPanel — i18n German translations", () => {
  it("expert_call.call_button_label resolves in de-DE", async () => {
    await act(async () => { await i18n.changeLanguage("de-DE"); });
    expect(i18n.t("expert_call.call_button_label")).toBe(
      "Fernspezialist für diese seltene Variante anrufen",
    );
  });

  it("expert_call.end_call_text resolves in de-DE", async () => {
    await act(async () => { await i18n.changeLanguage("de-DE"); });
    expect(i18n.t("expert_call.end_call_text")).toBe("Anruf beenden");
  });

  it("expert_call.ended_text resolves in de-DE", async () => {
    await act(async () => { await i18n.changeLanguage("de-DE"); });
    expect(i18n.t("expert_call.ended_text")).toBe(
      "Anruf beendet — Sitzung im digitalen VIN-Thread protokolliert",
    );
  });

  it("expert_call.mode_voice resolves in de-DE", async () => {
    await act(async () => { await i18n.changeLanguage("de-DE"); });
    expect(i18n.t("expert_call.mode_voice")).toBe("Nur-Sprache-Anruf · Aktiv");
  });
});
