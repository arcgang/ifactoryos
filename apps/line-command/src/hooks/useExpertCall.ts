import { useCallback, useEffect, useRef, useState } from "react";
import { apiRequest } from "../services/api";

// ---------------------------------------------------------------------------
// Contract types
// ---------------------------------------------------------------------------

export interface ExpertCallResponse {
  callId: string;
  callMode: "video" | "voice";
  createdAt: string;
}

export interface ExpertCallEndResponse {
  callId: string;
  auditEntryId: string;
  endedAt: string;
}

export interface HolographicAnnotation {
  annotationId: string;
  sessionId: string;
  sequenceNumber: number;
  annotationType: "label" | "arrow" | "highlight";
  spatialAnchor: {
    x: number;
    y: number;
    z: number;
    coordinateFrame: string;
  };
  content: {
    labelText: string | null;
    arrowVector: { dx: number; dy: number; dz: number } | null;
    highlightBounds: {
      minX: number;
      minY: number;
      minZ: number;
      maxX: number;
      maxY: number;
      maxZ: number;
    } | null;
  };
  expertUserId: string;
  createdAt: string;
}

// ---------------------------------------------------------------------------
// State machine
// ---------------------------------------------------------------------------

export type ExpertCallState =
  | { kind: "idle" }
  | { kind: "connecting" }
  | {
      kind: "active";
      callId: string;
      callMode: "video" | "voice";
      createdAt: string;
      elapsedSeconds: number;
      annotations: HolographicAnnotation[];
    }
  | { kind: "ended"; callId: string; auditEntryId: string; endedAt: string }
  | { kind: "error"; message: string };

// ---------------------------------------------------------------------------
// UUID helper
// ---------------------------------------------------------------------------

function generateUUID(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes)
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

// ---------------------------------------------------------------------------
// Media helpers — enforce §87 BetrVG constraints at getUserMedia level
// ---------------------------------------------------------------------------

async function openVideoStream(): Promise<MediaStream> {
  // Field-of-view only: no gaze, no biometric data. video:true, audio:true.
  return navigator.mediaDevices.getUserMedia({ video: true, audio: true });
}

async function openVoiceStream(): Promise<MediaStream> {
  // Voice-only: video:false enforced at the media-constraints level per spec.
  return navigator.mediaDevices.getUserMedia({ video: false, audio: true });
}

