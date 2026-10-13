import { useEffect, useRef, useState } from "react";

// ---------------------------------------------------------------------------
// Event shapes from GET /api/v1/torque/stream — SSE protocol (RFC 8895)
// ---------------------------------------------------------------------------

export type TorqueIndicatorState =
  | { kind: "neutral" }
  | { kind: "in_tolerance"; torqueLogId: string; vin: string; torqueNm: number; anglesDeg: number; recordedAt: string }
  | {
      kind: "out_of_tolerance";
      torqueLogId: string;
      vin: string;
      fastenerPointId: string;
      torqueNm: number;
      specNm: number;
      toleranceNm: number;
      alertId: string;
      recordedAt: string;
    }
  | { kind: "pending_sync"; stationId: string }
  | { kind: "sync_confirmed"; stationId: string };

const API_BASE = import.meta.env.VITE_API_URL ?? "";

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

/**
 * Subscribes to the SSE stream for a station's torque indicator events.
 * The SSE endpoint is GET /api/v1/torque/stream?stationId={stationId}.
 * Events are discriminated by the SSE `event:` field:
 *   - torque_submitted      → in_tolerance indicator
 *   - torque_out_of_tolerance → out_of_tolerance indicator + Shift Lead toast
 *   - pending_sync          → pending-sync indicator
 *   - sync_confirmed        → clears pending-sync indicator
 */
export function useTorqueStream(stationId: string): TorqueIndicatorState {
  const [state, setState] = useState<TorqueIndicatorState>({ kind: "neutral" });
  // Keep stationId fresh in the SSE message handler without reconnecting
  const stationIdRef = useRef(stationId);
  useEffect(() => {
    stationIdRef.current = stationId;
  }, [stationId]);

  useEffect(() => {
    // EventSource is not available in all environments (e.g. jsdom in tests)
    if (typeof EventSource === "undefined") return;

    const token = localStorage.getItem("lc_access_token");
    const url = `${API_BASE}/api/v1/torque/stream?stationId=${encodeURIComponent(stationId)}`;

    // EventSource does not support custom headers natively; the token is passed
    // as a query parameter here for compatibility with the backend's SSE auth
    // middleware. The backend validates the bearer token from ?token=.
    const urlWithAuth = token ? `${url}&token=${encodeURIComponent(token)}` : url;
    const es = new EventSource(urlWithAuth);

    es.addEventListener("torque_submitted", (ev: MessageEvent) => {
      try {
        const data = JSON.parse(ev.data) as {
          torqueLogId: string;
          vin: string;
          torqueNm: number;
          anglesDeg: number;
          recordedAt: string;
        };
        setState({
          kind: "in_tolerance",
          torqueLogId: data.torqueLogId,
          vin: data.vin,
          torqueNm: data.torqueNm,
          anglesDeg: data.anglesDeg,
          recordedAt: data.recordedAt,
        });
      } catch {
        // malformed SSE data — ignore
      }
    });

    es.addEventListener("torque_out_of_tolerance", (ev: MessageEvent) => {
      try {
        const data = JSON.parse(ev.data) as {
          torqueLogId: string;
          vin: string;
          fastenerPointId: string;
          torqueNm: number;
          specNm: number;
          toleranceNm: number;
          alertId: string;
          recordedAt: string;
        };
        setState({
          kind: "out_of_tolerance",
          torqueLogId: data.torqueLogId,
          vin: data.vin,
          fastenerPointId: data.fastenerPointId,
          torqueNm: data.torqueNm,
          specNm: data.specNm,
          toleranceNm: data.toleranceNm,
          alertId: data.alertId,
          recordedAt: data.recordedAt,
        });
      } catch {
        // malformed SSE data — ignore
      }
    });

    es.addEventListener("pending_sync", (ev: MessageEvent) => {
      try {
        const data = JSON.parse(ev.data) as { stationId: string };
        setState({ kind: "pending_sync", stationId: data.stationId });
      } catch {
        setState({ kind: "pending_sync", stationId: stationIdRef.current });
      }
    });

    es.addEventListener("sync_confirmed", (ev: MessageEvent) => {
      try {
        const data = JSON.parse(ev.data) as { stationId: string };
        setState({ kind: "sync_confirmed", stationId: data.stationId });
      } catch {
        setState({ kind: "sync_confirmed", stationId: stationIdRef.current });
      }
    });

    return () => {
      es.close();
    };
  }, [stationId]);

  return state;
}
