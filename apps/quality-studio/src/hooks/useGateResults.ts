import { useCallback, useEffect, useRef, useState } from "react";
import { apiRequest } from "../services/api";
import type { GateResultListResponse, GateResultRecord, GateType } from "../types/gateResults";
import { ALL_GATE_TYPES } from "../types/gateResults";

const POLL_INTERVAL_MS = 10_000;
const DEFAULT_TIMEOUT_SECONDS = 600;

export type GateResultsState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | {
      status: "loaded";
      byType: Partial<Record<GateType, GateResultRecord>>;
      receivedCount: number;
      isComplete: boolean;
      timedOut: boolean;
      timedOutTypes: GateType[];
      elapsedSeconds: number;
      timeoutSeconds: number;
    };

export function useGateResults(vin: string): GateResultsState {
  const [state, setState] = useState<GateResultsState>({ status: "loading" });
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const startTimeRef = useRef<number | null>(null);
  const timeoutSecondsRef = useRef<number>(DEFAULT_TIMEOUT_SECONDS);
  const timedOutRef = useRef(false);

  const clearTimers = useCallback(() => {
    if (timerRef.current !== null) clearInterval(timerRef.current);
    if (pollRef.current !== null) clearInterval(pollRef.current);
    timerRef.current = null;
    pollRef.current = null;
  }, []);

  const fetchResults = useCallback(async (isInitial: boolean) => {
    try {
      const response = await apiRequest<GateResultListResponse>(
        `/api/v1/gate-results?vin=${encodeURIComponent(vin)}&limit=5`,
      );

      const byType: Partial<Record<GateType, GateResultRecord>> = {};
      for (const record of response.data) {
        // Keep most-recently-received result per gate type
        const existing = byType[record.gateType];
        if (
          !existing ||
          record.receivedFromOpcenterAt > existing.receivedFromOpcenterAt
        ) {
          byType[record.gateType] = record;
        }
      }

      const receivedCount = Object.keys(byType).length;
      const isComplete = receivedCount === ALL_GATE_TYPES.length;

      if (isInitial) {
        if (!isComplete) {
          startTimeRef.current = Date.now();
        }
      }

      const elapsedSeconds =
        startTimeRef.current !== null
          ? Math.floor((Date.now() - startTimeRef.current) / 1000)
          : 0;

      const timedOut = timedOutRef.current;
      const timedOutTypes = timedOut
        ? ALL_GATE_TYPES.filter((gt) => !byType[gt])
        : [];

      setState({
        status: "loaded",
        byType,
        receivedCount,
        isComplete,
        timedOut,
        timedOutTypes,
        elapsedSeconds,
        timeoutSeconds: timeoutSecondsRef.current,
      });

      if (isComplete && pollRef.current !== null) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Unknown error";
      setState({ status: "error", message });
    }
  }, [vin]);

  useEffect(() => {
    setState({ status: "loading" });
    startTimeRef.current = null;
    timedOutRef.current = false;

    void fetchResults(true);

    pollRef.current = setInterval(() => {
      void fetchResults(false);
    }, POLL_INTERVAL_MS);

    // Countdown timer — updates elapsed every second
    timerRef.current = setInterval(() => {
      if (startTimeRef.current === null || timedOutRef.current) return;

      const elapsed = Math.floor((Date.now() - startTimeRef.current) / 1000);

      if (elapsed >= timeoutSecondsRef.current) {
        timedOutRef.current = true;
        // Stop polling on timeout
        if (pollRef.current !== null) {
          clearInterval(pollRef.current);
          pollRef.current = null;
        }
        // Force a state update to show timeout alert
        setState((prev) => {
          if (prev.status !== "loaded") return prev;
          return {
            ...prev,
            timedOut: true,
            timedOutTypes: ALL_GATE_TYPES.filter((gt) => !prev.byType[gt]),
            elapsedSeconds: elapsed,
          };
        });
      } else {
        setState((prev) => {
          if (prev.status !== "loaded") return prev;
          return { ...prev, elapsedSeconds: elapsed };
        });
      }
    }, 1000);

    return clearTimers;
  }, [vin, fetchResults, clearTimers]);

  return state;
}
