/**
 * Acceptance tests for US-012 — Trigger a remote expert call from the AR Workbench.
 *
 * Criterion coverage:
 *   AC1 — video approved: callMode=video → getUserMedia({ video: true, audio: true })
 *          annotations received over data channel are rendered in the AR overlay
 *   AC2 — voice-only (not approved): callMode=voice →
 *          getUserMedia({ video: false, audio: true }), no video track emitted
 *   AC3 — on end: end-call POST carries callId, expertUserId, durationSeconds;
 *          response auditEntryId displayed (confirming VDT log was written)
 *
 * These tests exercise the seam between useExpertCall (hook) and ExpertCallPanel
 * (UI) through the same harness that ARWorkbenchPage mounts, which no existing
 * unit test covers end-to-end.
 */

import { render, screen, cleanup, waitFor, fireEvent, act } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import i18n from "../i18n";
import { ExpertCallPanel } from "../components/ExpertCallPanel";
import { useExpertCall } from "./useExpertCall";
import * as apiModule from "../services/api";

// ---------------------------------------------------------------------------
// Module-level mocks (hoisted by Vitest)
// ---------------------------------------------------------------------------

vi.mock("../services/api", () => ({
  apiRequest: vi.fn(),
  ForbiddenError: class ForbiddenError extends Error {
    constructor(resource: string, _userId: string) {
      super(`403: ${resource}`);
      this.name = "ForbiddenError";
    }
  },
}));

// ---------------------------------------------------------------------------
// MediaDevices mock — captures every getUserMedia call and its constraints
// ---------------------------------------------------------------------------

const mockGetUserMedia = vi.fn();
Object.defineProperty(globalThis.navigator, "mediaDevices", {
  value: { getUserMedia: mockGetUserMedia },
  writable: true,
  configurable: true,
});

// ---------------------------------------------------------------------------
// RTCPeerConnection / RTCDataChannel mock
// Exposes the data channel's onmessage handler so tests can inject annotations.
// ---------------------------------------------------------------------------

type DataChannelHandle = {
  onmessage: ((ev: MessageEvent) => void) | null;
  close: ReturnType<typeof vi.fn>;
};

let _lastDataChannel: DataChannelHandle | null = null;

const mockAddTrack = vi.fn();
const mockCreateDataChannel = vi.fn((): DataChannelHandle => {
  const dc: DataChannelHandle = { onmessage: null, close: vi.fn() };
  _lastDataChannel = dc;
  return dc;
});
const mockPcClose = vi.fn();

vi.stubGlobal(
  "RTCPeerConnection",
  vi.fn(() => ({
    addTrack: mockAddTrack,
    createDataChannel: mockCreateDataChannel,
    close: mockPcClose,
  })),
);

// ---------------------------------------------------------------------------
// Test harness — wires hook + panel exactly as ARWorkbenchPage does
// ---------------------------------------------------------------------------

interface HarnessProps {
  vin?: string;
  stationId?: string;
  sessionId?: string;
  userId?: string;
}

function ExpertCallHarness({
  vin = "WBA3A5C55FK999001",
  stationId = "MARRIAGE-03",
  sessionId = "sess-accept-000000000001",
  userId = "associate@bmw-munich.de",
}: HarnessProps) {
  const { state, initiateCall, endCall } = useExpertCall({ vin, stationId, sessionId, userId });
  return (
    <I18nextProvider i18n={i18n}>
      <ExpertCallPanel state={state} onCallExpert={initiateCall} onEndCall={endCall} />
    </I18nextProvider>
  );
}