function closeMediaStream(stream: MediaStream | null): void {
  if (!stream) return;
  stream.getTracks().forEach((t) => t.stop());
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

interface UseExpertCallOptions {
  vin: string;
  stationId: string;
  sessionId: string;
  userId: string;
}

interface UseExpertCallReturn {
  state: ExpertCallState;
  initiateCall: () => void;
  endCall: () => void;
}

export function useExpertCall({
  vin,
  stationId,
  sessionId,
  userId,
}: UseExpertCallOptions): UseExpertCallReturn {
  const [state, setState] = useState<ExpertCallState>({ kind: "idle" });

  // Keep a ref to current state so callbacks that must be stable can read it
  // without going stale across re-renders.
  const stateRef = useRef<ExpertCallState>(state);
  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  // Track call start time for duration computation on end
  const callStartTimeRef = useRef<number | null>(null);

  // Media stream — closed before the end request is sent
  const mediaStreamRef = useRef<MediaStream | null>(null);

  // Timer driving the elapsed-seconds counter in the active state
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // WebRTC peer connection — real signalling handled by ACS SDK on device;
  // this stub wires the media tracks and annotation data channel.
  const peerConnRef = useRef<RTCPeerConnection | null>(null);

  // ACS data channel for receiving holographic annotation payloads
  const dataChannelRef = useRef<RTCDataChannel | null>(null);

  // 9 s SLA warning timeout
  const mediaTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 3 s ended → idle transition timeout
  const endedTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Monotonic sequence counter for incoming annotations
  const annotationSeqRef = useRef<number>(0);

  // Keep userId stable across re-renders inside long-lived callbacks
  const userIdRef = useRef(userId);
  useEffect(() => {
    userIdRef.current = userId;
  }, [userId]);

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const clearMediaTimeout = useCallback(() => {
    if (mediaTimeoutRef.current !== null) {
      clearTimeout(mediaTimeoutRef.current);
      mediaTimeoutRef.current = null;
    }
  }, []);

  const clearEndedTimeout = useCallback(() => {
    if (endedTimeoutRef.current !== null) {
      clearTimeout(endedTimeoutRef.current);
      endedTimeoutRef.current = null;
    }
  }, []);

  const teardownMedia = useCallback(() => {
    clearTimer();
    clearMediaTimeout();
    closeMediaStream(mediaStreamRef.current);
    mediaStreamRef.current = null;

    if (dataChannelRef.current) {
      dataChannelRef.current.close();
      dataChannelRef.current = null;
    }

    if (peerConnRef.current) {
      peerConnRef.current.close();
      peerConnRef.current = null;
    }
  }, [clearTimer, clearMediaTimeout]);

  // Cleanup all timers and media on unmount
  useEffect(() => {
    return () => {
      teardownMedia();
      clearEndedTimeout();
    };
  }, [teardownMedia, clearEndedTimeout]);

  const startElapsedTimer = useCallback((callId: string) => {
    let elapsed = 0;
    timerRef.current = setInterval(() => {
      elapsed += 1;
      setState((prev) => {
        if (prev.kind !== "active" || prev.callId !== callId) return prev;
        return { ...prev, elapsedSeconds: elapsed };
      });
    }, 1000);
  }, []);

  const handleAnnotationMessage = useCallback((event: MessageEvent) => {
    try {
      const annotation = JSON.parse(event.data as string) as HolographicAnnotation;
      // Only accept annotations with a strictly increasing sequence number
      if (annotation.sequenceNumber <= annotationSeqRef.current) return;
      annotationSeqRef.current = annotation.sequenceNumber;
      setState((prev) => {
        if (prev.kind !== "active") return prev;
        return { ...prev, annotations: [...prev.annotations, annotation] };
      });
    } catch {
      // Malformed message — discard silently
    }
  }, []);

  const initiateCall = useCallback(() => {
    const current = stateRef.current;
    if (current.kind === "connecting" || current.kind === "active") return;

    // Fresh idempotency key per tap
    const idempotencyKey = generateUUID();
    annotationSeqRef.current = 0;

    setState({ kind: "connecting" });

    apiRequest<ExpertCallResponse>("/api/v1/ar/expert-calls", {
      method: "POST",
      body: JSON.stringify({ vin, stationId, sessionId, idempotencyKey }),
      userId: userIdRef.current,
    })
      .then(async (response) => {
        callStartTimeRef.current = Date.now();

        // Open media channel — enforce §87 BetrVG constraints at getUserMedia level
        let stream: MediaStream;
        try {
          stream =
            response.callMode === "video"
              ? await openVideoStream()
              : await openVoiceStream();
        } catch (mediaErr) {
          setState({
            kind: "error",
            message:
              mediaErr instanceof Error ? mediaErr.message : "Media device unavailable",
          });
          return;
        }
        mediaStreamRef.current = stream;

        // Wire media tracks and annotation data channel into RTCPeerConnection.
        // RTCPeerConnection may be absent in test/non-WebXR environments — degrade gracefully.
        try {
          const pc = new RTCPeerConnection();
          peerConnRef.current = pc;
          stream.getTracks().forEach((track) => {
            pc.addTrack(track, stream);
          });
          const dc = pc.createDataChannel("annotations", { ordered: true });
          dataChannelRef.current = dc;
          dc.onmessage = handleAnnotationMessage;
        } catch {
          // Non-fatal — annotation channel unavailable without WebRTC
        }

        // Warn at 9 s if media channel is not confirmed (SLA = 10 s from response receipt)
        mediaTimeoutRef.current = setTimeout(() => {
          console.warn(
            `[expert-call] 9s SLA warning — media channel not confirmed for callId=${response.callId}`,
          );
        }, 9_000);

        setState({
          kind: "active",
          callId: response.callId,
          callMode: response.callMode,
          createdAt: response.createdAt,
          elapsedSeconds: 0,
          annotations: [],
        });

        startElapsedTimer(response.callId);
      })
      .catch((err: unknown) => {
        setState({
          kind: "error",
          message: err instanceof Error ? err.message : "Failed to initiate expert call",
        });
      });
  }, [vin, stationId, sessionId, handleAnnotationMessage, startElapsedTimer]);

  const endCall = useCallback(() => {
    const current = stateRef.current;
    if (current.kind !== "active") return;

    const { callId } = current;
    const startTime = callStartTimeRef.current;
    const durationSeconds =
      startTime !== null ? Math.floor((Date.now() - startTime) / 1000) : 0;

    // Close all media tracks before sending the end request — spec requirement
    teardownMedia();

    apiRequest<ExpertCallEndResponse>(`/api/v1/ar/expert-calls/${callId}/end`, {
      method: "POST",
      body: JSON.stringify({
        expertUserId: userIdRef.current,
        durationSeconds,
      }),
      userId: userIdRef.current,
    })
      .then((response) => {
        setState({
          kind: "ended",
          callId: response.callId,
          auditEntryId: response.auditEntryId,
          endedAt: response.endedAt,
        });

        // Return to idle after 3-second confirmation display
        endedTimeoutRef.current = setTimeout(() => {
          setState({ kind: "idle" });
        }, 3_000);
      })
      .catch((err: unknown) => {
        setState({
          kind: "error",
          message: err instanceof Error ? err.message : "Failed to end expert call",
        });
      });
  }, [teardownMedia]);

  return { state, initiateCall, endCall };
}
