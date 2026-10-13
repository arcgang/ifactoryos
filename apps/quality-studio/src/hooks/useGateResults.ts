import { useCallback, useEffect, useRef, useState } from "react";
import { listGateResults } from "../services/api";
import type { GateResultRecord, GateType } from "../services/api";

export const ALL_GATE_TYPES: GateType[] = [
  "dimensional",
  "leak",
  "electrical",
  "nvh",
  "adas",
];

export const POLL_INTERVAL_MS = 5_000;
export const TIMEOUT_MS = 10 * 60 * 1000; // 10 minutes

export type GateCommandPhase =
  | "loading"
  | "partial"
  | "populated"
  | "timeout"
  | "error";

export interface GateResultsState {
  phase: GateCommandPhase;
  results: GateResultRecord[];
  receivedGateTypes: Set<GateType>;
  missingGateTypes: GateType[];
  error: string | null;
  elapsedMs: number;
  reload: () => void;
}

function buildGateTypeMap(
  results: GateResultRecord[],
): Map<GateType, GateResultRecord> {
  const map = new Map<GateType, GateResultRecord>();
  for (const r of results) {
    if (!map.has(r.gateType)) {
      map.set(r.gateType, r);
    }
  }
  return map;
}

export function useGateResults(vin: string): GateResultsState {
  const [results, setResults] = useState<GateResultRecord[]>([]);
  const [phase, setPhase] = useState<GateCommandPhase>("loading");
  const [error, setError] = useState<string | null>(null);
  const [elapsedMs, setElapsedMs] = useState(0);

  const startedAtRef = useRef<number>(Date.now());
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const elapsedRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const mountedRef = useRef(true);

  const stopPolling = useCallback(() => {
    if (pollRef.current !== null) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    if (elapsedRef.current !== null) {
      clearInterval(elapsedRef.current);
      elapsedRef.current = null;
    }
  }, []);

  const fetchResults = useCallback(async () => {
    try {
      const resp = await listGateResults({ vin, limit: 200 });
      if (!mountedRef.current) return;

      const allResults = resp.data;
      const byType = buildGateTypeMap(allResults);
      const receivedTypes = new Set(byType.keys());
      const missing = ALL_GATE_TYPES.filter((t) => !receivedTypes.has(t));

      setResults(allResults);
      setError(null);

      const elapsed = Date.now() - startedAtRef.current;

      if (missing.length === 0) {
        setPhase("populated");
        stopPolling();
      } else if (elapsed >= TIMEOUT_MS) {
        setPhase("timeout");
        stopPolling();
      } else {
        setPhase("partial");
      }
    } catch (err) {
      if (!mountedRef.current) return;
      setError(err instanceof Error ? err.message : "Unknown error");
      setPhase("error");
      stopPolling();
    }
  }, [vin, stopPolling]);

  const startPolling = useCallback(() => {
    stopPolling();
    startedAtRef.current = Date.now();
    setElapsedMs(0);
    setPhase("loading");
    setError(null);

    void fetchResults();

    pollRef.current = setInterval(() => {
      const elapsed = Date.now() - startedAtRef.current;
      if (elapsed >= TIMEOUT_MS) {
        setPhase("timeout");
        stopPolling();
        return;
      }
      void fetchResults();
    }, POLL_INTERVAL_MS);

    elapsedRef.current = setInterval(() => {
      setElapsedMs(Date.now() - startedAtRef.current);
    }, 1_000);
  }, [fetchResults, stopPolling]);

  useEffect(() => {
    mountedRef.current = true;
    startPolling();
    return () => {
      mountedRef.current = false;
      stopPolling();
    };
  }, [startPolling, stopPolling]);

  const receivedGateTypes = new Set(
    results.map((r) => r.gateType as GateType),
  );
  const missingGateTypes = ALL_GATE_TYPES.filter(
    (t) => !receivedGateTypes.has(t),
  );

  return {
    phase,
    results,
    receivedGateTypes,
    missingGateTypes,
    error,
    elapsedMs,
    reload: startPolling,
  };
}