function renderHarness(props: HarnessProps = {}) {
  return render(<ExpertCallHarness {...props} />);
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeVideoStream() {
  return {
    getTracks: () => [
      { stop: vi.fn(), kind: "video" },
      { stop: vi.fn(), kind: "audio" },
    ],
  };
}

function makeAudioStream() {
  return {
    getTracks: () => [{ stop: vi.fn(), kind: "audio" }],
  };
}

// ---------------------------------------------------------------------------
// Setup / teardown
// ---------------------------------------------------------------------------

beforeEach(async () => {
  await i18n.changeLanguage("en-GB");
  vi.mocked(apiModule.apiRequest).mockReset();
  mockGetUserMedia.mockReset();
  mockAddTrack.mockReset();
  mockCreateDataChannel.mockImplementation((): DataChannelHandle => {
    const dc: DataChannelHandle = { onmessage: null, close: vi.fn() };
    _lastDataChannel = dc;
    return dc;
  });
  mockPcClose.mockReset();
  _lastDataChannel = null;
});

afterEach(() => {
  cleanup();
});

// ---------------------------------------------------------------------------
// AC1: video streaming Works-Council-approved
// ---------------------------------------------------------------------------

describe("AC1 — video streaming approved: expert sees associate field-of-view", () => {
  it("AC1: getUserMedia is called with video:true and audio:true when callMode=video is returned", async () => {
    // Precondition: API returns callMode=video (backend decided video is WC-approved)
    vi.mocked(apiModule.apiRequest).mockResolvedValueOnce({
      callId: "call-ac1-001",
      callMode: "video",
      createdAt: "2026-10-13T09:00:00Z",
    });
    mockGetUserMedia.mockResolvedValue(makeVideoStream());

    renderHarness();
    fireEvent.click(screen.getByRole("button", { name: /call a remote expert/i }));

    await waitFor(() => expect(mockGetUserMedia).toHaveBeenCalledTimes(1));

    // AC1: video:true must be in the getUserMedia constraints — field-of-view transmission
    const [constraints] = mockGetUserMedia.mock.calls[0] as [MediaStreamConstraints];
    expect(constraints.video).toBe(true);
    expect(constraints.audio).toBe(true);
  });

  it("AC1: video mode badge and 'No biometric or gaze data' notice rendered when callMode=video", async () => {
    vi.mocked(apiModule.apiRequest).mockResolvedValueOnce({
      callId: "call-ac1-002",
      callMode: "video",
      createdAt: "2026-10-13T09:00:00Z",
    });
    mockGetUserMedia.mockResolvedValue(makeVideoStream());

    renderHarness();
    fireEvent.click(screen.getByRole("button", { name: /call a remote expert/i }));

    await waitFor(() => screen.getByText(/Video Call/i));
    // AC1: panel confirms video call and privacy notice is shown
    expect(screen.getByText(/Video Call/i)).toBeTruthy();
    expect(screen.getByText(/No biometric or gaze data/i)).toBeTruthy();
  });

  it("AC1: holographic annotation received over data channel is rendered in the AR overlay", async () => {
    vi.mocked(apiModule.apiRequest).mockResolvedValueOnce({
      callId: "call-ac1-003",
      callMode: "video",
      createdAt: "2026-10-13T09:00:00Z",
    });
    mockGetUserMedia.mockResolvedValue(makeVideoStream());

    renderHarness();
    fireEvent.click(screen.getByRole("button", { name: /call a remote expert/i }));

    // Wait for active state with data channel wired
    await waitFor(() => screen.getByText(/Video Call/i));
    expect(_lastDataChannel).not.toBeNull();

    // AC1: expert sends a holographic annotation via the data channel
    const annotation = {
      annotationId: "ann-ac1-001",
      sessionId: "sess-accept-000000000001",
      sequenceNumber: 1,
      annotationType: "label",
      spatialAnchor: { x: 0.5, y: 0.5, z: 0.0, coordinateFrame: "world" },
      content: {
        labelText: "Apply torque here: 45 Nm",
        arrowVector: null,
        highlightBounds: null,
      },
      expertUserId: "expert-ac1-001",
      createdAt: "2026-10-13T09:00:05Z",
    };

    await act(async () => {
      // Simulate the ACS data channel delivering the annotation payload
      _lastDataChannel!.onmessage!({ data: JSON.stringify(annotation) } as MessageEvent);
    });

    // AC1: annotation label text must appear in the AR overlay
    await waitFor(() => screen.getByText("Apply torque here: 45 Nm"));
    expect(screen.getByText("Apply torque here: 45 Nm")).toBeTruthy();
  });

  it("AC1: out-of-order annotations (sequence number not strictly increasing) are discarded", async () => {
    vi.mocked(apiModule.apiRequest).mockResolvedValueOnce({
      callId: "call-ac1-004",
      callMode: "video",
      createdAt: "2026-10-13T09:00:00Z",
    });
    mockGetUserMedia.mockResolvedValue(makeVideoStream());

    renderHarness();
    fireEvent.click(screen.getByRole("button", { name: /call a remote expert/i }));
    await waitFor(() => screen.getByText(/Video Call/i));

    const makeAnnotation = (seq: number, text: string) => ({
      annotationId: `ann-${seq}`,
      sessionId: "sess-accept-000000000001",
      sequenceNumber: seq,
      annotationType: "label",
      spatialAnchor: { x: 0, y: 0, z: 0, coordinateFrame: "world" },
      content: { labelText: text, arrowVector: null, highlightBounds: null },
      expertUserId: "expert-001",
      createdAt: "2026-10-13T09:00:05Z",
    });

    await act(async () => {
      _lastDataChannel!.onmessage!({ data: JSON.stringify(makeAnnotation(1, "First")) } as MessageEvent);
    });
    await act(async () => {
      // seq=1 again — must be discarded (not strictly increasing)
      _lastDataChannel!.onmessage!({ data: JSON.stringify(makeAnnotation(1, "Duplicate")) } as MessageEvent);
    });

    // "Duplicate" annotation must not appear
    expect(screen.queryByText("Duplicate")).toBeNull();
    expect(screen.getByText("First")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// AC2: voice-only fallback — no video track emitted
// ---------------------------------------------------------------------------

describe("AC2 — voice-only fallback: audio channel only, no video transmitted", () => {
  it("AC2: getUserMedia is called with video:false and audio:true when callMode=voice is returned", async () => {
    // Precondition: API returns callMode=voice (backend decided video is not WC-approved)
    vi.mocked(apiModule.apiRequest).mockResolvedValueOnce({
      callId: "call-ac2-001",
      callMode: "voice",
      createdAt: "2026-10-13T09:00:00Z",
    });
    mockGetUserMedia.mockResolvedValue(makeAudioStream());

    renderHarness();
    fireEvent.click(screen.getByRole("button", { name: /call a remote expert/i }));

    await waitFor(() => expect(mockGetUserMedia).toHaveBeenCalledTimes(1));

    // AC2: video:false enforced at the media-constraints level — no video track transmitted
    const [constraints] = mockGetUserMedia.mock.calls[0] as [MediaStreamConstraints];
    expect(constraints.video).toBe(false);
    expect(constraints.audio).toBe(true);
  });

  it("AC2: voice mode badge and 'No video or gaze data' notice rendered when callMode=voice", async () => {
    vi.mocked(apiModule.apiRequest).mockResolvedValueOnce({
      callId: "call-ac2-002",
      callMode: "voice",
      createdAt: "2026-10-13T09:00:00Z",
    });
    mockGetUserMedia.mockResolvedValue(makeAudioStream());

    renderHarness();
    fireEvent.click(screen.getByRole("button", { name: /call a remote expert/i }));

    await waitFor(() => screen.getByText(/Voice-Only Call/i));
    // AC2: UI confirms voice-only mode with the privacy notice
    expect(screen.getByText(/Voice-Only Call/i)).toBeTruthy();
    expect(screen.getByText(/No video or gaze data/i)).toBeTruthy();
  });

  it("AC2: annotation overlay is not rendered in voice mode (no holographic channel)", async () => {
    vi.mocked(apiModule.apiRequest).mockResolvedValueOnce({
      callId: "call-ac2-003",
      callMode: "voice",
      createdAt: "2026-10-13T09:00:00Z",
    });
    mockGetUserMedia.mockResolvedValue(makeAudioStream());

    renderHarness();
    fireEvent.click(screen.getByRole("button", { name: /call a remote expert/i }));

    await waitFor(() => screen.getByText(/Voice-Only Call/i));
    // No annotation list in voice mode
    expect(screen.queryByRole("list")).toBeNull();
  });

  it("AC2: connecting indicator mentions 10-second SLA window (audio connection window)", async () => {
    // Backend response is never resolved — call stays in connecting state
    vi.mocked(apiModule.apiRequest).mockReturnValueOnce(new Promise(() => { /* pending */ }));

    renderHarness();
    fireEvent.click(screen.getByRole("button", { name: /call a remote expert/i }));

    await waitFor(() => screen.getByText(/Connecting to Expert/i));
    // AC2: 10-second SLA applies to both video and voice connections
    expect(screen.getByText(/10 seconds/i)).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// AC3: session end — duration, VIN, stationId, callMode, expertUserId in audit trail
// ---------------------------------------------------------------------------

describe("AC3 — session close: call data logged to VIN digital thread audit trail", () => {
  it("AC3: end-call POST includes callId, expertUserId, and a non-negative durationSeconds", async () => {
    vi.mocked(apiModule.apiRequest)
      .mockResolvedValueOnce({
        callId: "call-ac3-001",
        callMode: "voice",
        createdAt: "2026-10-13T09:00:00Z",
      })
      .mockResolvedValueOnce({
        callId: "call-ac3-001",
        auditEntryId: "audit-ac3-001",
        endedAt: "2026-10-13T09:01:30Z",
      });
    mockGetUserMedia.mockResolvedValue(makeAudioStream());

    renderHarness({ userId: "assoc-ac3@bmw.de" });
    fireEvent.click(screen.getByRole("button", { name: /call a remote expert/i }));
    await waitFor(() => screen.getByText(/Voice-Only Call/i));

    fireEvent.click(screen.getByRole("button", { name: /end (expert )?call/i }));
    await waitFor(() => expect(vi.mocked(apiModule.apiRequest)).toHaveBeenCalledTimes(2));

    const [endPath, endOptions] = vi.mocked(apiModule.apiRequest).mock.calls[1] as [
      string,
      { method: string; body: string },
    ];

    // AC3: correct endpoint called
    expect(endPath).toBe("/api/v1/ar/expert-calls/call-ac3-001/end");
    expect(endOptions.method).toBe("POST");

    const body = JSON.parse(endOptions.body) as {
      expertUserId: string;
      durationSeconds: number;
    };
    // AC3: expertUserId forwarded (logged in audit trail)
    expect(body.expertUserId).toBe("assoc-ac3@bmw.de");
    // AC3: duration must be non-negative
    expect(typeof body.durationSeconds).toBe("number");
    expect(body.durationSeconds).toBeGreaterThanOrEqual(0);
  });

  it("AC3: auditEntryId from end-call response is rendered — confirms VDT log was written", async () => {
    vi.mocked(apiModule.apiRequest)
      .mockResolvedValueOnce({
        callId: "call-ac3-002",
        callMode: "video",
        createdAt: "2026-10-13T09:00:00Z",
      })
      .mockResolvedValueOnce({
        callId: "call-ac3-002",
        auditEntryId: "audit-entry-ac3-confirmed-999",
        endedAt: "2026-10-13T09:02:00Z",
      });
    mockGetUserMedia.mockResolvedValue(makeVideoStream());

    renderHarness();
    fireEvent.click(screen.getByRole("button", { name: /call a remote expert/i }));
    await waitFor(() => screen.getByText(/Video Call/i));

    fireEvent.click(screen.getByRole("button", { name: /end (expert )?call/i }));

    // AC3: auditEntryId displayed in the ended confirmation state
    await waitFor(() => screen.getByText("audit-entry-ac3-confirmed-999"));
    expect(screen.getByText("audit-entry-ac3-confirmed-999")).toBeTruthy();
  });

  it("AC3: 'Call ended' confirmation text is shown after end-call succeeds", async () => {
    vi.mocked(apiModule.apiRequest)
      .mockResolvedValueOnce({
        callId: "call-ac3-003",
        callMode: "voice",
        createdAt: "2026-10-13T09:00:00Z",
      })
      .mockResolvedValueOnce({
        callId: "call-ac3-003",
        auditEntryId: "audit-ac3-003",
        endedAt: "2026-10-13T09:01:00Z",
      });
    mockGetUserMedia.mockResolvedValue(makeAudioStream());

    renderHarness();
    fireEvent.click(screen.getByRole("button", { name: /call a remote expert/i }));
    await waitFor(() => screen.getByText(/Voice-Only Call/i));

    fireEvent.click(screen.getByRole("button", { name: /end (expert )?call/i }));

    // AC3: confirmation that the session is closed and logged
    await waitFor(() => screen.getByText(/Call ended/i));
    expect(screen.getByText(/Call ended/i)).toBeTruthy();
  });

  it("AC3: end-call POST is made with the correct callId from the initiate response", async () => {
    const specificCallId = "call-ac3-specific-id-004";
    vi.mocked(apiModule.apiRequest)
      .mockResolvedValueOnce({
        callId: specificCallId,
        callMode: "voice",
        createdAt: "2026-10-13T09:00:00Z",
      })
      .mockResolvedValueOnce({
        callId: specificCallId,
        auditEntryId: "audit-ac3-004",
        endedAt: "2026-10-13T09:01:00Z",
      });
    mockGetUserMedia.mockResolvedValue(makeAudioStream());

    renderHarness();
    fireEvent.click(screen.getByRole("button", { name: /call a remote expert/i }));
    await waitFor(() => screen.getByText(/Voice-Only Call/i));

    fireEvent.click(screen.getByRole("button", { name: /end (expert )?call/i }));
    await waitFor(() => expect(vi.mocked(apiModule.apiRequest)).toHaveBeenCalledTimes(2));

    const [endPath] = vi.mocked(apiModule.apiRequest).mock.calls[1] as [string, unknown];
    // AC3: end request must reference the exact callId returned by the initiate call
    expect(endPath).toBe(`/api/v1/ar/expert-calls/${specificCallId}/end`);
  });

  it("AC3: media tracks are stopped before the end-call POST is issued (spec requirement)", async () => {
    const mockStop = vi.fn();
    const mockTrack = { stop: mockStop, kind: "audio" };

    vi.mocked(apiModule.apiRequest)
      .mockResolvedValueOnce({
        callId: "call-ac3-005",
        callMode: "voice",
        createdAt: "2026-10-13T09:00:00Z",
      })
      .mockImplementationOnce(async () => {
        // At the moment the end-call API is called, the track must already be stopped
        expect(mockStop).toHaveBeenCalledTimes(1);
        return {
          callId: "call-ac3-005",
          auditEntryId: "audit-ac3-005",
          endedAt: "2026-10-13T09:01:00Z",
        };
      });

    mockGetUserMedia.mockResolvedValue({ getTracks: () => [mockTrack] });

    renderHarness();
    fireEvent.click(screen.getByRole("button", { name: /call a remote expert/i }));
    await waitFor(() => screen.getByText(/Voice-Only Call/i));

    fireEvent.click(screen.getByRole("button", { name: /end (expert )?call/i }));
    await waitFor(() => expect(vi.mocked(apiModule.apiRequest)).toHaveBeenCalledTimes(2));
  });
});
