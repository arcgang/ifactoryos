import { render, screen, cleanup, waitFor, fireEvent } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import i18n from "../i18n";
import { ExpertCallPanel } from "../components/ExpertCallPanel";
import { useExpertCall } from "./useExpertCall";
import * as apiModule from "../services/api";

// ---------------------------------------------------------------------------
// Mock apiRequest at module level
// Vitest hoists vi.mock() calls before imports, so apiModule.apiRequest
// will be the mocked version when the module resolves.
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

// Mock navigator.mediaDevices
const mockGetUserMedia = vi.fn();
Object.defineProperty(global.navigator, "mediaDevices", {
  value: { getUserMedia: mockGetUserMedia },
  writable: true,
  configurable: true,
});

// Mock RTCPeerConnection
const mockAddTrack = vi.fn();
const mockCreateDataChannel = vi.fn(() => ({ onmessage: null as ((ev: MessageEvent) => void) | null, close: vi.fn() }));
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
// Test harness component that wires hook + panel
// ---------------------------------------------------------------------------

interface HarnessProps {
  vin?: string;
  stationId?: string;
  sessionId?: string;
  userId?: string;
}

function ExpertCallHarness({
  vin = "WBA3C9C50NF028471",
  stationId = "M-07",
  sessionId = "sess-00000000-0000-0000-0000-000000000001",
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
// Setup / teardown
// ---------------------------------------------------------------------------

beforeEach(async () => {
  await i18n.changeLanguage("en-GB");
  vi.mocked(apiModule.apiRequest).mockReset();
  mockGetUserMedia.mockReset();
  mockAddTrack.mockReset();
  mockCreateDataChannel.mockReset();
  mockPcClose.mockReset();
  // Default: open voice stream succeeds
  mockGetUserMedia.mockResolvedValue({
    getTracks: () => [{ stop: vi.fn(), kind: "audio" }],
  });
});

afterEach(() => {
  cleanup();
});

// ---------------------------------------------------------------------------
// Idle state
// ---------------------------------------------------------------------------

describe("useExpertCall — idle state", () => {
  it("renders Call Expert button initially", () => {
    renderHarness();
    expect(screen.getByRole("button", { name: /call a remote expert/i })).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Initiating call — POST body
// ---------------------------------------------------------------------------

describe("useExpertCall — POST /ar/expert-calls on tap", () => {
  it("calls POST /api/v1/ar/expert-calls with correct vin, stationId, sessionId on button tap", async () => {
    vi.mocked(apiModule.apiRequest).mockResolvedValueOnce({
      callId: "call-uuid-100",
      callMode: "voice",
      createdAt: "2026-10-13T09:00:00Z",
    });

    renderHarness({
      vin: "WBA3C9C50NF028471",
      stationId: "M-07",
      sessionId: "sess-test-001",
      userId: "user@bmw.de",
    });

    fireEvent.click(screen.getByRole("button", { name: /call a remote expert/i }));

    await waitFor(() => expect(vi.mocked(apiModule.apiRequest)).toHaveBeenCalledTimes(1));

    const [path, options] = vi.mocked(apiModule.apiRequest).mock.calls[0] as [string, { body: string; method: string }];
    expect(path).toBe("/api/v1/ar/expert-calls");
    expect(options.method).toBe("POST");

    const body = JSON.parse(options.body) as {
      vin: string;
      stationId: string;
      sessionId: string;
      idempotencyKey: string;
    };
    expect(body.vin).toBe("WBA3C9C50NF028471");
    expect(body.stationId).toBe("M-07");
    expect(body.sessionId).toBe("sess-test-001");
    expect(body.idempotencyKey).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
  });

  it("transitions to connecting state immediately on button tap", async () => {
    vi.mocked(apiModule.apiRequest).mockReturnValueOnce(new Promise(() => { /* pending */ }));

    renderHarness();
    fireEvent.click(screen.getByRole("button", { name: /call a remote expert/i }));

    await waitFor(() =>
      expect(screen.getByText(/Connecting to Expert/i)).toBeTruthy(),
    );
  });
});

// ---------------------------------------------------------------------------
// Video call path
// ---------------------------------------------------------------------------

describe("useExpertCall — callMode='video' response", () => {
  it("renders Video Call panel when callMode=video is returned", async () => {
    mockGetUserMedia.mockResolvedValueOnce({
      getTracks: () => [{ stop: vi.fn(), kind: "video" }, { stop: vi.fn(), kind: "audio" }],
    });
    vi.mocked(apiModule.apiRequest).mockResolvedValueOnce({
      callId: "call-uuid-video-001",
      callMode: "video",
      createdAt: "2026-10-13T09:14:35Z",
    });

    renderHarness();
    fireEvent.click(screen.getByRole("button", { name: /call a remote expert/i }));

    await waitFor(() => screen.getByText(/Video Call/i));
    expect(screen.getByText(/Video Call/i)).toBeTruthy();
  });

  it("calls getUserMedia with video:true for video call mode", async () => {
    mockGetUserMedia.mockResolvedValueOnce({
      getTracks: () => [{ stop: vi.fn(), kind: "video" }, { stop: vi.fn(), kind: "audio" }],
    });
    vi.mocked(apiModule.apiRequest).mockResolvedValueOnce({
      callId: "call-uuid-video-002",
      callMode: "video",
      createdAt: "2026-10-13T09:14:35Z",
    });

    renderHarness();
    fireEvent.click(screen.getByRole("button", { name: /call a remote expert/i }));

    await waitFor(() => expect(mockGetUserMedia).toHaveBeenCalledTimes(1));
    expect(mockGetUserMedia).toHaveBeenCalledWith({ video: true, audio: true });
  });

  it("renders no-biometrics note in active video state", async () => {
    mockGetUserMedia.mockResolvedValueOnce({
      getTracks: () => [{ stop: vi.fn() }, { stop: vi.fn() }],
    });
    vi.mocked(apiModule.apiRequest).mockResolvedValueOnce({
      callId: "call-uuid-video-003",
      callMode: "video",
      createdAt: "2026-10-13T09:14:35Z",
    });

    renderHarness();
    fireEvent.click(screen.getByRole("button", { name: /call a remote expert/i }));

    await waitFor(() => screen.getByText(/No biometric or gaze data/i));
    expect(screen.getByText(/No biometric or gaze data/i)).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Voice-only path — enforce no video track
// ---------------------------------------------------------------------------

describe("useExpertCall — callMode='voice' response", () => {
  it("renders Voice-Only Call panel when callMode=voice is returned", async () => {
    vi.mocked(apiModule.apiRequest).mockResolvedValueOnce({
      callId: "call-uuid-voice-001",
      callMode: "voice",
      createdAt: "2026-10-13T09:20:00Z",
    });

    renderHarness();
    fireEvent.click(screen.getByRole("button", { name: /call a remote expert/i }));

    await waitFor(() => screen.getByText(/Voice-Only Call/i));
    expect(screen.getByText(/Voice-Only Call/i)).toBeTruthy();
  });

  it("calls getUserMedia with video:false for voice call — no video track emitted", async () => {
    vi.mocked(apiModule.apiRequest).mockResolvedValueOnce({
      callId: "call-uuid-voice-002",
      callMode: "voice",
      createdAt: "2026-10-13T09:20:00Z",
    });

    renderHarness();
    fireEvent.click(screen.getByRole("button", { name: /call a remote expert/i }));

    await waitFor(() => expect(mockGetUserMedia).toHaveBeenCalledTimes(1));
    expect(mockGetUserMedia).toHaveBeenCalledWith({ video: false, audio: true });
  });

  it("renders voice-only note in active voice state", async () => {
    vi.mocked(apiModule.apiRequest).mockResolvedValueOnce({
      callId: "call-uuid-voice-003",
      callMode: "voice",
      createdAt: "2026-10-13T09:20:00Z",
    });

    renderHarness();
    fireEvent.click(screen.getByRole("button", { name: /call a remote expert/i }));

    await waitFor(() => screen.getByText(/No video or gaze data/i));
    expect(screen.getByText(/No video or gaze data/i)).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// End call — POST /ar/expert-calls/{callId}/end with duration
// ---------------------------------------------------------------------------

describe("useExpertCall — End Call flow", () => {
  it("calls POST /ar/expert-calls/{callId}/end when End Call is clicked", async () => {
    mockGetUserMedia.mockResolvedValueOnce({
      getTracks: () => [{ stop: vi.fn() }],
    });
    vi.mocked(apiModule.apiRequest)
      .mockResolvedValueOnce({
        callId: "call-uuid-end-001",
        callMode: "voice",
        createdAt: "2026-10-13T09:00:00Z",
      })
      .mockResolvedValueOnce({
        callId: "call-uuid-end-001",
        auditEntryId: "audit-end-001",
        endedAt: "2026-10-13T09:01:00Z",
      });

    renderHarness({ userId: "assoc@bmw.de" });
    fireEvent.click(screen.getByRole("button", { name: /call a remote expert/i }));

    await waitFor(() => screen.getByText(/Voice-Only Call/i));

    fireEvent.click(screen.getByRole("button", { name: /end (expert )?call/i }));

    await waitFor(() => expect(vi.mocked(apiModule.apiRequest)).toHaveBeenCalledTimes(2));

    const [path, options] = vi.mocked(apiModule.apiRequest).mock.calls[1] as [string, { body: string; method: string }];
    expect(path).toBe("/api/v1/ar/expert-calls/call-uuid-end-001/end");
    expect(options.method).toBe("POST");

    const body = JSON.parse(options.body) as { expertUserId: string; durationSeconds: number };
    expect(body.expertUserId).toBe("assoc@bmw.de");
    expect(typeof body.durationSeconds).toBe("number");
    expect(body.durationSeconds).toBeGreaterThanOrEqual(0);
  });

  it("renders audit entry ID after End Call completes", async () => {
    mockGetUserMedia.mockResolvedValueOnce({
      getTracks: () => [{ stop: vi.fn() }],
    });
    vi.mocked(apiModule.apiRequest)
      .mockResolvedValueOnce({
        callId: "call-uuid-end-002",
        callMode: "voice",
        createdAt: "2026-10-13T09:00:00Z",
      })
      .mockResolvedValueOnce({
        callId: "call-uuid-end-002",
        auditEntryId: "audit-entry-confirm-999",
        endedAt: "2026-10-13T09:01:00Z",
      });

    renderHarness();
    fireEvent.click(screen.getByRole("button", { name: /call a remote expert/i }));
    await waitFor(() => screen.getByText(/Voice-Only Call/i));

    fireEvent.click(screen.getByRole("button", { name: /end (expert )?call/i }));

    await waitFor(() => screen.getByText("audit-entry-confirm-999"));
    expect(screen.getByText("audit-entry-confirm-999")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Button isolation — Call Expert button only in AR Workbench view
// (ExpertCallPanel is only mounted inside ARWorkbenchPage — not Command Center
//  or Handover Builder. These tests confirm the component renders only its
//  own controls and does not bleed into other view rendering paths.)
// ---------------------------------------------------------------------------

describe("useExpertCall — button only in AR Workbench", () => {
  it("ExpertCallPanel renders Call Expert button when rendered", () => {
    renderHarness();
    expect(screen.getByRole("button", { name: /call a remote expert/i })).toBeTruthy();
  });

  it("standalone ExpertCallPanel in idle state renders exactly one button", () => {
    render(
      <I18nextProvider i18n={i18n}>
        <ExpertCallPanel state={{ kind: "idle" }} onCallExpert={vi.fn()} onEndCall={vi.fn()} />
      </I18nextProvider>,
    );
    expect(screen.getAllByRole("button").length).toBe(1);
  });
});
